"""Picture catalogue for permanent architectural map pieces."""
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QComboBox, QListWidget, QListWidgetItem

from pixelheart_core.interior_architecture import architecture_preview
from pixelheart_core.interior_architecture_rules import architecture_rule_hint, architecture_rule_issues
from .widgets import label, button


class ArchitecturePanel(QWidget):
    picked = Signal(str)
    connect_requested = Signal()
    visible_pieces_changed = Signal(list)
    issue_selected = Signal(str)

    def __init__(self, root, configure_gallery, parent=None):
        super().__init__(parent)
        self.root = root
        self.data = {}
        self._signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a counter, column, staircase…")
        self.search.setAccessibleName("Search architectural pieces")
        self.search.textChanged.connect(self.refresh_catalog)
        layout.addWidget(self.search)
        self.category = QComboBox()
        self.category.setAccessibleName("Architectural piece category")
        self.category.currentIndexChanged.connect(self.refresh_catalog)
        layout.addWidget(self.category)
        self.catalog = QListWidget()
        configure_gallery(self.catalog, "Architectural pieces: click to pick up")
        self.catalog.setMinimumHeight(245)
        self.catalog.itemClicked.connect(lambda item: self.picked.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.catalog.currentItemChanged.connect(self.show_catalog_rule)
        layout.addWidget(self.catalog, 1)
        self.empty = label("", "muted", True)
        layout.addWidget(self.empty)
        self.connect_button = button("Refresh game library…", self.connect_requested.emit)
        layout.addWidget(self.connect_button)
        self.hint = label("Choose a piece to see where it belongs. Click to pick it up, then click to place it.", "hint", True)
        layout.addWidget(self.hint)
        self.issues_title = label("Placements to fix", "notice")
        layout.addWidget(self.issues_title)
        self.issues_list = QListWidget()
        self.issues_list.setAccessibleName("Architectural placements that need repair")
        self.issues_list.setMaximumHeight(100)
        self.issues_list.itemClicked.connect(lambda item: self.issue_selected.emit(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.issues_list)
        self.issues_title.hide()
        self.issues_list.hide()

    def show_catalog_rule(self, item, previous=None):
        identity = item.data(Qt.ItemDataRole.UserRole) if item else None
        definition = next((d for d in self.data.get("architecture_catalog", []) if d["id"] == identity), None)
        self.show_rule(definition)

    def show_rule(self, definition):
        self.hint.setText(architecture_rule_hint(definition) if definition else
                          "Choose a piece to see where it belongs. Click to pick it up, then click to place it.")

    def refresh(self, data):
        self.data = data
        categories = sorted({d["category"] for d in data.get("architecture_catalog", [])})
        previous = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All architectural pieces", "all")
        for category in categories:
            self.category.addItem(category.replace("_", " ").title(), category)
        self.category.setCurrentIndex(max(0, self.category.findData(previous)))
        self.category.blockSignals(False)
        self.refresh_catalog()
        issues = architecture_rule_issues(data)
        self.issues_list.clear()
        seen = set()
        for issue in issues:
            if issue["placement_id"] in seen:
                continue
            seen.add(issue["placement_id"])
            definition = next(d for d in data["architecture_catalog"] if d["id"] == issue["piece_id"])
            item = QListWidgetItem(definition["name"])
            item.setData(Qt.ItemDataRole.UserRole, issue["placement_id"])
            item.setToolTip(issue["message"])
            self.issues_list.addItem(item)
        self.issues_title.setText(f"Placements to fix · {len(seen)}")
        self.issues_title.setVisible(bool(issues))
        self.issues_list.setVisible(bool(issues))

    def refresh_catalog(self, *_):
        definitions = self.data.get("architecture_catalog", [])
        query = self.search.text().strip().casefold()
        category = self.category.currentData()
        signature = (repr(definitions), repr(self.data.get("atlas")), query, category)
        if signature == self._signature:
            return
        self._signature = signature
        self.catalog.clear()
        for definition in definitions:
            if category not in (None, "all", definition["category"]):
                continue
            if query and query not in (definition["name"] + " " + definition["category"]).casefold():
                continue
            item = QListWidgetItem(definition["name"])
            item.setData(Qt.ItemDataRole.UserRole, definition["id"])
            item.setToolTip(f"{definition['name']} · {definition['width']} × {definition['height']} tiles\n{architecture_rule_hint(definition)}")
            try:
                preview = architecture_preview(definition, self.data, self.root)
                pixmap = QPixmap.fromImage(ImageQt(preview))
                preview.close()
                item.setIcon(QIcon(pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)))
            except (ValueError, OSError):
                item.setToolTip(item.toolTip() + " · Artwork unavailable; restore the missing project tilesheet")
            self.catalog.addItem(item)
        self.empty.setText("No matches. Try another category or search." if definitions else
                           "Connect a library with architectural pieces to browse built-in counters, columns, stairs and wall fixtures. If your library is older, open a save with the updated companion, then refresh it here.")
        self.empty.setVisible(not self.catalog.count())
        self.connect_button.setVisible(not definitions)
        for widget in (self.search, self.category, self.catalog, self.hint):
            widget.setVisible(bool(definitions))
        self.visible_pieces_changed.emit([self.catalog.item(row).data(Qt.ItemDataRole.UserRole)
                                         for row in range(self.catalog.count())])
