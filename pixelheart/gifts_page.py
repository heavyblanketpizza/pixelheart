"""An offline item catalog with explicit gift assignments and local-game import."""

from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QMimeData, QSize, QPoint, QTimer, QEvent
from PySide6.QtGui import QDrag, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QComboBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QFileDialog, QDialogButtonBox, QCheckBox, QSizePolicy, QPlainTextEdit,
    QStyledItemDelegate,
)

from pixelheart_core.catalog import load_catalog, validate_catalog, CatalogValidationError
from pixelheart_core.exporting import GIFT_IDS
from pixelheart_core.gift_defaults import vanilla_gift_tastes
from pixelheart_core.gift_presets import GIFT_PRESETS, RECOMMENDED_GIFT_PRESET_ID, get_gift_preset
from pixelheart_core.validation import MAX_GIFTS_PER_TASTE
from .widgets import label, button, card
from .item_icons import ItemIconStore, texture_commands
from .wiki_gift_loader import WikiGiftDownload


TASTES = ("love", "like", "dislike", "hate")
GIFT_MIME = "application/x-pixelheart-gift-items"
EXPORT_COMMAND = "patch export Data/Objects"
VANILLA_FILE = Path(__file__).resolve().parent.parent / "pixelheart_core" / "data" / "vanilla_items.json"


def vanilla_catalog():
    return validate_catalog(json.loads(VANILLA_FILE.read_text(encoding="utf-8")))


class GiftTileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        # Keep long names inside their cell, including after a viewport resize.
        return self.parent().gridSize() - QSize(4, 4)


class GiftList(QListWidget):
    """Only accept item drags originating from this character's gift editor."""
    def __init__(self, page, taste=None):
        super().__init__()
        self.page, self.taste = page, taste
        self.drop_area = None
        self.setAccessibleName((taste.title() + " gifts") if taste else "Available gift items")
        self.setObjectName("giftTiles")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(QSize(32, 32))
        self.setGridSize(QSize(84, 86))
        self.setItemDelegate(GiftTileDelegate(self))
        self.setWordWrap(True)
        self.setSpacing(0)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        # Static icon movement disables drops on the viewport independently of
        # the list widget. Explicitly restore it for native mouse drops.
        self.viewport().setAcceptDrops(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Keep column widths stable as searches/defaults change the row count.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMinimumHeight(100)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.itemSelectionChanged.connect(lambda: self.page.select_list(self))

    def set_drop_area(self, widget):
        """Accept the same gift drop over the panel heading and margins."""
        self.drop_area = widget
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.drop_area:
            handler = {
                QEvent.Type.DragEnter: self.dragEnterEvent,
                QEvent.Type.DragMove: self.dragMoveEvent,
                QEvent.Type.DragLeave: self.dragLeaveEvent,
                QEvent.Type.Drop: self.dropEvent,
            }.get(event.type())
            if handler:
                handler(event)
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_tiles()

    def fit_tiles(self):
        """Fit whole columns and a readable first row inside each gift panel."""
        # QListView's icon layout also reserves the styled contents margins.
        margins = self.contentsMargins()
        width = max(1, self.viewport().width() - margins.left() - margins.right() - 2)
        compact = width < 240 or self.viewport().height() < 180
        icon_size = 24 if compact else 32
        columns = max(1, width // (78 if compact else 88))
        cell_width = min(104, width // columns)
        # Allow two name lines plus the inherited-default caption in taste lists.
        lines = 3 if self.taste else 2
        cell_height = icon_size + lines * self.fontMetrics().lineSpacing() + 14
        grid = QSize(cell_width, cell_height)
        if self.iconSize().width() != icon_size:
            self.setIconSize(QSize(icon_size, icon_size))
        if self.gridSize() != grid:
            self.setGridSize(grid)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count():
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#7b866f"))
            painter.drawText(self.viewport().rect().adjusted(12, 12, -12, -12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             "Drop gifts here" if self.taste else "No matching items")
            painter.end()

    def drop_feedback(self, active):
        for widget in (self, self.drop_area):
            if widget is not None and widget.property("dropTarget") != active:
                widget.setProperty("dropTarget", active)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
        self.viewport().update()

    def selected_values(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()]

    def startDrag(self, actions):
        values = self.selected_values()
        if not values:
            return
        mime = QMimeData()
        mime.setData(GIFT_MIME, json.dumps(values).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        selected = self.selectedItems()[0]
        drag.setPixmap(selected.icon().pixmap(self.iconSize()))
        drag.setHotSpot(QPoint(self.iconSize().width() // 2, self.iconSize().height() // 2))
        # Assignment changes happen in dropEvent, never by deleting model rows.
        try:
            drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            for target in [self.page.library, *self.page.lists.values()]:
                target.drop_feedback(False)

    def accepts(self, event):
        source = event.source()
        return isinstance(source, GiftList) and source.page is self.page and event.mimeData().hasFormat(GIFT_MIME)

    def dragEnterEvent(self, event):
        self.drop_feedback(self.accepts(event))
        event.acceptProposedAction() if self.accepts(event) else event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if self.accepts(event) else event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_feedback(False)
        event.accept()

    def dropEvent(self, event):
        self.drop_feedback(False)
        if not self.accepts(event):
            event.ignore()
            return
        try:
            values = json.loads(bytes(event.mimeData().data(GIFT_MIME)))
            allowed = {self.page.key(event.source().item(row).data(Qt.ItemDataRole.UserRole)) for row in range(event.source().count())}
            if not isinstance(values, list) or any(not isinstance(value, str) or self.page.key(value) not in allowed for value in values):
                raise ValueError("Unknown dragged item")
            if self.page.assign_items(values, self.taste):
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                event.ignore()
        except (ValueError, TypeError):
            event.ignore()

    def keyPressEvent(self, event):
        if self.taste and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.page.assign_items(self.selected_values(), None)
            event.accept()
        else:
            super().keyPressEvent(event)


class GameImportDialog(QDialog):
    """Use Content Patcher's supported export instead of parsing mod source files."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.catalog = None
        self.setWindowTitle("Load items from my game")
        self.setMinimumSize(680, 650)
        self.resize(700, 700)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 25, 28, 25)
        root.setSpacing(17)
        root.addWidget(label("Bring your valley with you.", "title"))
        root.addWidget(label("Import the object items from your modded game, including mod additions.", "muted", True))
        instructions, content = card()
        for title, detail in [
            ("1. Open your game through SMAPI", "Use Stardew Valley 1.6 with Content Patcher and your usual mods. Load the save whose items you want to use."),
            ("2. Export the current item data", "Paste this command into the SMAPI console and press Enter. Content Patcher will print the exported file's location."),
        ]:
            content.addWidget(label(title, "sectionTitle"))
            content.addWidget(label(detail, "muted", True))
        command_row = QHBoxLayout()
        self.command = QLineEdit(EXPORT_COMMAND)
        self.command.setReadOnly(True)
        self.command.setMinimumHeight(37)
        self.command.setAccessibleName("SMAPI item export command")
        command_row.addWidget(self.command, 1)
        copy = button("Copy command", self.copy_command)
        copy.setMinimumHeight(35)
        command_row.addWidget(copy)
        content.addLayout(command_row)
        content.addWidget(label("3. Choose the exported JSON", "sectionTitle"))
        content.addWidget(label("Select Data_Objects.json in your game's “patch export” folder. You can also select the game folder to find that file.", "muted", True))
        choices = QHBoxLayout()
        choose = button("Choose exported JSON…", self.choose_export, "primary")
        find = button("Find in game folder…", self.choose_game_folder)
        for control in (choose, find):
            control.setMinimumHeight(35)
            choices.addWidget(control)
        content.addLayout(choices)
        root.addWidget(instructions)
        self.status = label("", "muted", True)
        root.addWidget(self.status)
        root.addWidget(label("This reads an exported snapshot; it does not install mods or edit your game. Export again after changing mods or when save-dependent items change. Item names and IDs stay with this project.", "hint", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def copy_command(self):
        QApplication.clipboard().setText(EXPORT_COMMAND)
        self.status.setText("Command copied. Paste it into the SMAPI console.")

    def choose_export(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Content Patcher's Data_Objects.json", "", "Item data (*.json)")
        if path:
            self.import_path(path)

    def choose_game_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your Stardew Valley game folder")
        if not folder:
            return
        root = Path(folder)
        # Support the platform game root, macOS app bundle, and the export folder.
        candidates = [root / "patch export" / "Data_Objects.json", root / "Contents" / "MacOS" / "patch export" / "Data_Objects.json", root / "Stardew Valley.app" / "Contents" / "MacOS" / "patch export" / "Data_Objects.json", root / "Data_Objects.json"]
        found = [path for path in candidates if path.is_file()]
        if len(found) == 1:
            self.import_path(found[0])
        elif len(found) > 1:
            self.status.setText("Several exports were found. Use Choose exported JSON to pick the snapshot you want.")
        else:
            self.status.setText("No Data_Objects.json export was found here. Run the command above, then choose the file at the location printed by Content Patcher.")

    def import_path(self, path):
        try:
            imported = load_catalog(path)
            if not imported["items"]:
                self.status.setText("This export contains no giftable object entries. The current catalog has not been changed.")
                return False
            self.catalog = imported
            self.accept()
            return True
        except CatalogValidationError as exc:
            self.status.setText(str(exc))
            return False


class ItemIconsDialog(QDialog):
    """Bring original sprites from a user's Content Patcher texture exports."""

    def __init__(self, store, records, parent=None):
        super().__init__(parent)
        self.store, self.records = store, records
        self.setWindowTitle("Load item icons")
        self.resize(640, 590)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(14)
        root.addWidget(label("A familiar face for every gift.", "title", True))
        root.addWidget(label("Use the original item sprites from your local game. Labeled icons stay available for any missing textures.", "muted", True))
        root.addWidget(label("1. Export these textures in SMAPI", "sectionTitle"))
        root.addWidget(label("With Content Patcher and your mods loaded, run each command in the SMAPI console. Load the same save used for the item catalog.", "muted", True))
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setAccessibleName("SMAPI item texture export commands")
        commands = texture_commands(records)
        self.commands.setPlainText("\n".join(commands))
        self.commands.setMinimumHeight(120)
        root.addWidget(self.commands, 1)
        root.addWidget(button("Copy commands", self.copy_commands))
        root.addWidget(label("2. Choose the “patch export” folder", "sectionTitle"))
        root.addWidget(button("Choose exported textures…", self.choose_folder, "primary"))
        self.status = label("", "muted", True)
        if not commands:
            self.status.setText("This catalog has no sprite references. Load a fresh item export from your game first.")
        root.addWidget(self.status)
        root.addWidget(label("Sprites are cached on this computer for previews. They are not included in your NPC pack. Import again after changing texture mods.", "hint", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def copy_commands(self):
        QApplication.clipboard().setText(self.commands.toPlainText())
        self.status.setText("Commands copied. Run each line in the SMAPI console.")

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Content Patcher's patch export folder")
        if folder:
            try:
                result = self.store.import_folder(folder, self.records)
                missing = f" {len(result.missing_textures)} textures were missing or unreadable." if result.missing_textures else ""
                warnings = " ".join(result.warnings[:3])
                if len(result.warnings) > 3:
                    warnings += f" {len(result.warnings) - 3} more textures need review."
                self.status.setText(f"{result.imported_textures} textures imported; {result.ready_items} item sprites available.{missing} {warnings}".strip())
            except (OSError, ValueError) as exc:
                self.status.setText(f"Could not load item icons: {exc}")


class GiftsPage(QWidget):
    changed = Signal()
    download_stopped = Signal()

    def __init__(self, *, auto_download=False):
        super().__init__()
        self.auto_download = auto_download
        self.wiki_worker = None
        self._wiki_attempted = set()
        self._wiki_pending = set()
        self._wiki_message = ""
        self._wiki_progress = None
        self._wiki_shutdown = False
        self._wiki_refresh = QTimer(self)
        self._wiki_refresh.setSingleShot(True)
        self._wiki_refresh.setInterval(100)
        self._wiki_refresh.timeout.connect(self.refresh_wiki_icons)
        self.original = {}
        self._preset_undo = None
        self.icon_store = ItemIconStore()
        self.assignments = {taste: [] for taste in TASTES}
        self.base_catalog = vanilla_catalog()
        self.game_defaults = vanilla_gift_tastes()
        self.catalog = self.base_catalog
        self.custom_catalog = None
        self.active_list = None
        self.items = {}
        self.names = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        starter, starter_layout = card()
        starter.setStyleSheet("QFrame#card { background: #f0f3e8; border-color: #d4dcc2; }")
        starter_layout.setContentsMargins(16, 10, 16, 10)
        starter_layout.setSpacing(6)
        starter_layout.addWidget(label("Start with a gift preset", "sectionTitle"))
        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        self.preset_picker = QComboBox()
        self.preset_picker.setAccessibleName("Gift preset")
        for preset in GIFT_PRESETS:
            caption = preset.name + (" (recommended)" if preset.id == RECOMMENDED_GIFT_PRESET_ID else "")
            self.preset_picker.addItem(caption, preset.id)
        self.preset_picker.setCurrentIndex(self.preset_picker.findData(RECOMMENDED_GIFT_PRESET_ID))
        preset_row.addWidget(self.preset_picker, 1)
        self.apply_preset_button = button("Apply preset", self.apply_preset, "primary")
        self.apply_preset_button.setAccessibleName("Apply the selected gift preset")
        preset_row.addWidget(self.apply_preset_button)
        self.undo_preset_button = button("Undo preset", self.undo_preset, "quiet")
        self.undo_preset_button.setToolTip("Undo the last preset before making another gift edit.")
        self.undo_preset_button.setEnabled(False)
        preset_row.addWidget(self.undo_preset_button)
        starter_layout.addLayout(preset_row)
        self.preset_description = label("", "muted", True)
        starter_layout.addWidget(self.preset_description)
        preview_row = QHBoxLayout()
        self.preset_preview_button = button("Preview gifts ▸", self.toggle_preset_preview, "quiet")
        self.preset_preview_button.setCheckable(True)
        self.preset_preview_button.setAccessibleName("Show or hide the gift preset preview")
        preview_row.addWidget(self.preset_preview_button)
        self.preset_plan_label = label("", "hint", True)
        preview_row.addWidget(self.preset_plan_label, 1)
        starter_layout.addLayout(preview_row)
        self.preset_preview = QWidget()
        preview_grid = QGridLayout(self.preset_preview)
        preview_grid.setContentsMargins(0, 4, 0, 4)
        preview_grid.setHorizontalSpacing(16)
        preview_grid.setVerticalSpacing(6)
        self.preset_preview_labels = {}
        for column, taste in enumerate(TASTES):
            preview_grid.addWidget(label(taste.title(), "muted"), 0, column)
            names = label("", "hint", True)
            names.setAlignment(Qt.AlignmentFlag.AlignTop)
            preview_grid.addWidget(names, 1, column)
            preview_grid.setColumnStretch(column, 1)
            self.preset_preview_labels[taste] = names
        starter_layout.addWidget(self.preset_preview)
        self.preset_preview.hide()
        self.preset_status = label("", "muted", True)
        self.preset_status.hide()
        starter_layout.addWidget(self.preset_status)
        root.addWidget(starter)
        source_row = QHBoxLayout()
        self.source_label = label("", "badge")
        source_row.addWidget(self.source_label)
        source_row.addStretch()
        self.reset_catalog_button = button("Use vanilla catalog", self.reset_catalog)
        source_row.addWidget(self.reset_catalog_button)
        self.import_button = button("Load from my game…", self.import_game)
        source_row.addWidget(self.import_button)
        root.addLayout(source_row)
        icon_row = QHBoxLayout()
        self.icon_status = label("", "hint", True)
        icon_row.addWidget(self.icon_status, 1)
        self.wiki_button = button("Download icons", self.download_wiki_icons, "quiet")
        icon_row.addWidget(self.wiki_button)
        icon_row.addWidget(button("Use local textures…", self.import_icons, "quiet"))
        root.addLayout(icon_row)
        defaults_row = QHBoxLayout()
        defaults_row.setSpacing(12)
        self.show_game_defaults = QCheckBox("Show game defaults")
        self.show_game_defaults.setChecked(True)
        self.show_game_defaults.setAccessibleName("Show inherited game gift tastes")
        defaults_row.addWidget(self.show_game_defaults)
        self.defaults_hint = label("Vanilla 1.6.15 defaults · Drag gifts to change a taste. Reset selected restores the default.", "hint", True)
        defaults_row.addWidget(self.defaults_hint, 1)
        root.addLayout(defaults_row)
        self.catalog_notice = label("", "hint", True)
        root.addWidget(self.catalog_notice)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        library, content = card("Gift catalog")
        library.setMinimumWidth(250)
        content.setContentsMargins(12, 12, 12, 12)
        content.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search items or IDs…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search gift items")
        content.addWidget(self.search)
        self.filter = QComboBox()
        self.filter.setAccessibleName("Filter gift items by type")
        content.addWidget(self.filter)
        self.unassigned_only = QCheckBox("Without personal tastes")
        content.addWidget(self.unassigned_only)
        self.library = GiftList(self)
        self.library.set_drop_area(library)
        self.library.setToolTip("Drag an item into a taste panel. Drop it back here to restore the game default.")
        self.library.setMinimumHeight(140)
        content.addWidget(self.library, 1)
        self.results = label("", "hint")
        content.addWidget(self.results)
        assign_row = QHBoxLayout()
        self.target = QComboBox()
        self.target.setAccessibleName("Gift assignment category")
        for taste in TASTES:
            self.target.addItem(taste.title(), taste)
        self.target.addItem("Game default", None)
        assign_row.addWidget(self.target)
        assign_row.addWidget(button("Assign", self.assign_selected))
        content.addLayout(assign_row)
        columns.addWidget(library, 1)
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        self.lists, self.counts = {}, {}
        for index, taste in enumerate(TASTES):
            frame, content = card()
            content.setContentsMargins(10, 10, 10, 8)
            content.setSpacing(8)
            title_row = QHBoxLayout()
            title_row.addWidget(label(taste.title(), "sectionTitle"))
            title_row.addStretch()
            self.counts[taste] = label("0 items", "hint")
            title_row.addWidget(self.counts[taste])
            content.addLayout(title_row)
            content.addWidget(label(f"Drop items here to set {taste.title()}", "hint"))
            target = GiftList(self, taste)
            target.set_drop_area(frame)
            target.setToolTip(f"Drop an item anywhere in this panel to set {taste.title()}.")
            self.lists[taste] = target
            content.addWidget(target, 1)
            content.addWidget(button("Reset selected", lambda checked=False, key=taste: self.assign_items(self.lists[key].selected_values(), None), "quiet"))
            grid.addWidget(frame, index // 2, index % 2)
        columns.addLayout(grid, 2)
        root.addLayout(columns, 1)
        self.feedback = label("", "muted", True)
        root.addWidget(self.feedback)
        root.addWidget(label("An item can have one personal taste. Drag it between categories to move it, or back to the catalog to use the game default. Ctrl/Cmd-click selects multiple items.", "hint", True))
        self.search.textChanged.connect(self.render_library)
        self.filter.currentIndexChanged.connect(self.render_library)
        self.unassigned_only.toggled.connect(self.render_library)
        self.show_game_defaults.toggled.connect(self.render)
        self.preset_picker.currentIndexChanged.connect(self.refresh_preset)
        self._index_catalog()

    def toggle_preset_preview(self, checked):
        self.preset_preview.setVisible(checked)
        self.preset_preview_button.setText("Hide preview ▾" if checked else "Preview gifts ▸")

    def _preset_plan(self):
        preset = get_gift_preset(self.preset_picker.currentData())
        assigned = self.memberships()
        additions = {taste: [] for taste in TASTES}
        kept, missing = 0, 0
        for taste, item_ids in preset.gifts.items():
            for item_id in item_ids:
                if item_id in assigned:
                    kept += 1
                elif item_id not in self.items:
                    missing += 1
                else:
                    additions[taste].append("(O)" + item_id)
        return preset, additions, kept, missing

    def refresh_preset(self):
        preset, additions, kept, missing = self._preset_plan()
        self.preset_description.setText(preset.description)
        total = sum(map(len, additions.values()))
        full = [taste.title() for taste in TASTES
                if len(self.assignments[taste]) + len(additions[taste]) > MAX_GIFTS_PER_TASTE]
        if full:
            detail = f"{', '.join(full)} would exceed {MAX_GIFTS_PER_TASTE} gifts. Remove some items first."
        else:
            detail = f"{total} new gifts · Keeps your existing choices." if total else "No new gifts to add."
            if kept:
                detail += f" {kept} already assigned."
            if missing:
                detail += f" {missing} unavailable in this catalog."
        self.preset_plan_label.setText(detail)
        self.apply_preset_button.setEnabled(total > 0 and not full)
        base_items = {item["id"]: item for item in self.base_catalog["items"]}
        assigned = self.memberships()
        for taste, item_ids in preset.gifts.items():
            names, details = [], []
            for item_id in item_ids:
                record = self.items.get(item_id) or base_items.get(item_id)
                name = record["name"] if record else f"Object {item_id}"
                names.append(name)
                note = f"kept in {assigned[item_id].title()}" if item_id in assigned else "not in this catalog" if item_id not in self.items else f"add to {taste.title()}"
                details.append(f"{name}: {note}")
            self.preset_preview_labels[taste].setText(" · ".join(names))
            self.preset_preview_labels[taste].setToolTip("\n".join(details))

    def _clear_preset_undo(self):
        self._preset_undo = None
        self.undo_preset_button.setEnabled(False)
        self.preset_status.clear()
        self.preset_status.hide()

    def apply_preset(self):
        preset, additions, kept, missing = self._preset_plan()
        total = sum(map(len, additions.values()))
        updated = {taste: [*self.assignments[taste], *additions[taste]] for taste in TASTES}
        if any(len(values) > MAX_GIFTS_PER_TASTE for values in updated.values()):
            self.preset_status.setText(f"This preset would exceed {MAX_GIFTS_PER_TASTE} gifts in one taste. Your gifts were not changed.")
            self.preset_status.show()
            return False
        if not total:
            self.preset_status.setText("No gifts were changed. " + self.preset_plan_label.text())
            self.preset_status.show()
            return False
        self._preset_undo = (deepcopy(self.assignments), deepcopy(updated))
        self.assignments = updated
        self.render()
        self.undo_preset_button.setEnabled(True)
        message = f"Added {total} gifts from {preset.name}. You can customize any item below."
        if kept:
            message += f" Kept {kept} existing choices."
        if missing:
            message += f" Skipped {missing} items not in this catalog."
        self.preset_status.setText(message)
        self.preset_status.show()
        self.changed.emit()
        return True

    def undo_preset(self):
        if self._preset_undo is None:
            return False
        before, after = self._preset_undo
        if self.assignments != after:
            self._clear_preset_undo()
            return False
        self.assignments = deepcopy(before)
        self._clear_preset_undo()
        self.render()
        self.preset_status.setText("Preset undone. Your previous gift choices have been restored.")
        self.preset_status.show()
        self.changed.emit()
        return True

    def _index_catalog(self):
        self.items = {item["id"]: item for item in self.catalog["items"]}
        self.names = {}
        for item in self.catalog["items"]:
            self.names.setdefault(item["name"].casefold(), []).append(item["id"])
        previous = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All item types", None)
        for name in sorted({item["category_name"] for item in self.catalog["items"]}):
            self.filter.addItem(name, name)
        index = self.filter.findData(previous)
        self.filter.setCurrentIndex(max(0, index))
        self.filter.blockSignals(False)
        self.source_label.setText(("My game" if self.custom_catalog else self.catalog["label"]) + f"  ·  {len(self.items)} items")
        self.reset_catalog_button.setVisible(self.custom_catalog is not None)
        imported = self.catalog.get("imported_at")
        self.source_label.setToolTip(self.catalog["label"] + ("\nImported: " + imported if imported else ""))
        self.catalog_notice.setText("Defaults shown for matching vanilla item IDs. This item import does not include your game's modified gift rules; mod items have no assumed default." if self.custom_catalog else "Vanilla object gifts. Neutral defaults stay in the catalog. Trinkets and dynamically flavored variants are outside this catalog.")
        self.catalog_notice.setToolTip("\n".join(self.catalog.get("warnings", [])))
        self.catalog_notice.setVisible(bool(self.catalog_notice.text()))
        self.icon_store.prepare(self.catalog["items"])
        self.update_icon_status()
        self.render()
        if self.auto_download and self.isVisible():
            QTimer.singleShot(0, lambda: self.download_wiki_icons(automatic=True))

    def showEvent(self, event):
        super().showEvent(event)
        if self.auto_download:
            QTimer.singleShot(0, lambda: self.download_wiki_icons(automatic=True))

    def update_icon_status(self):
        ready = sum(self.icon_store.has_icon(record) for record in self.catalog["items"])
        if self._wiki_progress is not None:
            done, total = self._wiki_progress
            detail = f"Downloading from Stardew Wiki… {done}/{total}"
        elif self._wiki_message:
            detail = self._wiki_message
        else:
            detail = "Artwork © ConcernedApe · Stardew Valley Wiki / local textures"
        self.icon_status.setText(f"Game sprites: {ready} of {len(self.items)} · {detail}")
        missing = self.icon_store.missing_wiki_ids(self.catalog["items"])
        self.wiki_button.setVisible(bool(missing) or self.wiki_worker is not None)
        self.wiki_button.setEnabled(self.wiki_worker is None and bool(missing))
        self.wiki_button.setText("Downloading…" if self.wiki_worker is not None else "Retry icons" if self._wiki_message else "Download icons")

    def download_wiki_icons(self, checked=False, *, automatic=False):
        if self.wiki_worker is not None or self._wiki_shutdown:
            return
        missing = self.icon_store.missing_wiki_ids(self.catalog["items"])
        if automatic:
            missing = [item_id for item_id in missing if item_id not in self._wiki_attempted]
        if not missing:
            return
        # Show allocated items first, then follow the visible catalog's order.
        assigned = self.memberships()
        missing.sort(key=lambda item_id: item_id not in assigned)
        self._wiki_attempted.update(missing)
        self._wiki_progress = (0, len(missing))
        self._wiki_message = ""
        worker = WikiGiftDownload(missing, self.icon_store.wiki_cache_dir, self)
        self.wiki_worker = worker
        worker.item_ready.connect(self.wiki_icon_ready)
        worker.progress.connect(self.wiki_download_progress)
        worker.completed.connect(self.wiki_download_result)
        worker.failed.connect(self.wiki_download_failed)
        worker.finished.connect(self.wiki_download_finished)
        self.update_icon_status()
        worker.start()

    def wiki_icon_ready(self, item_id):
        self._wiki_pending.add(item_id)
        if not self._wiki_refresh.isActive():
            self._wiki_refresh.start()

    def wiki_download_progress(self, done, total):
        self._wiki_progress = (done, total)
        if not self._wiki_refresh.isActive():
            self._wiki_refresh.start()

    def refresh_wiki_icons(self):
        pending, self._wiki_pending = self._wiki_pending, set()
        if pending:
            self.icon_store.invalidate_wiki_icons(pending)
            # Update artwork in place so downloads cannot change selections,
            # scroll position, drag payloads, or the user's gift assignments.
            for target in (self.library, *self.lists.values()):
                for row in range(target.count()):
                    item = target.item(row)
                    value = item.data(Qt.ItemDataRole.UserRole)
                    if self.key(value) in pending:
                        item.setIcon(self.item_icon(value))
        self.update_icon_status()

    def wiki_download_result(self, result):
        failed = len(result.get("failed", {}))
        if failed:
            self._wiki_message = f"{failed} icons unavailable. Retry when online."
        elif result.get("cancelled"):
            self._wiki_message = "Download stopped; cached icons remain available."

    def wiki_download_failed(self, message):
        self._wiki_message = "Wiki icons unavailable. Retry when online."
        self.icon_status.setToolTip(message)

    def wiki_download_finished(self):
        worker, self.wiki_worker = self.wiki_worker, None
        if worker is not None:
            worker.deleteLater()
        self._wiki_progress = None
        self._wiki_refresh.stop()
        self.refresh_wiki_icons()
        self.download_stopped.emit()
        if self.auto_download and self.isVisible() and not self._wiki_shutdown:
            QTimer.singleShot(0, lambda: self.download_wiki_icons(automatic=True))

    def cancel_wiki_download(self):
        self._wiki_shutdown = True
        if self.wiki_worker is not None:
            self.wiki_worker.requestInterruption()

    def is_downloading_icons(self):
        return self.wiki_worker is not None

    def item_icon(self, value):
        record = self.items.get(self.key(value), {"id": self.key(value), "name": value, "category": 0})
        return self.icon_store.icon(record, size=48)

    def import_icons(self):
        dialog = ItemIconsDialog(self.icon_store, self.catalog["items"], self)
        dialog.exec()
        self._index_catalog()
        dialog.deleteLater()

    def key(self, value):
        if value.startswith("(O)"):
            return value[3:]
        if value.startswith("id:"):
            return value[3:]
        if value in self.items:
            return value
        if value.casefold() in GIFT_IDS:
            return GIFT_IDS[value.casefold()]
        matches = self.names.get(value.casefold(), [])
        return matches[0] if len(matches) == 1 else value

    def title(self, value):
        item = self.items.get(self.key(value))
        return item["name"] if item else value

    def memberships(self):
        return {self.key(value): taste for taste in TASTES for value in self.assignments[taste]}

    def visible_memberships(self):
        """Add inherited display rows without creating saved personal tastes."""
        visible = {
            item_id: taste for item_id, taste in self.game_defaults.items()
            if item_id in self.items and taste in TASTES
        } if self.show_game_defaults.isChecked() else {}
        visible.update(self.memberships())
        return visible

    def render_library(self):
        selected = {self.key(value) for value in self.library.selected_values()}
        self.library.blockSignals(True)
        self.library.clear()
        query = self.search.text().strip().casefold().split()
        category = self.filter.currentData()
        assigned = self.memberships()
        for record in self.catalog["items"]:
            item_id = record["id"]
            if category is not None and record["category_name"] != category:
                continue
            if self.unassigned_only.isChecked() and item_id in assigned:
                continue
            if not all(word in (record["name"] + " " + item_id).casefold() for word in query):
                continue
            taste = assigned.get(item_id)
            item = QListWidgetItem(record["name"] + ("  ·  " + taste.title() if taste else ""))
            item.setIcon(self.item_icon("(O)" + item_id))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setData(Qt.ItemDataRole.UserRole, "(O)" + item_id)
            if taste:
                detail = "Personal taste: " + taste.title()
            elif item_id in self.game_defaults:
                detail = "Game default: " + self.game_defaults[item_id].title() + " · Vanilla 1.6.15"
                if self.custom_catalog:
                    detail += "\nThis item import does not include modified gift rules."
            elif item_id == "StardropTea":
                detail = "Special gift behavior · Stardrop Tea uses its own friendship rules."
            else:
                detail = "No vanilla gift default is available for this item."
            item.setToolTip(f"{record['name']}\n{record['category_name']} · (O){item_id}\n" + detail)
            self.library.addItem(item)
            item.setSelected(item_id in selected)
        self.results.setText(f"{self.library.count()} of {len(self.items)} items")
        self.library.blockSignals(False)

    def render(self):
        missing = set()
        personal = self.memberships()
        visible = self.visible_memberships()
        show_defaults = self.show_game_defaults.isChecked()
        for taste, target in self.lists.items():
            selected = {self.key(value) for value in target.selected_values()}
            target.blockSignals(True)
            target.clear()
            for value in self.assignments[taste]:
                key = self.key(value)
                known = key in self.items
                item = QListWidgetItem(self.title(value) + ("" if known else " · not in catalog"))
                item.setIcon(self.item_icon(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                item.setData(Qt.ItemDataRole.UserRole, value)
                detail = f"Personal taste: {taste.title()} · {value}" if known else "Saved assignment retained. Import the matching game catalog or return this item to default."
                item.setToolTip(self.title(value) + "\n" + detail)
                target.addItem(item)
                item.setSelected(key in selected)
                if not known:
                    missing.add(key)
            default_count = 0
            for record in self.catalog["items"]:
                item_id = record["id"]
                if item_id in personal or visible.get(item_id) != taste:
                    continue
                value = "(O)" + item_id
                item = QListWidgetItem(record["name"] + "\nDefault")
                item.setIcon(self.item_icon(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                item.setForeground(QColor("#798074"))
                item.setData(Qt.ItemDataRole.UserRole, value)
                detail = f"Game default: {taste.title()} · Vanilla 1.6.15 · {value}\nDrag to another taste to make a personal choice."
                if self.custom_catalog:
                    detail += "\nThis item import does not include modified gift rules."
                item.setToolTip(record["name"] + "\n" + detail)
                target.addItem(item)
                item.setSelected(item_id in selected)
                default_count += 1
            count = f"{len(self.assignments[taste])} personal"
            if show_defaults:
                count += f" · {default_count} default"
            self.counts[taste].setText(count)
            target.blockSignals(False)
        self.feedback.setText(f"{len(missing)} saved assignment(s) are outside this catalog. They are preserved; review them before export." if missing else "")
        self.feedback.setVisible(bool(missing))
        self.render_library()
        self.refresh_preset()

    def select_list(self, target):
        if not target.selectedItems():
            return
        self.active_list = target
        for other in [self.library, *self.lists.values()]:
            if other is not target:
                other.blockSignals(True)
                other.clearSelection()
                other.blockSignals(False)

    def assign_selected(self):
        if self.active_list is not None:
            self.assign_items(self.active_list.selected_values(), self.target.currentData())

    def assign_items(self, values, taste):
        if taste is not None and taste not in TASTES:
            return False
        if not values:
            return False
        keys = {self.key(value) for value in values}
        known = set(self.items) | {self.key(value) for entries in self.assignments.values() for value in entries}
        if keys - known:
            return False
        updated = {name: [value for value in entries if self.key(value) not in keys] for name, entries in self.assignments.items()}
        if taste:
            seen = set()
            for value in values:
                key = self.key(value)
                if key not in seen:
                    updated[taste].append("(O)" + key if key in self.items else value)
                    seen.add(key)
            if len(updated[taste]) > MAX_GIFTS_PER_TASTE:
                self.feedback.setText(f"A taste can contain up to {MAX_GIFTS_PER_TASTE} items. Select fewer items.")
                self.feedback.show()
                return False
        if updated == self.assignments:
            return True
        self._clear_preset_undo()
        self.assignments = updated
        self.render()
        self.changed.emit()
        return True

    def load(self, gifts):
        self._clear_preset_undo()
        self.original = deepcopy(gifts)
        self.assignments = {taste: list(gifts.get(taste, [])) for taste in TASTES}
        self.render()

    def dump(self):
        # Recognized legacy names migrate to stable IDs. Unknown saved references
        # remain intact so changing catalogs can never silently delete a taste.
        resolved = {}
        for taste, values in self.assignments.items():
            resolved[taste] = list(dict.fromkeys("(O)" + self.key(value) if self.key(value) in self.items else value for value in values))
        return {**deepcopy(self.original), **resolved}

    def load_catalog_snapshot(self, snapshot):
        self.custom_catalog = validate_catalog(snapshot) if snapshot is not None else None
        self._clear_preset_undo()
        self.catalog = self.custom_catalog or self.base_catalog
        self._index_catalog()

    def catalog_snapshot(self):
        return deepcopy(self.custom_catalog)

    def import_game(self):
        dialog = GameImportDialog(self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.catalog is not None:
                self.load_catalog_snapshot(dialog.catalog)
                self.changed.emit()
        finally:
            dialog.deleteLater()

    def reset_catalog(self):
        if self.custom_catalog is not None:
            self.load_catalog_snapshot(None)
            self.changed.emit()
