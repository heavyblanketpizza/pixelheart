"""Route the main workspace's editing controls through one project history."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPlainTextEdit, QTextEdit, QWidget
from shiboken6 import isValid

from pixelheart_core.project_history import ProjectHistory
from .location_picker import MapSelector


_AUTO_MERGE = object()
_TEXT_EDITORS = (QLineEdit, QPlainTextEdit, QTextEdit)


class ProjectHistoryController(QObject):
    """Own project actions while leaving independent modal draft editors alone.

    The window supplies project_snapshot(), restore_project_snapshot(snapshot),
    and project_history_context(). Live project dialogs may opt in with the
    ``projectHistoryLive`` property; Home settings are recognized directly.
    """

    TYPING_GROUP_SECONDS = 1.0

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.history = ProjectHistory({})
        self.undo_action = self.redo_action = None
        self._ready = self._restoring = False
        self._typing_editor = None
        self._gesture_key = None
        self._last_recorded_at = None
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(self._finish_typing_event)
        application = QApplication.instance()
        application.installEventFilter(self)
        application.focusChanged.connect(self._focus_changed)

    def install_actions(self, edit_menu):
        for name, standard, callback in (
            ("undo", QKeySequence.StandardKey.Undo, self.undo),
            ("redo", QKeySequence.StandardKey.Redo, self.redo),
        ):
            action = QAction("&" + name.title(), self.window)
            action.setObjectName("project" + name.title())
            action.setShortcuts(QKeySequence.keyBindings(standard))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(callback)
            edit_menu.addAction(action)
            setattr(self, name + "_action", action)
        self.sync()

    def reset(self, snapshot):
        self.history.reset(snapshot)
        self._ready = True
        self.close_group()
        self.sync()

    def record(self, snapshot, merge_key=_AUTO_MERGE):
        if self._restoring or getattr(self.window, "loading", False):
            return False
        if not self._ready:
            self.reset(snapshot)
            return False
        if merge_key is _AUTO_MERGE:
            merge_key = self._typing_merge_key()
        now = time.monotonic()
        if (self._gesture_key is None and self._last_recorded_at is not None
                and now - self._last_recorded_at > self.TYPING_GROUP_SECONDS):
            self.history.close_group()
        changed = self.history.record(snapshot, merge_key=merge_key)
        if changed:
            self._last_recorded_at = now if merge_key is not None else None
        self.sync()
        return changed

    def record_current(self, merge_key=_AUTO_MERGE):
        return self.record(self.window.project_snapshot(), merge_key=merge_key)

    def close_group(self, *_):
        self.history.close_group()
        self._gesture_key = None
        self._last_recorded_at = None
        self._typing_timer.stop()
        self._finish_typing_event()

    def begin_gesture(self, key):
        """Group live updates until a pointer gesture finishes or loses focus."""
        self.close_group()
        self._gesture_key = ("gesture", key)

    def end_gesture(self, *_):
        self.close_group()

    def _restore(self, direction):
        self.close_group()
        if self._restoring or getattr(self.window, "loading", False):
            return False
        snapshot = getattr(self.history, direction)()
        if snapshot is None:
            self.sync()
            return False
        self._restoring = True
        reverse = "redo" if direction == "undo" else "undo"
        try:
            restored = self.window.restore_project_snapshot(snapshot)
            if restored is False:
                getattr(self.history, reverse)()
                return False
        except Exception:
            getattr(self.history, reverse)()
            raise
        finally:
            self._restoring = False
            self.sync()
        return True

    def undo(self):
        return self._restore("undo")

    def redo(self):
        return self._restore("redo")

    def sync(self):
        if self.undo_action is not None:
            self.undo_action.setEnabled(self._ready and self.history.can_undo)
            self.redo_action.setEnabled(self._ready and self.history.can_redo)
        self._hide_local_controls()
        if self._ready:
            self.window.dirty = self.history.is_dirty or getattr(self.window, "_external_dirty", False)
            self.window.update_title()

    def _hide_local_controls(self):
        window = self.window
        owners = [(getattr(window, "gifts", None), "undo_preset_button"),
                  (getattr(window, "world", None), "undo_remove_button")]
        events = getattr(window, "events", None)
        owners.extend((owner, "undo_button") for owner in (
            events, getattr(window, "relationships", None),
            getattr(events, "actors", None), getattr(events, "beats", None)))
        life = getattr(window, "life", None)
        owners.extend((editor, "undo") for editor in getattr(life, "editors", {}).values())
        for owner, name in owners:
            button = getattr(owner, name, None)
            if isinstance(button, QWidget) and isValid(button):
                button.hide()

    def _focus_changed(self, before, after):
        if self._belongs_to_project(before) or self._belongs_to_project(after):
            self.close_group()

    def _belongs_to_project(self, widget):
        if not isValid(self.window) or not isinstance(widget, QWidget) or not isValid(widget):
            return False
        # Several editor pages keep their owner in an attribute named window.
        top = QWidget.window(widget)
        if top is self.window:
            return True
        owner = top.parentWidget()
        while owner is not None and owner is not self.window:
            owner = owner.parentWidget()
        if owner is not self.window:
            return False
        if isinstance(top, QDialog):
            settings = getattr(getattr(self.window, "world", None), "settings_dialog", None)
            return top is settings or bool(top.property("projectHistoryLive"))
        return False

    def _text_editor(self, widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, _TEXT_EDITORS):
                return widget
            if widget is self.window or isinstance(widget, QDialog):
                break
            widget = widget.parentWidget()
        return None

    def _transient_text(self, editor):
        ancestor = editor
        while isinstance(ancestor, QWidget):
            if ancestor.property("projectHistoryTransient"):
                return True
            if getattr(ancestor, "search", None) is editor:
                return True
            if isinstance(ancestor, MapSelector) and ancestor.combo.lineEdit() is editor:
                return True
            if ancestor is self.window:
                break
            ancestor = ancestor.parentWidget()
        return False

    def _routes_undo(self, widget):
        if not self._belongs_to_project(widget):
            return False
        editor = self._text_editor(widget)
        return editor is None or not self._transient_text(editor)

    def _finish_typing_event(self):
        self._typing_editor = None

    def _begin_typing_event(self, editor):
        self._typing_editor = editor
        self._typing_timer.start(0)

    def _typing_merge_key(self):
        if self._gesture_key is not None:
            return self._gesture_key
        editor = self._typing_editor
        if editor is None or not isValid(editor) or not self._routes_undo(editor):
            return None
        if self._text_editor(QApplication.focusWidget()) is not editor:
            return None
        context = self.window.project_history_context()
        return ("typing", id(editor), context)

    @staticmethod
    def _undo_direction(event):
        if event.matches(QKeySequence.StandardKey.Undo):
            return "undo"
        if event.matches(QKeySequence.StandardKey.Redo):
            return "redo"
        return None

    def _text_context_menu(self, editor, event):
        menu = editor.createStandardContextMenu()
        # Editable Qt text controls put Undo and Redo first in their standard
        # menu. Keep their other native editing and selection actions intact.
        for native, direction, standard in zip(menu.actions()[:2], ("undo", "redo"),
                                                (QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo)):
            shortcut = QKeySequence(standard).toString(QKeySequence.SequenceFormat.NativeText)
            action = QAction(direction.title() + "\t" + shortcut, menu)
            action.setEnabled(getattr(self.history, "can_" + direction))
            action.triggered.connect(getattr(self, direction))
            menu.insertAction(native, action)
            menu.removeAction(native)
        try:
            menu.exec(event.globalPos())
        finally:
            if isValid(menu):
                menu.deleteLater()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.ShortcutOverride,
                        QEvent.Type.KeyPress, QEvent.Type.InputMethod, QEvent.Type.ContextMenu):
            return super().eventFilter(watched, event)
        if not isinstance(watched, QWidget) or not self._belongs_to_project(watched):
            return super().eventFilter(watched, event)
        if kind == QEvent.Type.MouseButtonPress:
            self.close_group()
        if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            direction = self._undo_direction(event)
            if direction and self._routes_undo(watched):
                if kind == QEvent.Type.KeyPress:
                    getattr(self, direction)()
                event.accept()
                return True
            if kind == QEvent.Type.KeyPress:
                editor = self._text_editor(watched)
                typing = (editor is not None and not editor.isReadOnly() and not self._transient_text(editor)
                          and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
                          and (bool(event.text()) or event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete))
                          and event.key() not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Return, Qt.Key.Key_Enter))
                if typing:
                    self._begin_typing_event(editor)
                else:
                    self.close_group()
        elif kind == QEvent.Type.InputMethod:
            editor = self._text_editor(watched)
            if editor is not None and not editor.isReadOnly() and not self._transient_text(editor):
                self._begin_typing_event(editor)
        elif kind == QEvent.Type.ContextMenu:
            editor = self._text_editor(watched)
            if editor is not None and not editor.isReadOnly() and not self._transient_text(editor):
                self.close_group()
                self._text_context_menu(editor, event)
                event.accept()
                return True
        return super().eventFilter(watched, event)
