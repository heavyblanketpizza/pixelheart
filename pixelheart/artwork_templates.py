"""On-demand reference templates; downloaded artwork never lives in the app."""

from html import escape
from pathlib import Path
import tempfile

from PySide6.QtCore import QStandardPaths, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QVBoxLayout, QWidget, QScrollArea,
)

from pixelheart_core.wiki_artwork import WIKI_TEMPLATES, download_npc_template
from .artwork_browser import SheetBrowser
from .widgets import button, card, label


def template_source_metadata(template):
    """Keep source labels useful for both current and legacy template records."""
    provider = template.get("provider") or "stardew-wiki"
    return {
        "provider": provider,
        "source_name": template.get("source_name") or ("Stardew Valley Wiki" if provider == "stardew-wiki" else provider),
        "attribution": template.get("attribution") or f"Artwork © {template.get('creator') or 'ConcernedApe'}",
    }


def template_cache_directory():
    """Use the OS user cache, including when the app runs from a checkout."""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    return Path(location or tempfile.gettempdir()) / "Pixelheart" / "wiki-templates"


class TemplateDownload(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, template_id, cache_root, parent=None):
        super().__init__(parent)
        self.template_id = template_id
        self.cache_root = cache_root

    def run(self):
        try:
            result = download_npc_template(
                self.template_id, self.cache_root, cancelled=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except Exception as exc:
            # Report worker failures in the dialog; never raise through Qt.
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class ArtworkTemplateDialog(QDialog):
    """Inspect a reference first; accepting is the only project-changing action."""

    def __init__(self, appearance="Default", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vanilla artwork template")
        self.resize(850, 720)
        self.setMinimumWidth(720)
        self.loaded = None
        self.worker = None
        self.closing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(label("Start with a familiar face.", "title", True))
        root.addWidget(label("Explore an NPC's expressions and walking frames, or use their sheets as a starting point for your own artwork.", "muted", True))
        choose = QHBoxLayout()
        choose.addWidget(label("Reference NPC"))
        self.character = QComboBox()
        self.character.setAccessibleName("Vanilla template character")
        for template_id, template in WIKI_TEMPLATES.items():
            self.character.addItem(template["name"], template_id)
        choose.addWidget(self.character, 1)
        self.load_button = button("Load template", self.load_template, "primary")
        choose.addWidget(self.load_button)
        root.addLayout(choose)
        self.source_info = label("", "hint", True)
        self.source_info.setAccessibleName("Selected template source")
        root.addWidget(self.source_info)
        self.status = label("", "muted", True)
        root.addWidget(self.status)

        preview_area = QScrollArea()
        preview_area.setWidgetResizable(True)
        preview_area.setMinimumHeight(260)
        preview_content = QWidget()
        self.preview_row = QHBoxLayout(preview_content)
        self.preview_row.setContentsMargins(0, 0, 0, 0)
        self.browsers = {}
        for kind, title in (("portrait", "Portrait expressions"), ("sprite", "Movement & poses")):
            frame, content = card(title)
            browser = SheetBrowser(kind)
            content.addWidget(browser)
            content.addStretch()
            self.browsers[kind] = browser
            self.preview_row.addWidget(frame, 1)
        preview_area.setWidget(preview_content)
        root.addWidget(preview_area, 1)
        self.credit = label("", "hint", True)
        root.addWidget(self.credit)
        self.sources = label("", "hint", True)
        self.sources.setTextFormat(Qt.TextFormat.RichText)
        self.sources.setOpenExternalLinks(True)
        root.addWidget(self.sources)
        root.addWidget(label(f"Use as starting artwork replaces the portrait and sprite sheets for {appearance}. Your character details stay the same.", "muted", True))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.use_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.use_button.setText("Use as starting artwork")
        self.use_button.setObjectName("primary")
        self.use_button.setEnabled(False)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)
        root.addWidget(self.buttons)
        self.character.currentIndexChanged.connect(self.clear_template)
        self.clear_template()

    def show_source(self, template):
        source = template_source_metadata(template)
        self.source_info.setText("Source: " + source["source_name"])
        self.credit.setText(source["attribution"])
        source_url = template.get("source_url")
        self.sources.setText(
            '<a href="' + escape(source_url, quote=True) + '">View reference on '
            + escape(source["source_name"]) + '</a>' if source_url else ""
        )

    def clear_template(self):
        self.loaded = None
        self.use_button.setEnabled(False)
        self.show_source(WIKI_TEMPLATES.get(self.character.currentData(), {}))
        self.status.setText("Choose Load template to download these sheets. Once cached, the reference is available offline.")
        for browser in self.browsers.values():
            browser.set_image()

    def load_template(self):
        if self.worker is not None:
            return
        self.clear_template()
        self.status.setText("Loading reference sheets…")
        self.load_button.setEnabled(False)
        self.character.setEnabled(False)
        self.worker = TemplateDownload(self.character.currentData(), template_cache_directory(), self)
        self.worker.ready.connect(self.receive_template)
        self.worker.failed.connect(self.show_failure)
        self.worker.finished.connect(self.download_finished)
        self.worker.start()

    def receive_template(self, result):
        if self.closing:
            return
        self.loaded = result
        for kind, browser in self.browsers.items():
            browser.set_image(result[kind])
        self.status.setText("Reference loaded. Choose an expression, play a walk, or turn on Sheet layout to see where each frame goes.")
        self.show_source({**WIKI_TEMPLATES.get(self.character.currentData(), {}), **result})

    def show_failure(self, message):
        self.status.setText("Couldn't load this template. " + message + " You can retry or upload your own PNG sheets.")

    def download_finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self.closing:
            super().reject()
            return
        self.load_button.setEnabled(True)
        self.character.setEnabled(True)
        self.use_button.setEnabled(self.loaded is not None)

    def accept(self):
        if self.loaded is not None and self.worker is None:
            super().accept()

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.worker.requestInterruption()
            self.buttons.setEnabled(False)
            self.status.setText("Stopping download…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
