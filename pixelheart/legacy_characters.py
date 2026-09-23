"""Limited repair and removal for characters retained from older projects."""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QFileDialog, QMessageBox

from pixelheart_core.artwork import inspect_artwork, ArtworkValidationError
from pixelheart_core.projects import import_artwork, ProjectError
from pixelheart_core.world import WorldError
from .widgets import label, button


class LegacyCharactersDialog(QDialog):
    def __init__(self, page, *, index=0):
        super().__init__(page)
        self.page = page
        self.window = page.window
        self.setWindowTitle("Legacy bundled characters")
        self.resize(560, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(label("Characters from an older project", "sectionTitle"))
        layout.addWidget(label("Each project now focuses on one custom NPC. These older bundled characters are preserved with their existing content. Repair missing artwork here, or remove a character from this pack. Other validation errors require removing the legacy character or repairing the project backup.", "muted", True))
        self.listing = QListWidget()
        self.listing.setAccessibleName("Legacy bundled characters")
        layout.addWidget(self.listing, 1)
        self.artwork_status = label("", "hint", True)
        layout.addWidget(self.artwork_status)
        row = QHBoxLayout()
        self.import_buttons = []
        for kind in ("portrait", "sprite"):
            control = button("Import " + kind + " sheet…", lambda checked=False, k=kind: self.import_artwork(k))
            self.import_buttons.append(control)
            row.addWidget(control)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.remove_button = button("Remove from this pack…", self.remove_character)
        row.addWidget(self.remove_button)
        row.addStretch()
        row.addWidget(button("Done", self.accept, "primary"))
        layout.addLayout(row)
        self.listing.currentRowChanged.connect(self.refresh_selection)
        self.refresh_list(index)

    def refresh_list(self, index=0):
        self.listing.clear()
        self.listing.addItems([entry["character"].get("name") or "Unnamed legacy character"
                              for entry in self.page.world["characters"]])
        self.listing.setCurrentRow(min(index, self.listing.count() - 1))
        self.refresh_selection()

    def selected_record(self):
        index = self.listing.currentRow()
        records = self.page.world["characters"]
        return records[index] if 0 <= index < len(records) else None

    def refresh_selection(self):
        record = self.selected_record()
        self.remove_button.setEnabled(record is not None)
        for control in self.import_buttons:
            control.setEnabled(record is not None)
        artwork = record.get("artwork", {}) if record else {}
        self.artwork_status.setText("\n".join(kind.title() + ": " + (artwork.get(kind) or "No sheet selected")
                                              for kind in ("portrait", "sprite")) if record else "No legacy characters remain in this pack.")

    def import_artwork(self, kind):
        record = self.selected_record()
        if record is None:
            return
        identity = record["id"]
        path, _ = QFileDialog.getOpenFileName(self, "Repair legacy character " + kind, "", "PNG artwork (*.png)")
        if not path:
            return
        try:
            inspect_artwork(path)
            if not self.window.ensure_saved():
                return
            # First save reloads the page, so resolve the original record again.
            record = next(entry for entry in self.page.world["characters"] if entry["id"] == identity)
            record.setdefault("artwork", {})[kind] = import_artwork(path, self.window.project_file, kind)
            self.refresh_selection()
            self.page.changed.emit()
        except (ArtworkValidationError, ProjectError, WorldError, OSError) as exc:
            self.window.show_error("Legacy artwork needs attention", str(exc))

    def remove_character(self):
        record = self.selected_record()
        if record is None:
            return
        name = record["character"].get("name") or "this character"
        answer = QMessageBox.question(self, "Remove legacy character",
                                      f"Remove {name} from this pack? Their bundled content will be removed from the project when you save. Artwork files remain. Review any scenes that reference this character.",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        index = self.listing.currentRow()
        del self.page.world["characters"][index]
        self.page.load(self.page.world)
        self.refresh_list(index)
        self.page.changed.emit()
