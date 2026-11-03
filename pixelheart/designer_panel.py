"""A dock beside the workspace where the AI designer talks, and every change shows as a receipt."""
from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QDialog, QDockWidget, QFrame, QHBoxLayout, QPlainTextEdit, QTextBrowser, QVBoxLayout,
                               QWidget)

from pixelheart_designer.config import make_provider
from pixelheart_designer.providers import ProviderError
from pixelheart_designer.session import DesignerSession, PortClosed

from .designer_config import PANEL_VISIBLE_KEY, load_designer_config, save_designer_config
from .game_import import game_import_settings
from .designer_setup import DesignerSetupDialog
from .widgets import button, label


class WindowDocumentPort(QObject):
    """Snapshot and apply on the GUI thread, whichever thread the session runs on."""

    _snapshot_requested = Signal(object)
    _apply_requested = Signal(object, object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.retired = False
        self._snapshot_requested.connect(self._do_snapshot, Qt.ConnectionType.BlockingQueuedConnection)
        self._apply_requested.connect(self._do_apply, Qt.ConnectionType.BlockingQueuedConnection)

    def _on_gui_thread(self):
        return QThread.currentThread() == self.thread()

    def retire(self):
        """Another project is open now; whatever the running turn still wants to do must not land."""
        self.retired = True

    def _check_open(self):
        if self.retired:
            raise PortClosed("The project changed while I was working, so this change wasn't applied.")

    def snapshot(self):
        self._check_open()
        if self._on_gui_thread():
            return self.window.project_snapshot()
        holder = {}
        self._snapshot_requested.emit(holder)
        return holder["document"]

    def apply(self, document, receipt):
        self._check_open()
        if self._on_gui_thread():
            self.window.apply_designer_change(document, receipt)
        else:
            self._apply_requested.emit(document, receipt)

    @Slot(object)
    def _do_snapshot(self, holder):
        holder["document"] = self.window.project_snapshot()

    @Slot(object, object)
    def _do_apply(self, document, receipt):
        self.window.apply_designer_change(document, receipt)


class DesignerTurn(QThread):
    """One conversation turn off the GUI thread; events come back as signals."""

    assistant_delta = Signal(str)
    assistant_text = Signal(str)
    receipt = Signal(object)
    notice = Signal(str)
    failed = Signal(str)

    def __init__(self, session, text, parent=None):
        super().__init__(parent)
        self.session = session
        self.text = text

    def run(self):
        self.session.run_turn(self.text, self._relay)

    def _relay(self, kind, payload):
        if kind == "assistant_delta":
            self.assistant_delta.emit(payload)
        elif kind == "assistant":
            self.assistant_text.emit(payload)
        elif kind == "receipt":
            self.receipt.emit(payload)
        elif kind == "notice":
            self.notice.emit(payload)
        elif kind == "error":
            self.failed.emit(payload)


_STYLES = {
    "user": '<p><b>You</b><br>{}</p>',
    "assistant": '<p>{}</p>',
    "notice": '<p style="color:#7a7a7a"><i>{}</i></p>',
    "error": '<p style="color:#a03c2e">{}</p>',
}


class DesignerPanel(QDockWidget):
    def __init__(self, window, *, inline: bool = False):
        super().__init__("AI designer", window)
        self.window = window
        self.inline = inline
        self.session = None
        self.turn = None
        self.entries = []
        self._preview = None   # [start position, fragments] while the designer's words are still arriving
        self._was_visible = False
        self._welcome_hidden = False
        self._closing = False
        self.setObjectName("designerPanel")
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.setMinimumWidth(320)

        body = QWidget()
        # Typing here is a message to the designer, not an edit: keep the project's Edit → Undo out of it.
        body.setProperty("projectHistoryTransient", True)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.setup_card = QFrame()
        self.setup_card.setObjectName("card")
        card = QVBoxLayout(self.setup_card)
        card.setContentsMargins(18, 16, 18, 18)
        card.setSpacing(10)
        card.addWidget(label("Design with AI", "sectionTitle"))
        card.addWidget(label("Talk through who your character is, and the designer fills in their words, routine, gifts and "
                             "heart events as ordinary edits you can undo. Set it up with an Anthropic key or a local model "
                             "server running llama.cpp or MLX.", "muted", True))
        self.setup_button = button("Set up AI designer…", self.open_setup, "primary")
        card.addWidget(self.setup_button)
        layout.addWidget(self.setup_card)

        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setAccessibleName("AI designer conversation")
        self.transcript.anchorClicked.connect(self._anchor_clicked)
        layout.addWidget(self.transcript, 1)

        self.status = label("", "muted")
        layout.addWidget(self.status)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Describe the character you dream of… (Enter sends, Shift+Enter for a new line)")
        self.input.setAccessibleName("Message to the AI designer")
        self.input.setFixedHeight(84)
        self.input.setProperty("projectHistoryTransient", True)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        actions = QHBoxLayout()
        self.stop_button = button("Stop", self.stop, "quiet")
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        self.new_button = button("New conversation", self.new_conversation, "quiet")
        actions.addWidget(self.new_button)
        actions.addStretch()
        self.send_button = button("Send", self.send, "primary")
        actions.addWidget(self.send_button)
        layout.addLayout(actions)
        self.setWidget(body)
        self.refresh_from_settings()

    # --- provider and session ---

    def refresh_from_settings(self):
        try:
            provider = make_provider(load_designer_config())
        except ProviderError as exc:
            self.session = None
            self.setup_card.show()
            self.status.setText("" if exc.kind == "not_configured" else exc.message)
            self._set_chat_enabled(False)
            return
        self.set_provider(provider)

    def set_provider(self, provider):
        self.session = DesignerSession(provider, WindowDocumentPort(self.window))
        self.setup_card.hide()
        self._set_chat_enabled(True)
        self.status.setText(provider.label)
        self._append("notice", f"Now chatting with {provider.label}")

    def _set_chat_enabled(self, enabled):
        self.input.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.new_button.setEnabled(enabled)

    def open_setup(self):
        dialog = DesignerSetupDialog(self.window, load_designer_config())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_designer_config(dialog.values())
            self.refresh_from_settings()

    # --- turns ---

    def send(self, text=None):
        if self.session is None or self.turn is not None:
            return
        text = (text if isinstance(text, str) else self.input.toPlainText()).strip()
        if not text:
            return
        self.input.clear()
        self._append("user", text)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("Writing…")
        if self.inline:
            try:
                self.session.run_turn(text, self._event)
            finally:
                self._finished()
            return
        turn = DesignerTurn(self.session, text, self)
        turn.retired = False
        turn.assistant_delta.connect(lambda text, turn=turn: self._turn_event(turn, "assistant_delta", text))
        turn.assistant_text.connect(lambda text, turn=turn: self._turn_event(turn, "assistant", text))
        turn.receipt.connect(lambda receipt, turn=turn: self._turn_event(turn, "receipt", receipt))
        turn.notice.connect(lambda text, turn=turn: self._turn_event(turn, "notice", text))
        turn.failed.connect(lambda text, turn=turn: self._turn_event(turn, "error", text))
        turn.finished.connect(self._finished)
        self.turn = turn
        turn.start()

    def _turn_event(self, turn, kind, payload):
        if not getattr(turn, "retired", False):
            self._event(kind, payload)

    def _event(self, kind, payload):
        if kind == "assistant_delta":
            self._preview_grow(payload)
            return
        if self._preview is not None:
            # The reply settled, or the turn ended another way: the live paragraph gives way to the kept text.
            self._preview_settle(keep=kind != "assistant")
        if kind == "receipt":
            self._append_receipt(payload)
        elif kind in _STYLES:
            self._append(kind, payload)

    def _finished(self):
        if self.turn is not None:
            self.turn.deleteLater()
            self.turn = None
        if self._preview is not None:
            self._preview_settle(keep=True)
        self._set_chat_enabled(self.session is not None)
        self.stop_button.setEnabled(False)
        self.status.setText(self.session.provider.label if self.session is not None else "")

    def stop(self):
        if self.session is not None:
            self.session.cancel()
        if self.turn is not None:
            self.status.setText("Stopping…")
        self.stop_button.setEnabled(False)

    def new_conversation(self):
        """Forget the conversation so far and start afresh with the same provider; a running turn is stopped first."""
        if self.session is None:
            return
        if self.turn is not None:
            self.turn.retired = True
            self.session.cancel()
            self.session.port.retire()
            self.stop_button.setEnabled(False)
        self.transcript.clear()
        self.entries = []
        self._preview = None
        self.set_provider(self.session.provider)
        if self.turn is not None:
            self._set_chat_enabled(False)

    def reset_for_project(self):
        """A different character is open now: retire a running turn and start the conversation afresh."""
        if self.session is None or (self.turn is None and len(self.entries) <= 1):
            return
        self.new_conversation()

    def finish_then(self, callback) -> bool:
        """Stop the running turn and call back once its worker has ended. False when nothing is running."""
        if self.turn is None:
            return False
        self.stop()
        if not self._closing:
            self._closing = True
            self.turn.finished.connect(callback)
        return True

    def eventFilter(self, watched, event):
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send()
            return True
        return super().eventFilter(watched, event)

    # --- transcript ---

    def _append(self, kind, text):
        self.entries.append((kind, text))
        self.transcript.append(_STYLES[kind].format(html.escape(text).replace("\n", "<br>")))

    def _preview_grow(self, fragment):
        """Show the designer's words as they arrive, in a paragraph that only exists until the reply settles."""
        if self._preview is None:
            if not fragment.strip():
                return
            self.transcript.append(_STYLES["assistant"].format(html.escape(fragment).replace("\n", "<br>")))
            self._preview = [self.transcript.document().lastBlock().position(), [fragment]]
        else:
            self._preview[1].append(fragment)
            cursor = QTextCursor(self.transcript.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(fragment)
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _preview_settle(self, keep):
        """Take the live paragraph down; with ``keep`` its words stay as an ordinary entry."""
        start, fragments = self._preview
        self._preview = None
        cursor = QTextCursor(self.transcript.document())
        cursor.setPosition(max(start - 1, 0))   # from the separator before the paragraph, so none is left behind
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        if keep:
            self._append("assistant", "".join(fragments))

    def _append_receipt(self, receipt):
        self.entries.append(("receipt", f"{receipt['title']} · {receipt['detail']}"))
        self.transcript.append(f'<p style="color:#6b6b6b"><a href="section:{html.escape(receipt["section"])}">'
                               f'{html.escape(receipt["title"])}</a> · {html.escape(receipt["detail"])}</p>')

    def _anchor_clicked(self, url: QUrl):
        if url.scheme() == "section":
            self.window.open_section(url.path())

    # --- remembered visibility ---

    @staticmethod
    def remembered_visible() -> bool:
        value = game_import_settings().value(PANEL_VISIBLE_KEY, False)
        return str(value).lower() in ("true", "1")

    def remember_visible(self, visible):
        """Called on the user's own choices: the View menu, the Overview button, the dock's close button."""
        if not self._welcome_hidden:
            settings = game_import_settings()
            settings.setValue(PANEL_VISIBLE_KEY, bool(visible))
            settings.sync()

    def closeEvent(self, event):
        self.remember_visible(False)
        super().closeEvent(event)

    # --- welcome screen ---

    def set_workspace_visible(self, visible):
        if not visible:
            if not self._welcome_hidden:
                self._was_visible = not self.isHidden()
                self._welcome_hidden = True
                self.hide()
        elif self._welcome_hidden:
            self._welcome_hidden = False
            if self._was_visible:
                self.show()
