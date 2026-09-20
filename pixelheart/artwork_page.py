"""Upload, compare and select local original/prepared PNG sheets."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile

from PySide6.QtCore import Signal, QSaveFile, QIODevice, QStandardPaths
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QDialogButtonBox,
    QFileDialog, QTabBar, QMenu, QLabel,
)

from pixelheart_core.artwork import inspect_artwork, prepare_artwork, ArtworkValidationError
from pixelheart_core.projects import import_artwork, resolve_artwork, ProjectError, APPEARANCE_VARIANTS
from .artwork_browser import SheetBrowser
from .artwork_templates import ArtworkTemplateDialog, template_source_metadata
from .artwork_review import ArtworkReviewDialog
from pixelheart_core.artwork_review import load_review_sheet
from .widgets import label, button, card, ArtworkPreview


class PreparationDialog(QDialog):
    def __init__(self, source, kind, romanceable, parent=None):
        super().__init__(parent)
        inspect_artwork(source)
        self.source, self.kind, self.romanceable = source, kind, romanceable
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-preview-")
        self.prepared = Path(self.temporary.name) / "prepared.png"
        self.setWindowTitle("Prepare " + kind + " artwork")
        self.resize(850, 650)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(16)
        root.addWidget(label("Make room for every pixel.", "title"))
        root.addWidget(label("Your original stays untouched. Review the prepared copy before choosing it for export.", "muted", True))
        previews = QHBoxLayout()
        for title, path in [("ORIGINAL UPLOAD", source), ("PREPARED COPY", None)]:
            frame, content = card()
            content.addWidget(label(title, "eyebrow"))
            preview = ArtworkPreview("Choose compatible artwork")
            preview.setMinimumHeight(270)
            preview.set_image(path)
            content.addWidget(preview, 1)
            previews.addWidget(frame)
            if path is None:
                self.output_preview = preview
        root.addLayout(previews, 1)
        row = QHBoxLayout()
        row.addWidget(label("Pixelation"))
        self.pixelation = QComboBox()
        for text, factor in [("Off · preserve pixel edges", 1), ("2 px grid", 2), ("4 px grid", 4), ("8 px grid", 8)]:
            self.pixelation.addItem(text, factor)
        row.addWidget(self.pixelation)
        row.addStretch()
        root.addLayout(row)
        self.result = label("", "muted", True)
        root.addWidget(self.result)
        root.addWidget(label("Sheets resize proportionally to game dimensions. No expressions, poses, or animation frames are created.", "hint", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.accept_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.accept_button.setText("Use prepared copy")
        self.accept_button.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.pixelation.currentIndexChanged.connect(self.update_preparation)
        self.update_preparation()

    def update_preparation(self):
        try:
            prepare_artwork(self.source, self.prepared, self.kind, pixelate=self.pixelation.currentData(), romanceable=self.romanceable)
            info = inspect_artwork(self.prepared)
            self.output_preview.set_image(self.prepared)
            self.result.setText(f"Prepared sheet: {info['width']} × {info['height']} px. Check every frame before exporting.")
            self.accept_button.setEnabled(True)
        except ArtworkValidationError as exc:
            self.result.setText(str(exc))
            self.output_preview.set_image()
            self.accept_button.setEnabled(False)


class ArtworkPage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.cards = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)
        start = QHBoxLayout()
        start.addWidget(label("Bring your own sheets, or start with a familiar NPC.", "muted", True), 1)
        self.template_button = button("From my game…", self.choose_template)
        start.addWidget(self.template_button)
        root.addLayout(start)
        review_row = QHBoxLayout()
        review_row.addWidget(label("Inspect every frame, compare a reference, and save an offline review.", "hint", True), 1)
        self.review_button = button("Detailed review…", lambda: self.open_review(), "primary")
        self.review_button.setAccessibleName("Open detailed artwork review")
        review_row.addWidget(self.review_button)
        root.addLayout(review_row)
        appearances = QWidget()
        appearance_layout = QVBoxLayout(appearances)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(8)
        self.appearance_tabs = QTabBar()
        self.appearance_tabs.setAccessibleName("Character appearance")
        self.appearance_tabs.setExpanding(False)
        self.appearance_tabs.addTab("Default")
        self.appearance_tabs.setTabData(0, None)
        for variant, name in APPEARANCE_VARIANTS.items():
            index = self.appearance_tabs.addTab(name)
            self.appearance_tabs.setTabData(index, variant)
        self.appearance_tabs.currentChanged.connect(self.refresh)
        appearance_layout.addWidget(self.appearance_tabs)
        self.appearance_info = label("", "muted", True)
        appearance_layout.addWidget(self.appearance_info)
        root.addWidget(appearances)
        row = QHBoxLayout()
        row.setSpacing(20)
        for kind, title, description in [
            ("portrait", "Every expression", "2 columns of 64 × 64 portraits. One PNG, 128 × 192 px or taller, with at least 6 expressions."),
            ("sprite", "A life in motion", "4 columns of 16 × 32 frames. One PNG, at least 64 × 128 px; 64 × 416 for romance."),
        ]:
            frame, content = card(title, description)
            for caption in frame.findChildren(QLabel):
                if caption.objectName() == "muted":
                    caption.setMinimumHeight(34)
            content.addWidget(button("Upload " + kind + " sheet", lambda checked=False, k=kind: self.upload(k), "primary"))
            preview = SheetBrowser(kind)
            content.addWidget(preview)
            info = label("", "muted", True)
            content.addWidget(info)
            select = QComboBox()
            select.setAccessibleName(kind.title() + " export source")
            select.currentIndexChanged.connect(lambda index, k=kind: self.select_source(k))
            content.addWidget(select)
            options = button("Sheet options", lambda: None, "quiet")
            menu = QMenu(options)
            review = menu.addAction("Detailed review…")
            review.triggered.connect(lambda checked=False, k=kind: self.open_review(k))
            prepare = menu.addAction("Prepare & compare…")
            prepare.triggered.connect(lambda checked=False, k=kind: self.prepare(k))
            export = menu.addAction("Save PNG copy for editing…")
            export.triggered.connect(lambda checked=False, k=kind: self.save_png_copy(k))
            menu.addSeparator()
            remove = menu.addAction("Remove sheet")
            remove.triggered.connect(lambda checked=False, k=kind: self.remove(k))
            options.setMenu(menu)
            options.setAccessibleName(kind.title() + " sheet options")
            content.addWidget(options)
            content.addStretch()
            self.cards[kind] = {"preview": preview, "info": info, "select": select, "prepare": prepare, "remove": remove, "options": options, "export": export}
            row.addWidget(frame, 1)
        root.addLayout(row)
        root.addWidget(label("Import sheets exported by Content Patcher from your game. Exports can include active mods; use a clean profile for vanilla artwork. Check the creators' permissions before sharing.", "hint", True))
        root.addStretch()

    @property
    def variant(self):
        return self.appearance_tabs.tabData(self.appearance_tabs.currentIndex())

    def artwork_set(self, create=False):
        artwork = self.window.document["artwork"]
        if self.variant is None:
            return artwork
        if create:
            return artwork.setdefault("variants", {}).setdefault(self.variant, {})
        return artwork.get("variants", {}).get(self.variant, {})

    def select_appearance(self, variant=None):
        for index in range(self.appearance_tabs.count()):
            if self.appearance_tabs.tabData(index) == variant:
                self.appearance_tabs.setCurrentIndex(index)
                return

    def refresh(self, *_):
        if self.variant is None:
            description = "Default artwork is required. Optional seasonal appearances use it wherever you haven't supplied a separate sheet."
        elif self.variant == "beach":
            description = "Optional island attire. Unassigned sheets use Default. Island visits need separate setup before this appearance can be used in-game."
        else:
            description = f"Optional {self.variant} appearance. Upload either or both sheets; unassigned artwork uses Default."
        self.appearance_info.setText(description)
        for kind, widgets in self.cards.items():
            record = self.artwork_set().get(kind)
            select = widgets["select"]
            select.blockSignals(True)
            select.clear()
            if record:
                select.addItem("Export original upload", "original")
                if isinstance(record, dict) and record.get("prepared"):
                    select.addItem("Export prepared copy", "prepared")
                    select.setCurrentIndex(select.findData(record.get("selected", "original")))
            else:
                select.addItem("Use default " + kind + " sheet" if self.variant else "No sheet uploaded", None)
            select.setEnabled(bool(record))
            select.setVisible(select.count() > 1)
            select.blockSignals(False)
            widgets["prepare"].setEnabled(bool(record))
            widgets["remove"].setEnabled(bool(record))
            widgets["prepare"].setVisible(bool(record))
            widgets["remove"].setVisible(bool(record))
            widgets["remove"].setText("Use default" if self.variant else "Remove sheet")
            widgets["export"].setEnabled(False)
            widgets["options"].setVisible(bool(record))
            try:
                inherited = bool(self.variant and not record)
                path = resolve_artwork(self.window.document, self.window.project_file, kind, variant=None if inherited else self.variant) if self.window.project_file else None
                if path:
                    info = inspect_artwork(path)
                    widgets["preview"].set_image(path)
                    origin = "Using Default · " if inherited else ""
                    widgets["info"].setText(f"{origin}{info['width']} × {info['height']} px  ·  {info['size'] / 1024:.1f} KB")
                    effective_record = self.window.document["artwork"].get(kind) if inherited else record
                    source_metadata = effective_record.get("source") if isinstance(effective_record, dict) else None
                    if isinstance(source_metadata, dict) and source_metadata.get("provider"):
                        source = template_source_metadata(source_metadata)
                        widgets["info"].setText(widgets["info"].text() + f"\nTemplate · {source['attribution']}")
                    elif isinstance(effective_record, dict) and effective_record.get("source_history"):
                        widgets["info"].setText(widgets["info"].text() + "\nPrevious sheet's source retained in project history.")
                    widgets["info"].setToolTip(str(path))
                    widgets["info"].show()
                    widgets["export"].setEnabled(True)
                    widgets["options"].show()
                else:
                    widgets["preview"].set_image()
                    widgets["info"].setText("Default sheet hasn't been uploaded yet." if inherited else "Upload a complete sheet to see all its frames.")
                    widgets["info"].setToolTip("")
                    widgets["info"].setVisible(inherited)
            except (ProjectError, ArtworkValidationError) as exc:
                widgets["preview"].set_image()
                widgets["info"].setText(str(exc))
                widgets["info"].show()

    def create_review(self, kind=None):
        """Resolve a copy: even legacy provenance normalization stays read-only."""
        document = deepcopy(self.window.document)
        sheets, originals, notes = {}, {}, []
        name = document["character"].get("name") or "Your character"
        appearance = APPEARANCE_VARIANTS.get(self.variant, "Default")
        for asset_kind in ("portrait", "sprite"):
            try:
                artwork = document["artwork"]
                override = artwork.get("variants", {}).get(self.variant, {}).get(asset_kind) if self.variant else artwork.get(asset_kind)
                inherited = bool(self.variant and not override)
                variant = None if inherited else self.variant
                path = resolve_artwork(document, self.window.project_file, asset_kind, variant=variant) if self.window.project_file else None
                if not path:
                    continue
                caption = name + (" · Default" if inherited else " · " + appearance)
                sheets[asset_kind] = load_review_sheet(path, asset_kind, caption)
                if inherited:
                    notes.append(f"{asset_kind.title()} uses Default because {appearance} has no override.")
                original_document = deepcopy(document)
                records = original_document["artwork"] if variant is None else original_document["artwork"]["variants"][variant]
                record = records.get(asset_kind)
                if isinstance(record, dict):
                    record["selected"] = "original"
                original = resolve_artwork(original_document, self.window.project_file, asset_kind, variant=variant)
                if original:
                    originals[asset_kind] = original
            except (ProjectError, ArtworkValidationError, OSError, ValueError) as exc:
                notes.append(f"{asset_kind.title()}: {exc}")
        dialog = ArtworkReviewDialog(name, sheets, appearance=appearance, originals=originals, notes=notes, parent=self)
        active = kind or ("sprite" if "sprite" in sheets else "portrait")
        dialog.set_kind(active)
        dialog.select_frame(self.cards[active]["preview"].current_frame)
        return dialog

    def open_review(self, kind=None):
        dialog = self.create_review(kind)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def choose_template(self):
        appearance = APPEARANCE_VARIANTS.get(self.variant, "Default")
        dialog = ArtworkTemplateDialog(appearance, self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.apply_template(dialog.loaded)
        finally:
            dialog.deleteLater()

    def apply_template(self, template):
        """Import both validated sheets before changing either selected record."""
        if not template:
            return False
        try:
            for kind in ("portrait", "sprite"):
                inspect_artwork(template[kind])
            if not self.window.ensure_saved():
                return False
            records = {}
            for kind in ("portrait", "sprite"):
                relative = import_artwork(template[kind], self.window.project_file, kind)
                source = {
                    **template_source_metadata(template), "template": template["id"],
                    "url": template.get("source_url", ""),
                    **deepcopy(template.get("source", {})),
                    **deepcopy(template.get(kind + "_source", {})),
                }
                expected_hash = source.get("sha256")
                if expected_hash:
                    copied = self.window.project_file.parent / relative
                    if hashlib.sha256(copied.read_bytes()).hexdigest() != expected_hash:
                        raise ProjectError("The exported artwork changed after preview. Load the template again before using it.")
                records[kind] = {
                    "original": relative, "prepared": None, "selected": "original",
                    "source": source,
                }
            self.artwork_set(create=True).update(records)
            self.changed.emit()
            self.refresh()
            self.window.statusBar().showMessage("Template loaded. Use Sheet options → Save PNG copy for editing, then upload your edited sheets.", 15000)
            return True
        except (ArtworkValidationError, ProjectError, OSError) as exc:
            self.window.show_error("Could not use template", str(exc))
            return False

    def save_png_copy(self, kind):
        try:
            if not self.window.project_file:
                return
            variant = self.variant if self.artwork_set().get(kind) else None
            source = resolve_artwork(self.window.document, self.window.project_file, kind, variant=variant)
            if not source:
                return
            inspect_artwork(source)
            directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
            destination, _ = QFileDialog.getSaveFileName(self, "Save a separate PNG to edit", str(Path(directory) / f"{kind}.png"), "PNG artwork (*.png)")
            if not destination:
                return
            if not destination.lower().endswith(".png"):
                destination += ".png"
            artwork = self.window.document["artwork"]
            protected = set()
            for appearance in (artwork, *artwork.get("variants", {}).values()):
                for asset_kind in ("portrait", "sprite"):
                    record = appearance.get(asset_kind)
                    references = [record] if isinstance(record, str) else [record.get(key) for key in ("original", "prepared")] if record else []
                    protected.update((self.window.project_file.parent / reference).resolve() for reference in references if reference)
            if Path(destination).resolve() in protected:
                raise ProjectError("Choose a separate file so your imported original stays unchanged.")
            output = QSaveFile(destination)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            payload = source.read_bytes()
            if output.write(payload) != len(payload) or not output.commit():
                raise OSError(output.errorString())
            self.window.statusBar().showMessage("PNG copy saved. Edit it in your pixel editor, then upload the edited sheet here.", 12000)
        except (ArtworkValidationError, ProjectError, OSError) as exc:
            self.window.show_error("Could not save PNG copy", str(exc))

    def upload(self, kind):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a complete " + kind + " sheet", "", "PNG artwork (*.png)")
        if not path:
            return
        try:
            inspect_artwork(path)
            if not self.window.ensure_saved():
                return
            previous = self.artwork_set().get(kind)
            relative = import_artwork(path, self.window.project_file, kind)
            record = {"original": relative, "prepared": None, "selected": "original"}
            if isinstance(previous, dict):
                history = deepcopy(previous.get("source_history", []))
                source = previous.get("source")
                if isinstance(source, dict) and source not in history:
                    history.append(deepcopy(source))
                if history:
                    record["source_history"] = history
            self.artwork_set(create=True)[kind] = record
            self.changed.emit()
            self.refresh()
        except (ArtworkValidationError, ProjectError) as exc:
            self.window.show_error("Artwork needs attention", str(exc))

    def prepare(self, kind):
        if not self.window.project_file:
            return
        record = self.artwork_set().get(kind)
        if not record:
            return
        original_document = deepcopy(self.window.document)
        if isinstance(record, dict):
            artwork = original_document["artwork"]
            if self.variant:
                artwork = artwork["variants"][self.variant]
            artwork[kind]["selected"] = "original"
        try:
            source = resolve_artwork(original_document, self.window.project_file, kind, variant=self.variant)
            dialog = PreparationDialog(source, kind, self.window.document["character"]["romanceable"], self)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                # Imports use content-addressed names and safe containment checks.
                relative = import_artwork(dialog.prepared, self.window.project_file, kind)
                if isinstance(record, str):
                    record = {"original": record}
                record = {**record, "prepared": relative, "selected": "prepared"}
                self.artwork_set(create=True)[kind] = record
                self.changed.emit()
                self.refresh()
            finally:
                dialog.temporary.cleanup()
                dialog.deleteLater()
        except (ProjectError, ArtworkValidationError, OSError) as exc:
            self.window.show_error("Could not prepare artwork", str(exc))

    def select_source(self, kind):
        record = self.artwork_set().get(kind)
        if isinstance(record, dict):
            record["selected"] = self.cards[kind]["select"].currentData()
            self.changed.emit()
            self.refresh()

    def remove(self, kind):
        if not self.artwork_set().get(kind):
            return
        self.artwork_set()[kind] = None
        self.changed.emit()
        self.refresh()
