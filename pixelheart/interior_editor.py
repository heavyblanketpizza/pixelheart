"""Independent interior authoring UI: rooms, real item placements and tile art.

The dialog stages supplied assets until acceptance. It never edits game files,
and its animation preview does not claim to simulate an installed item mod.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import time

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QRect, QSize, QPoint, Signal, QTimer, QMimeData, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon, QShortcut, QKeySequence, QDrag
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter,
    QTabWidget, QScrollArea, QComboBox, QSpinBox, QCheckBox, QLineEdit,
    QListWidget, QListWidgetItem, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QListView, QStackedWidget,
    QStyledItemDelegate, QStyle, QApplication, QSizePolicy,
)

from pixelheart_core.interiors import (
    InteriorDraft, import_atlas, render_interior, footprint, room_edit_candidate,
    ensure_doorway, place_doorway, interior_asset_references, spouse_access_issues, validate_spouse_access,
    normalize_interior,
)
from pixelheart_core.interior_layout import (
    resize_room_candidate, corridor_candidate, partition_candidate,
    opening_candidate, remove_partition_candidate, native_partition_thickness, partition_rectangle,
)
from pixelheart_core.interior_architecture import (
    architecture_candidate, remove_architecture_candidate, stage_architecture_library,
)
from pixelheart_core.interior_architecture_rules import architecture_rule, architecture_rule_issues, validate_architecture_rules
from pixelheart_core.interior_furniture import (
    import_furniture_library, validate_definition, import_catalog_textures,
    attach_texture, preview_frame,
)
from pixelheart_core.world import asset_path, _read_asset, _write_new_file
from .widgets import label, button
from .game_import import game_import_settings
from .interior_canvas import FURNITURE_MIME, ROOM_MIME, InteriorCanvas
from .architecture_panel import ArchitecturePanel


def _number(minimum=0, maximum=255, initial=0):
    field = QSpinBox()
    field.setRange(minimum, maximum)
    field.setValue(initial)
    return field


def _asset_references(design):
    yield from interior_asset_references(design)


class CatalogueTile(QStyledItemDelegate):
    """Fixed picture cards keep labels stable when an item is selected."""

    def sizeHint(self, option, index):
        return QSize(94, 112)

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor("#6b8051" if selected else "#d8c8a9"), 2 if selected else 1))
        painter.setBrush(QColor("#e5e9d5" if selected else "#f0e4c7" if hovered else "#fffbf0"))
        painter.drawRoundedRect(rect, 3, 3)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(QSize(64, 64))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(rect.center().x() - pixmap.width() // 2, rect.top() + 6 + (64 - pixmap.height()) // 2, pixmap)
        painter.setPen(QColor("#354425" if selected else "#554833"))
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QRect(rect.left()+4, rect.top()+73, rect.width()-8, 32),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                         index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.restore()


class FurnitureCatalogue(QListWidget):
    """Copy catalogue pieces into the room with a native mouse drag."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self._drag_started = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_started = False
        super().mousePressEvent(event)

    def pick_up(self, item):
        # A native drag can finish with a catalogue click notification. Only
        # a fresh mouse press may pick up another copy after that gesture.
        if not self._drag_started:
            self.editor.begin_catalog_placement(item)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        identity = item.data(Qt.ItemDataRole.UserRole)
        definition = next((d for d in self.editor.draft.data["catalog"] if d["id"] == identity), None)
        if not definition or not definition.get("footprint"):
            return
        self._drag_started = True
        # Capture everything before exec: committing a drop may rebuild the
        # catalogue (Recently used), invalidating the QListWidgetItem.
        pixmap = item.icon().pixmap(QSize(48, 48))
        mime = QMimeData()
        mime.setData(FURNITURE_MIME, identity.encode("utf-8"))
        self.editor.canvas.cancel_interaction()
        self.editor.coordinates.setText("Drag into the room · Release to place · Esc to cancel")
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.editor.canvas.clear_catalogue_drag()
            self.editor.canvas.clear_placement()
            self.editor.set_tool("select")
            self.editor.preview_feedback(True, "")


class RoomPreset(QWidget):
    """Drag the selected room size into the layout without directional controls."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self._press = None
        self.setFixedHeight(80)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName("Drag a new room into the layout")
        self.setToolTip("Drag this room onto the canvas, beside an existing room.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#8c9d73"), 2, Qt.PenStyle.DashLine))
        painter.setBrush(QColor("#eef0e3"))
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 5, 5)
        width, height = self.editor.room_size.currentData()
        raised = self.editor.room_type.currentData() == "raised"
        total_height = height + (4 if raised else 0)
        cell = min(7, 64 // max(width, total_height))
        room = QRect(20, (self.height() - total_height * cell) // 2, width * cell, height * cell)
        painter.setBrush(QColor("#d8c596"))
        painter.setPen(QPen(QColor("#796747"), 2))
        painter.drawRect(room)
        if raised:
            steps = QRect(room.center().x() - cell, room.bottom() + 1, 2 * cell, 4 * cell)
            painter.drawRect(steps)
            for row in (1, 2, 3):
                painter.drawLine(steps.left(), steps.top() + row * cell, steps.right(), steps.top() + row * cell)
        painter.setPen(QColor("#354425"))
        painter.drawText(QRect(98, 15, self.width()-110, self.height()-30),
                         Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                         f"{'Drag raised room + steps' if raised else 'Drag room into layout'}\n{width} × {height} tiles")
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if (self._press is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance()):
            return
        self._press = None
        self.editor.canvas.cancel_interaction()
        mime = QMimeData()
        mime.setData(ROOM_MIME, b"new-room")
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        width, height = self.editor.room_size.currentData()
        drag.setHotSpot(QPoint(20 + width * min(7, 64 // max(width, height)) // 2, self.height() // 2))
        self.editor.coordinates.setText("Drag above a lower room, leaving four tiles for steps and landings · Release when green" if self.editor.room_type.currentData() == "raised"
                                        else "Drag beside a room · Release when the outline is green · Esc to cancel")
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.editor.canvas.clear_room_catalogue_drag()
            self.editor.set_tool("room-select")
            self.editor.preview_feedback(True, "")

    def mouseReleaseEvent(self, event):
        self._press = None
        event.accept()


class InteriorPalette(QWidget):
    selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.columns = 0
        self.tile_count = 0
        self.selection = 0
        self.setAccessibleName("Interior tilesheet; click a tile to apply it")

    def load(self, atlas, root):
        self.columns = atlas.get("columns", 0)
        self.tile_count = atlas.get("tile_count", 0)
        self.pixmap = QPixmap(str(asset_path(atlas["asset"], root))) if atlas.get("asset") else QPixmap()
        size = self.pixmap.size() * 2 if not self.pixmap.isNull() else QSize(240, 100)
        self.setFixedSize(size)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#e8e2d5"))
        if self.pixmap.isNull():
            painter.setPen(QColor("#796b56"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Choose a tilesheet PNG")
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(self.rect(), self.pixmap)
            if self.columns:
                painter.setPen(QPen(QColor("#61734d"), 2))
                painter.drawRect(self.selection % self.columns * 32 + 1,
                                 self.selection // self.columns * 32 + 1, 30, 30)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.columns:
            return
        tile = int(event.position().y()) // 32 * self.columns + int(event.position().x()) // 32
        if 0 <= tile < self.tile_count:
            self.selection = tile
            self.selected.emit(tile)
            self.update()


class FurnitureDetails(QDialog):
    """Explicit metadata for a native or installed-mod furniture item."""

    def __init__(self, definition=None, parent=None):
        super().__init__(parent)
        self.original = deepcopy(definition or {})
        self.result_definition = None
        self.texture_source = None
        self.setWindowTitle("Furniture details — Pixelheart")
        self.resize(620, 820)
        root = QVBoxLayout(self)
        root.addWidget(label("Describe an existing game or mod furniture item.", "sectionTitle", True))
        root.addWidget(label("The item ID must exist in the installed game. Preview frames describe its appearance; they do not create new furniture behavior.", "muted", True))
        form = QFormLayout()
        self.item_id = QLineEdit(self.original.get("id", "(F)"))
        self.name = QLineEdit(self.original.get("name", ""))
        self.kind = QLineEdit(self.original.get("kind", "other"))
        self.dependency = QLineEdit(self.original.get("dependency", ""))
        self.width = _number(1, 32, (self.original.get("footprint") or [1, 1])[0])
        self.height = _number(1, 32, (self.original.get("footprint") or [1, 1])[1])
        self.rotations = QComboBox()
        for count in (1, 2, 4):
            self.rotations.addItem(str(count), count)
        self.rotations.setCurrentIndex(self.rotations.findData(self.original.get("rotations", 1)))
        for title, field in (("Qualified item ID", self.item_id), ("Display name", self.name),
                             ("Furniture type", self.kind), ("Required mod ID", self.dependency),
                             ("Footprint width (tiles)", self.width), ("Footprint height (tiles)", self.height),
                             ("Rotation states", self.rotations)):
            field.setAccessibleName(title)
            form.addRow(title, field)
        root.addLayout(form)
        root.addWidget(label("Footprint per rotation · optional; blank cells use the base footprint", "hint", True))
        self.rotation_bounds = QTableWidget(0, 3)
        self.rotation_bounds.setHorizontalHeaderLabels(["Rotation", "Width (tiles)", "Height (tiles)"])
        self.rotation_bounds.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rotation_bounds.setMaximumHeight(135)
        self.rotation_bounds.setAccessibleName("Furniture footprint for each rotation")
        self.rotations.currentIndexChanged.connect(self.update_rotation_bounds)
        self.update_rotation_bounds()
        for key, dimensions in self.original.get("rotation_footprints", {}).items():
            for column, number in enumerate(dimensions, 1):
                self.rotation_bounds.setItem(int(key), column, QTableWidgetItem(str(number)))
        root.addWidget(self.rotation_bounds)
        self.texture_label = label(self.original.get("preview_asset") or "No preview texture attached", "hint", True)
        root.addWidget(button("Attach preview PNG…", self.choose_texture))
        root.addWidget(self.texture_label)
        root.addWidget(label("Preview frames · pixel rectangles; rotation states begin at 0", "hint", True))
        self.frames = QTableWidget(0, 6)
        self.frames.setHorizontalHeaderLabels(["Rotation", "X", "Y", "Width", "Height", "ms"])
        self.frames.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.frames.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for frame in self.original.get("frames", []):
            self.add_frame([frame["rotation"], *frame["rect"], frame["duration_ms"]], offset=frame.get("offset"))
        root.addWidget(self.frames, 1)
        row = QHBoxLayout()
        row.addWidget(button("Add frame", lambda: self.add_frame()))
        row.addWidget(button("Remove frame", self.remove_frame, "quiet"))
        row.addStretch()
        root.addLayout(row)
        self.error = label("", "notice", True)
        self.error.hide()
        root.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", self.reject, "quiet"))
        row.addWidget(button("Apply details", self.save_details, "primary"))
        root.addLayout(row)

    def update_rotation_bounds(self):
        self.rotation_bounds.setRowCount(self.rotations.currentData())
        for row in range(self.rotation_bounds.rowCount()):
            identity = QTableWidgetItem(str(row))
            identity.setFlags(identity.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.rotation_bounds.setItem(row, 0, identity)
            for column in (1, 2):
                if self.rotation_bounds.item(row, column) is None:
                    self.rotation_bounds.setItem(row, column, QTableWidgetItem(""))
    def add_frame(self, values=None, *, offset=None):
        values = values or [0, 0, 0, 16, 16, 150]
        row = self.frames.rowCount()
        self.frames.insertRow(row)
        for column, value in enumerate(values):
            self.frames.setItem(row, column, QTableWidgetItem(str(value)))
        if offset is not None:
            self.frames.item(row, 0).setData(Qt.ItemDataRole.UserRole, list(offset))

    def remove_frame(self):
        if self.frames.currentRow() >= 0:
            self.frames.removeRow(self.frames.currentRow())

    def choose_texture(self):
        selected, _ = QFileDialog.getOpenFileName(self, "Choose furniture preview texture", "", "PNG images (*.png)")
        if selected:
            self.texture_source = selected
            self.texture_label.setText(Path(selected).name)

    def save_details(self):
        try:
            frames = []
            for row in range(self.frames.rowCount()):
                values = [int(self.frames.item(row, column).text()) for column in range(6)]
                frame = {"rotation": values[0], "rect": values[1:5], "duration_ms": values[5]}
                offset = self.frames.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if offset is not None:
                    frame["offset"] = list(offset)
                frames.append(frame)
            rotation_footprints = {}
            for row in range(self.rotation_bounds.rowCount()):
                width = self.rotation_bounds.item(row, 1).text().strip()
                height = self.rotation_bounds.item(row, 2).text().strip()
                if width or height:
                    rotation_footprints[str(row)] = [int(width), int(height)]
            candidate = {**self.original, "id": self.item_id.text().strip(), "name": self.name.text().strip(),
                         "kind": self.kind.text().strip(), "footprint": [self.width.value(), self.height.value()],
                         "rotations": self.rotations.currentData(), "dependency": self.dependency.text().strip(),
                         "frames": frames, "rotation_footprints": rotation_footprints}
            self.result_definition = validate_definition(candidate)
            self.accept()
        except (ValueError, TypeError, AttributeError) as exc:
            self.error.setText(str(exc) or "Use whole numbers in every frame cell.")
            self.error.show()


class InteriorEditor(QDialog):
    draft_changed = Signal()

    def __init__(self, project_file, design=None, kind="residence", parent=None, *, resident_name="", allow_rebase=False, validate_layout=None, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        self._disposed = False
        self._last_emitted_draft = None
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.project_file = Path(project_file)
        self.result_design = None
        self.allow_rebase = allow_rebase
        self.validate_layout = validate_layout
        self.result_room_translations = {}
        self._room_offsets = {}
        self._offsets_undo, self._offsets_redo = [], []
        self.selected_partition = ""
        self.selected_architecture = ""
        self.selected_architecture_piece = ""
        self.draft = InteriorDraft(deepcopy(design), kind=kind)
        # Upgrade only this staged draft. Cancel leaves saved arrival positions
        # and the source project untouched.
        self.draft.data = ensure_doorway(self.draft.data)
        self._temporary = tempfile.TemporaryDirectory(prefix="pixelheart-interior-")
        self.stage_root = Path(self._temporary.name)
        for reference in set(_asset_references(self.draft.data)):
            source = asset_path(reference, self.project_file.parent)
            if source.is_file():
                _write_new_file(asset_path(reference, self.stage_root), _read_asset(source))
        if self.draft.data.get("room_frame") and self.draft.data["atlas"]["asset"]:
            from pixelheart_core.interior_surface_design import stage_partition_frame
            reference = asset_path(self.draft.data["atlas"]["asset"], self.stage_root)
            if reference.is_file():
                self.draft.data = stage_partition_frame(self.draft.data, self.stage_root)
        self.settings = game_import_settings()
        self.selected_furniture = ""
        self.selected_catalog = ""
        self.placement_rotation = 0
        self.selected_surface = ""
        self.elapsed_ms = 0
        self._started = 0.0
        self._playing_base = 0
        self._refreshing = False
        self._auto_fit = True
        self._fitting = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_visible_room)
        self._catalog_signature = None
        self._surface_signature = None
        self.favorites = set(self.settings.value("interiors/favorites", [], type=list))
        self.recent = []
        spouse = self.draft.data["kind"] == "spouse"
        self.setWindowTitle("Decorate their spouse room — Pixelheart" if spouse else "Decorate their home — Pixelheart")
        self.resize(1320, 880)
        self.setMinimumSize(*(680, 480) if embedded else (1000, 700))
        root = QVBoxLayout(self)
        root.setContentsMargins(*(0, 0, 0, 0) if embedded else (20, 18, 20, 14))
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = (resident_name + "’s " if resident_name else "Their ") + ("spouse room" if spouse else "home")
        if not embedded:
            titles.addWidget(label(title, "profileName"))
        self.artwork_status = label("", "muted", True)
        titles.addWidget(self.artwork_status)
        heading.addLayout(titles, 1)
        self.library_button = button("Connect game library…", self.connect_library)
        heading.addWidget(self.library_button)
        self.change_library_button = button("Change library…", self.change_library, "quiet")
        heading.addWidget(self.change_library_button)
        root.addLayout(heading)

        # The hidden tool model also preserves the editor's programmatic contract.
        # People choose visible actions; no technical tool dropdown is required.
        self.tool = QComboBox(self)
        for title, value in (("Move furniture", "select"), ("Place furniture", "place"),
                             ("Move rooms", "room-select"), ("Draw a room", "room"),
                             ("Select walls", "wall-select"),
                             ("Draw hallway", "corridor"), ("Draw wall", "partition"),
                             ("Place opening", "opening"), ("Choose a finish", "surface"),
                             ("Move pieces", "architecture-select"), ("Place piece", "architecture-place")):
            self.tool.addItem(title, value)
        self.tool.addItem("Choose standing spot" if spouse else "Move doorway", "spouse_stand" if spouse else "entry")
        self.tool.hide()
        self.tool.currentIndexChanged.connect(self.change_tool)
        toolbar = QHBoxLayout()
        self.move_tool = button("Move furniture", self.cancel_tool)
        self.move_tool.setToolTip("Click an item to select it. Drag to move it.")
        toolbar.addWidget(self.move_tool)
        self.undo_button = button("Undo", self.undo)
        self.redo_button = button("Redo", self.redo)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        toolbar.addStretch()
        self.play = button("Play", lambda: None)
        self.play.setCheckable(True)
        self.play.toggled.connect(self.toggle_playback)
        self.play.setToolTip("Preview animated decorations")
        toolbar.addWidget(self.play)
        self.zoom = QComboBox()
        for scale in (1, 2, 3, 4):
            self.zoom.addItem(f"{scale * 100}%", scale)
        self.zoom.setCurrentIndex(2)
        self.zoom.setAccessibleName("Room zoom")
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        toolbar.addWidget(self.zoom)
        toolbar.addWidget(button("Fit room", self.fit_room))
        self.grid = QCheckBox("Grid")
        self.grid.setChecked(False)
        self.grid.toggled.connect(lambda *_: self.render())
        toolbar.addWidget(self.grid)
        root.addLayout(toolbar)

        self.advanced = QDialog(self)
        self.advanced.setWindowTitle("Advanced interior tools — Pixelheart")
        self.advanced.resize(640, 760)
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.addWidget(label("Advanced tools", "profileName"))
        advanced_layout.addWidget(label("For custom assets and precise map settings. Decorating works without these fields.", "muted", True))
        self.advanced_tabs = QTabWidget()
        advanced_layout.addWidget(self.advanced_tabs, 1)
        advanced_layout.addWidget(button("Back to decorating", self.advanced.accept, "primary"))

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(346)
        self.tabs.setMaximumWidth(380)
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { padding: 12px 8px; min-width: 0px; }")
        self._build_furniture()
        self._build_surfaces()
        self._build_rooms()
        self._build_animation()
        self.tabs.currentChanged.connect(self.change_mode)
        split.addWidget(self.tabs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        preview_controls = QHBoxLayout()
        preview_controls.addWidget(label("Time", "hint"))
        self.preview_time = QComboBox()
        for title, value in (("Day", "day"), ("Evening", "evening"), ("Night", "night")):
            self.preview_time.addItem(title, value)
        self.preview_time.setAccessibleName("Preview time of day")
        self.preview_time.setToolTip("Preview the room at a different time of day")
        preview_controls.addWidget(self.preview_time)
        preview_controls.addSpacing(12)
        preview_controls.addWidget(label("Lights", "hint"))
        self.preview_lights = QComboBox()
        for title, value in (("Auto", "auto"), ("On", "on"), ("Off", "off")):
            self.preview_lights.addItem(title, value)
        self.preview_lights.setAccessibleName("Preview lights")
        self.preview_lights.setToolTip("Auto turns lights on in the evening and at night")
        preview_controls.addWidget(self.preview_lights)
        preview_controls.addStretch()
        self.preview_time.currentIndexChanged.connect(lambda *_: self.render())
        self.preview_lights.currentIndexChanged.connect(lambda *_: self.render())
        right_layout.addLayout(preview_controls)
        self.farmhouse_caption = label("", "hint", True)
        self.farmhouse_caption.setVisible(spouse)
        right_layout.addWidget(self.farmhouse_caption)
        self.canvas = InteriorCanvas(self.draft, root=self.stage_root)
        self.canvas.catalogue_source = self.catalog_list
        self.canvas.furniture_validator = self.checked_layout
        self.canvas.place_catalog_drop = self.place_catalog_drop
        self.canvas.room_catalogue_source = self.room_preset
        self.canvas.room_dimensions = lambda: self.room_size.currentData()
        self.canvas.room_top_edge_origin = self.raised_room_top_edge_origin
        self.canvas.room_candidate = lambda *args: self.room_candidate(*args, preview=True)
        self.canvas.place_room_drop = self.place_room_drop
        self.canvas.room_moved.connect(self.move_room)
        self.canvas.room_resize_candidate = lambda *args: self.resize_candidate(*args, preview=True)
        self.canvas.room_resized.connect(self.resize_room)
        self.canvas.corridor_candidate = lambda *args: self.hallway_candidate(*args, preview=True)
        self.canvas.corridor_drawn.connect(self.draw_hallway)
        self.canvas.partition_candidate = lambda *args: self.wall_candidate(*args, preview=True)
        self.canvas.partition_thickness = self.wall_thickness
        self.canvas.partition_drawn.connect(self.draw_partition)
        self.canvas.partition_selected.connect(self.select_partition_on_canvas)
        self.canvas.opening_candidate = lambda x, y: self.opening_at_candidate(x, y, preview=True)
        self.canvas.doorway_candidate = lambda x, y: self.checked_layout(place_doorway(self.canvas._collision_candidate(), x, y))
        self.canvas.doorway_moved.connect(self.move_doorway)
        self.canvas.spouse_stand_candidate = lambda x, y: self.standing_spot_candidate(x, y, preview=True)
        self.canvas.spouse_stand_moved.connect(self.move_standing_spot)
        self.canvas.architecture_candidate = lambda piece, x, y, placement=None: self.piece_candidate(piece, x, y, placement, preview=True)
        self.canvas.architecture_placed.connect(self.place_architecture)
        self.canvas.architecture_moved.connect(self.move_architecture)
        self.canvas.architecture_selected.connect(self.select_architecture)
        self.canvas.scale = self.zoom.currentData()
        self.canvas.tile_clicked.connect(self.click_tile)
        self.canvas.selected.connect(self.select_furniture)
        self.canvas.moved.connect(self.move_furniture)
        self.canvas.canceled.connect(self.cancel_tool)
        self.canvas.rotate_requested.connect(self.rotate_active)
        self.canvas.room_drawn.connect(self.draw_room)
        self.canvas.room_selected.connect(self.select_room_on_canvas)
        self.canvas.preview_changed.connect(self.preview_feedback)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setObjectName("interiorStage")
        self.canvas_scroll.setStyleSheet("QScrollArea#interiorStage { background: #292d30; border: 2px solid #8e8068; border-radius: 4px; } QScrollArea#interiorStage > QWidget > QWidget { background: #292d30; }")
        self.canvas_stage = QWidget()
        self.canvas.setParent(self.canvas_stage)
        self.canvas_scroll.setWidget(self.canvas_stage)
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_scroll.viewport().installEventFilter(self)
        right_layout.addWidget(self.canvas_scroll, 1)
        self.coordinates = label("Drag furniture from the catalogue into the room. Drag a placed piece to move it.", "hint", True)
        right_layout.addWidget(self.coordinates)
        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("card")
        selected = QHBoxLayout(self.selection_bar)
        self.selection_label = label("Nothing selected", "hint", True)
        selected.addWidget(self.selection_label, 1)
        self.rotate_button = button("Rotate ↻", self.rotate_active)
        self.duplicate_button = button("Duplicate", self.duplicate_selected)
        self.remove_button = button("Put away", self.put_away)
        for widget in (self.rotate_button, self.duplicate_button, self.remove_button):
            selected.addWidget(widget)
        right_layout.addWidget(self.selection_bar)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        self.status = label("", "notice", True)
        self.status.hide()
        root.addWidget(self.status)
        self.access_notice = label("", "notice", True)
        self.access_notice.hide()
        root.addWidget(self.access_notice)
        bottom = QHBoxLayout()
        bottom.addWidget(button("Advanced…", self.advanced.exec, "quiet"))
        self.design_summary = label("", "hint")
        bottom.addWidget(self.design_summary, 1)
        if not embedded:
            bottom.addWidget(button("Cancel", self.reject, "quiet"))
            self.save_button = button("Apply room" if spouse else "Apply home", self.save_design, "primary")
            self.save_button.setToolTip("Apply these edits to the project draft, then use Save project in the main window.")
            bottom.addWidget(self.save_button)
        root.addLayout(bottom)
        if not embedded:
            root.addWidget(label("Editing a draft · Apply returns to Home. Save project keeps your changes on disk.", "hint", True))
        self.timer = QTimer(self)
        self.timer.setInterval(75)
        self.timer.timeout.connect(self.advance_animation)
        self.shortcuts = []
        for sequence, callback in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            if embedded:
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        for sequence, callback in (("Delete", self.put_away), ("Backspace", self.put_away),
                                   ("R", self.rotate_active), ("D", self.duplicate_selected)):
            shortcut = QShortcut(QKeySequence(sequence), self.canvas)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        for sequence in ("Delete", "Backspace"):
            shortcut = QShortcut(QKeySequence(sequence), self.partition_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self.remove_partition)
            self.shortcuts.append(shortcut)
        escape = QShortcut(QKeySequence("Escape"), self)
        if embedded:
            escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.canvas.cancel_interaction)
        self.shortcuts.append(escape)
        self.finished.connect(self._finish)
        self.refresh()
        if not self.draft.data["catalog"] or (spouse and not self.draft.data.get("spouse_context")) or (self.draft.data["kind"] == "residence"
                and self.draft.data.get("surfaces")
                and not {"door_left", "door_right"} <= self.draft.data.get("room_frame", {}).keys()):
            saved_library = self.remembered_library()
            if isinstance(saved_library, str) and saved_library:
                candidates = self.remembered_library_candidates()
                if candidates:
                    if not self.draft.data["catalog"]:
                        self.load_catalog(candidates[0])
                    elif spouse:
                        self.load_spouse_context(candidates[0])
                    else:
                        self.load_room_frame(candidates[0])
        self.schedule_fit()

    def _tab(self, title, *, advanced=False):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        (self.advanced_tabs if advanced else self.tabs).addTab(scroll, title)
        return content, layout

    def _build_surfaces(self):
        _, layout = self._tab("Walls && floors")
        self.surface_kind = QComboBox()
        self.surface_kind.addItem("Wallpaper", "wall")
        self.surface_kind.addItem("Flooring", "floor")
        self.surface_kind.currentIndexChanged.connect(self.refresh_surfaces)
        layout.addWidget(self.surface_kind)
        self.surface_list = QListWidget()
        self._configure_gallery(self.surface_list, "Wallpaper and flooring swatches")
        self.surface_list.currentItemChanged.connect(self.choose_surface)
        self.surface_list.itemClicked.connect(self.choose_surface)
        layout.addWidget(self.surface_list, 1)
        self.surface_empty = label("Connect your game library to browse wallpaper and flooring. Each swatch dresses the whole room.", "muted", True)
        layout.addWidget(self.surface_empty)
        layout.addWidget(label("Pick a finish, then click a room to apply it.", "hint", True))
        self.surface_all = button("Apply to every room", self.apply_surface_all)
        layout.addWidget(self.surface_all)
        _, custom = self._tab("Tile art", advanced=True)
        custom.addWidget(label("Use a custom tilesheet instead of the game library.", "muted", True))
        custom.addWidget(button("Choose tilesheet PNG…", self.choose_atlas))
        self.style_target = QComboBox()
        for name, key in (("Floor", "floor"), ("Wall · top", "wall_top"), ("Wall · middle", "wall_middle"), ("Wall · bottom", "wall_bottom")):
            self.style_target.addItem(name, key)
        self.style_target.currentIndexChanged.connect(self.show_style_tile)
        custom.addWidget(self.style_target)
        self.style_tile = label("Tile 0", "hint")
        custom.addWidget(self.style_tile)
        self.palette = InteriorPalette()
        self.palette.selected.connect(self.apply_tile)
        scroll = QScrollArea()
        scroll.setWidget(self.palette)
        custom.addWidget(scroll, 1)

    def _build_rooms(self):
        _, layout = self._tab("Rooms")
        self.layout_mode = QComboBox()
        self.layout_mode.addItem("Rooms & hallways", "rooms")
        self.layout_mode.addItem("Walls & openings", "walls")
        self.layout_mode.addItem("Architectural pieces", "architecture")
        self.layout_mode.setAccessibleName("Floorplan tools")
        self.layout_mode.currentIndexChanged.connect(self.change_layout_mode)
        layout.addWidget(self.layout_mode)
        self.room_list = QListWidget()
        self.room_list.setAccessibleName("Choose a room")
        self.room_list.setFixedHeight(64)
        self.room_list.currentItemChanged.connect(self.room_selection_changed)
        layout.addWidget(self.room_list)
        self.room_controls = QWidget()
        controls = QVBoxLayout(self.room_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(label("Add a room", "sectionTitle"))
        self.room_name = QLineEdit("New room")
        self.room_name.setPlaceholderText("Room name")
        self.room_name.setAccessibleName("New room name")
        controls.addWidget(self.room_name)
        self.room_type = QComboBox()
        self.room_type.addItem("Ordinary", "ordinary")
        self.room_type.addItem("Raised + steps", "raised")
        self.room_type.setAccessibleName("New room type")
        self.room_type.currentIndexChanged.connect(self.change_room_type)
        room_options = QHBoxLayout()
        room_options.addWidget(self.room_type, 1)
        self.room_size = QComboBox()
        for name, dimensions in (("4 × 4 tiles", (4, 4)), ("6 × 6 tiles", (6, 6)), ("8 × 6 tiles", (8, 6))):
            self.room_size.addItem(name, dimensions)
        self.room_size.setCurrentIndex(1)
        self.room_size.setAccessibleName("New room size")
        for combo in (self.room_type, self.room_size):
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(5)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        room_options.addWidget(self.room_size, 1)
        controls.addLayout(room_options)
        self.room_preset = RoomPreset(self)
        controls.addWidget(self.room_preset)
        self.room_size.currentIndexChanged.connect(lambda *_: self.room_preset.update())
        draw_actions = QHBoxLayout()
        draw_actions.addWidget(button("Draw a room…", lambda: self.set_tool("room"), "quiet"))
        draw_actions.addWidget(button("Draw hallway", lambda: self.set_tool("corridor"), "quiet"))
        controls.addLayout(draw_actions)
        self.room_optional = QCheckBox("Optional expansion")
        self.room_optional.setToolTip("Offer this room as an optional expansion players can enable in game.")
        self.room_optional.setChecked(False)
        controls.addWidget(self.room_optional)
        controls.addWidget(button("Remove selected room", self.remove_room, "quiet"))
        layout.addWidget(self.room_controls)
        self.wall_controls = QWidget()
        walls = QVBoxLayout(self.wall_controls)
        walls.setContentsMargins(0, 0, 0, 0)
        walls.addWidget(label("Interior walls", "sectionTitle"))
        walls.addWidget(label("Drag a straight wall inside a room. Leave an opening for a doorway or a wide passage.", "hint", True))
        self.wall_type = QComboBox()
        self.wall_type.addItem("Room wall", "room")
        self.wall_type.addItem("Slim divider", "slim")
        self.wall_type.setAccessibleName("Interior wall type")
        self.wall_type.setToolTip("Room walls use the game's closed-wall cutaway. Slim dividers occupy one tile.")
        self.wall_type.currentIndexChanged.connect(self.change_wall_type)
        walls.addWidget(self.wall_type)
        walls.addWidget(button("Draw wall", lambda: self.set_tool("partition")))
        self.partition_list = QListWidget()
        self.partition_list.setAccessibleName("Interior walls")
        self.partition_list.setMaximumHeight(160)
        self.partition_list.currentItemChanged.connect(self.partition_selection_changed)
        walls.addWidget(self.partition_list)
        opening_row = QHBoxLayout()
        opening_row.addWidget(label("Opening", "hint"))
        self.opening_width = QComboBox()
        for caption, width in (("Solid wall", 0), ("Doorway · 1 tile", 1), ("Wide · 2 tiles", 2), ("Archway · 3 tiles", 3)):
            self.opening_width.addItem(caption, width)
        self.opening_width.setCurrentIndex(1)
        self.opening_width.setAccessibleName("Wall opening width")
        self.opening_width.currentIndexChanged.connect(self.change_opening_width)
        opening_row.addWidget(self.opening_width, 1)
        walls.addLayout(opening_row)
        self.opening_button = button("Place opening", self.start_opening)
        walls.addWidget(self.opening_button)
        self.remove_wall_button = button("Remove selected wall", self.remove_partition, "quiet")
        walls.addWidget(self.remove_wall_button)
        self.wall_controls.hide()
        layout.addWidget(self.wall_controls)
        self.architecture_panel = ArchitecturePanel(self.stage_root, self._configure_gallery)
        self.architecture_panel.picked.connect(self.begin_architecture_placement)
        self.architecture_panel.connect_requested.connect(self.connect_library)
        self.architecture_panel.visible_pieces_changed.connect(self.architecture_filter_changed)
        self.architecture_panel.issue_selected.connect(self.review_architecture_placement)
        self.architecture_panel.hide()
        layout.addWidget(self.architecture_panel, 1)
        spouse = self.draft.data["kind"] == "spouse"
        self.layout_mode.setVisible(not spouse)
        self.room_controls.setVisible(not spouse)
        self.layout_help = label("Drag the heart to move their standing spot. Keep a clear path from the bedroom opening on the left. The surrounding farmhouse is preview only." if spouse else "Drag rooms to move. Drag selected edges to resize.", "hint", True)
        layout.addWidget(self.layout_help)
        self.entrance_button = button("Choose their standing spot" if spouse else "Move the doorway", lambda: self.set_tool("spouse_stand" if spouse else "entry"))
        layout.insertWidget(1, self.entrance_button)
        layout.addStretch()
        _, precise = self._tab("Dimensions", advanced=True)
        self.canvas_size_controls = QWidget()
        size_layout = QHBoxLayout(self.canvas_size_controls)
        self.canvas_width = _number(6, 96, self.draft.data["width"])
        self.canvas_height = _number(6, 96, self.draft.data["height"])
        size_layout.addWidget(self.canvas_width)
        size_layout.addWidget(label("×"))
        size_layout.addWidget(self.canvas_height)
        size_layout.addWidget(button("Resize canvas", self.resize_canvas))
        precise.addWidget(self.canvas_size_controls)
        self.canvas_size_controls.setVisible(not spouse)
        self.room_fields = {"x": _number(0, 95, 12), "y": _number(3, 95, 5),
                            "width": _number(2, 64, 8), "height": _number(2, 64, 8)}
        dimensions = QWidget()
        form = QFormLayout(dimensions)
        for key, widget in self.room_fields.items():
            widget.setAccessibleName("New room " + key)
            form.addRow(key.title(), widget)
        form.addRow(button("Add exact room", self.add_room))
        dimensions.setVisible(not spouse)
        precise.addWidget(dimensions)
        self.selected_x, self.selected_y = _number(), _number()
        self.move_button = button("Move selected furniture", self.move_selected)
        form = QFormLayout()
        form.addRow("Furniture X", self.selected_x)
        form.addRow("Furniture Y", self.selected_y)
        form.addRow(self.move_button)
        precise.addLayout(form)
        precise.addStretch()

    @staticmethod
    def _configure_gallery(widget, name):
        widget.setViewMode(QListView.ViewMode.IconMode)
        widget.setResizeMode(QListView.ResizeMode.Adjust)
        widget.setMovement(QListView.Movement.Static)
        widget.setIconSize(QSize(64, 64))
        widget.setGridSize(QSize(94, 112))
        widget.setItemDelegate(CatalogueTile(widget))
        widget.setWordWrap(True)
        widget.setSpacing(3)
        widget.setUniformItemSizes(True)
        widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setAccessibleName(name)
        widget.setStyleSheet("QListWidget { background: #f6eedb; border: 1px solid #c6b38f; border-radius: 4px; padding: 4px; } QListWidget::item { border: 1px solid #ded0b3; border-radius: 3px; background: #fffbf0; padding: 4px; } QListWidget::item:selected { background: #e5e9d5; border: 2px solid #6b8051; color: #354425; } QListWidget::item:hover { background: #f0e4c7; }")

    def _build_furniture(self):
        _, layout = self._tab("Furnish")
        self.library_welcome = QFrame()
        self.library_welcome.setObjectName("card")
        welcome = QVBoxLayout(self.library_welcome)
        welcome.addWidget(label("Bring your game into the room", "sectionTitle", True))
        welcome.addWidget(label("Browse the furniture, wallpaper and floors from your own Stardew Valley and installed mods.", "muted", True))
        welcome.addWidget(button("Connect game library…", self.connect_library, "primary"))
        welcome.addWidget(label("One-time setup. Your artwork stays on this computer.", "hint", True))
        layout.addWidget(self.library_welcome)
        self.plan_first = button("Shape the room first →", lambda: self.tabs.setCurrentIndex(2), "quiet")
        layout.addWidget(self.plan_first)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a chair, rug, lamp…")
        self.search.setAccessibleName("Search furniture")
        self.search.textChanged.connect(self.refresh_catalog)
        layout.addWidget(self.search)
        self.category = QComboBox()
        for name, value in (("All furniture", "all"), ("♥ Favorites", "favorites"), ("Recently used", "recent"),
                            ("Seating", "seating"), ("Tables", "tables"), ("Beds", "beds"), ("Storage", "storage"),
                            ("Rugs", "rugs"), ("Lights", "lights"), ("Windows", "windows"),
                            ("Wall decorations", "wall"), ("Other decorations", "decor")):
            self.category.addItem(name, value)
        self.category.setAccessibleName("Furniture category")
        self.category.currentIndexChanged.connect(self.refresh_catalog)
        layout.addWidget(self.category)
        self.collection = QComboBox()
        self.collection.addItem("All collections", "")
        self.collection.setAccessibleName("Furniture collection")
        self.collection.currentIndexChanged.connect(self.refresh_catalog)
        layout.addWidget(self.collection)
        self.catalog_list = FurnitureCatalogue(self)
        self._configure_gallery(self.catalog_list, "Furniture catalogue: drag a piece into the room, or click to pick it up")
        self.catalog_list.setDragEnabled(True)
        self.catalog_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.catalog_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.catalog_list.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        self.catalog_list.currentItemChanged.connect(self.choose_catalog_item)
        self.catalog_list.itemClicked.connect(self.catalog_list.pick_up)
        layout.addWidget(self.catalog_list, 1)
        self.catalog_details = label("Drag a piece into the room, or click to pick it up.", "hint", True)
        self.catalog_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.catalog_details)
        self.favorite_button = button("♡ Favorite", self.toggle_favorite, "quiet")
        layout.addWidget(self.favorite_button)
        _, advanced = self._tab("Furniture data", advanced=True)
        advanced.addWidget(button("Import library file…", self.choose_catalog))
        advanced.addWidget(button("Attach exported textures folder…", self.choose_texture_folder))
        advanced.addWidget(button("Add item metadata…", lambda: self.edit_definition(new=True)))
        advanced.addWidget(button("Edit selected item metadata…", self.edit_definition))
        advanced.addWidget(label("Only needed for a custom source that the connected game library cannot resolve.", "hint", True))
        advanced.addStretch()

    def _build_animation(self):
        _, layout = self._tab("Animation", advanced=True)
        layout.addWidget(label("Animate a room tile using frames from the same tilesheet.", "muted", True))
        self.animation_list = QListWidget()
        self.animation_list.setAccessibleName("Animated room tiles")
        self.animation_list.setMaximumHeight(180)
        self.animation_list.currentItemChanged.connect(self.select_animation)
        layout.addWidget(self.animation_list)
        form = QFormLayout()
        self.animation_tile = _number(0, 16383)
        self.animation_frames = QLineEdit()
        self.animation_frames.setPlaceholderText("e.g. 5, 6, 7, 6")
        self.animation_duration = _number(16, 10000, 150)
        self.animation_duration.setSuffix(" ms")
        form.addRow("Animated tile ID", self.animation_tile)
        form.addRow("Frame tile IDs", self.animation_frames)
        form.addRow("Duration per frame", self.animation_duration)
        layout.addLayout(form)
        layout.addWidget(button("Apply tile animation", self.apply_animation))
        layout.addWidget(button("Remove selected animation", self.remove_animation, "quiet"))
        layout.addWidget(label("Use Play animation above to preview. Furniture preview frames are configured in that item's details; its installed mod supplies the in-game animation.", "hint", True))
        layout.addStretch()

    def set_tool(self, value):
        index = self.tool.findData(value)
        if self.tool.currentIndex() == index:
            self.change_tool()
        else:
            self.tool.setCurrentIndex(index)
        self.canvas.setFocus()

    def cancel_tool(self):
        self.canvas.clear_placement()
        room_mode = self.tabs.currentIndex() == 2 and self.draft.data["kind"] == "residence"
        architecture = room_mode and self.layout_mode.currentData() == "architecture"
        walls = room_mode and self.layout_mode.currentData() == "walls"
        self.set_tool("architecture-select" if architecture else "wall-select" if walls else "room-select" if room_mode else "select")
        self.select_furniture("")
        self.select_architecture("")
        self.preview_feedback(True, "")

    def change_layout_mode(self, *_):
        if not hasattr(self, "wall_controls"):
            return
        walls = self.layout_mode.currentData() == "walls"
        architecture = self.layout_mode.currentData() == "architecture"
        self.room_controls.setVisible(not walls and not architecture and self.draft.data["kind"] == "residence")
        self.room_list.setVisible(not walls and not architecture)
        self.wall_controls.setVisible(walls)
        self.architecture_panel.setVisible(architecture)
        self.entrance_button.setVisible(True)
        if hasattr(self, "canvas"):
            self.cancel_tool()
            self.move_tool.setText("Move pieces" if architecture else "Select walls" if walls else "Move rooms")
            self.move_tool.setToolTip("Drag a built-in piece to move it." if architecture else "Select a wall to adjust its opening." if walls else "Drag a room to move it; drag its handles to resize.")
            self.layout_help.setVisible(not walls and not architecture)
            self.update_contextual_actions()
            if walls or architecture:
                self.canvas.selected_room_id = ""
            else:
                self.room_selection_changed()
            if not walls:
                self.selected_partition = ""
                self.canvas.selected_partition_id = ""
            self.rotate_button.setVisible(not architecture)
            self.remove_button.setText("Remove" if architecture else "Put away")
            self.canvas.update()

    def change_mode(self, index):
        if not hasattr(self, "canvas"):
            return
        room_mode = index == 2 and self.draft.data["kind"] == "residence"
        wall_mode = room_mode and self.layout_mode.currentData() == "walls"
        architecture = room_mode and self.layout_mode.currentData() == "architecture"
        self.move_tool.setVisible(index == 0 or room_mode)
        self.move_tool.setText("Move pieces" if architecture else "Select walls" if wall_mode else "Move rooms" if room_mode else "Move furniture")
        self.move_tool.setToolTip("Drag a built-in piece to move it." if architecture else "Select a wall to adjust its opening." if wall_mode
                                 else "Drag a room to move it; drag its handles to resize." if room_mode
                                 else "Click an item to select it. Drag to move it.")
        self.selection_bar.setVisible(index == 0 or architecture)
        self.rotate_button.setVisible(not architecture)
        self.remove_button.setText("Remove" if architecture else "Put away")
        self.cancel_tool()
        if index == 1:
            self.coordinates.setText("Pick wallpaper or flooring, then click the room you want to change.")
        elif room_mode:
            self.preview_feedback(True, "")
        elif index == 2:
            self.coordinates.setText("Drag the heart, or use Choose their standing spot to place it with a click.")

    def update_contextual_actions(self):
        if not hasattr(self, "selection_bar"):
            return
        tool = self.tool.currentData()
        self.selection_bar.setVisible(
            (self.tabs.currentIndex() == 0 and tool in ("select", "place"))
            or tool in ("architecture-select", "architecture-place"))
        self.schedule_fit()

    def center_room(self):
        if not hasattr(self, "canvas_scroll"):
            return
        rooms = [r for r in self.draft.data["rooms"] if r["enabled"]]
        left, top = min(r["x"] for r in rooms), min(r["y"] - 3 for r in rooms)
        right = max(r["x"] + r["width"] for r in rooms)
        bottom = max(r["y"] + r["height"] for r in rooms)
        if self.draft.data["kind"] == "spouse":
            left, top = 0, 0
            right, bottom = self.canvas.view_size
        cell = self.canvas.scale * 16
        viewport = self.canvas_scroll.viewport()
        cx, cy = (left + right) * cell / 2, (top + bottom) * cell / 2
        # Add only the padding needed to center the *room*, including when its
        # map coordinates are close to an edge. Mouse coordinates stay local.
        # Round spare half-pixels down: rounding both sides up makes a fitting
        # stage one pixel too large and repeatedly toggles the scrollbars.
        px, py = max(0, int(viewport.width()/2 - cx)), max(0, int(viewport.height()/2 - cy))
        rx = max(0, int(viewport.width()/2 - (self.canvas.width() - cx)))
        by = max(0, int(viewport.height()/2 - (self.canvas.height() - cy)))
        self.canvas.move(px, py)
        self.canvas_stage.setFixedSize(self.canvas.width() + px + rx, self.canvas.height() + py + by)
        self.canvas_scroll.horizontalScrollBar().setValue(round(px + cx - viewport.width() / 2))
        self.canvas_scroll.verticalScrollBar().setValue(round(py + cy - viewport.height() / 2))

    def fit_room(self):
        if not hasattr(self, "canvas_scroll"):
            return
        rooms = [r for r in self.draft.data["rooms"] if r["enabled"]]
        width = max(r["x"] + r["width"] for r in rooms) - min(r["x"] for r in rooms) + 1
        height = max(r["y"] + r["height"] for r in rooms) - min(r["y"] - 3 for r in rooms) + 1
        if self.draft.data["kind"] == "spouse":
            width, height = (dimension + 1 for dimension in self.canvas.view_size)
        viewport = self.canvas_scroll.viewport()
        scale = max(1, min(4, viewport.width() // (width * 16), viewport.height() // (height * 16)))
        self._auto_fit = True
        self._fitting = True
        self.zoom.setCurrentIndex(self.zoom.findData(scale))
        self._fitting = False
        self.center_room()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_fit()

    def eventFilter(self, watched, event):
        if (hasattr(self, "canvas_scroll") and watched is self.canvas_scroll.viewport()
                and event.type() == QEvent.Type.Resize):
            self.schedule_fit()
        return super().eventFilter(watched, event)

    def schedule_fit(self):
        """Wait for the visible viewport after layout changes, preserving manual zoom."""
        if (not self._disposed and getattr(self, "_auto_fit", False) and not self._fitting
                and hasattr(self, "canvas_scroll") and not self._fit_timer.isActive()):
            self._fit_timer.start(0)

    def _fit_visible_room(self):
        if self.isVisible() and self._auto_fit:
            self.fit_room()

    def preview_feedback(self, valid, message):
        if message:
            self.coordinates.setText(message)
        elif self.canvas.catalogue_drag:
            self.coordinates.setText("Release to place · Esc to cancel")
        elif self.tool.currentData() == "place":
            self.coordinates.setText("Click to place · Right-click or R to rotate · Esc to stop")
        elif self.tool.currentData() == "room":
            self.coordinates.setText("Draw above a lower room, leaving four tiles for steps and landings. Release to build both." if self.room_type.currentData() == "raised"
                                     else "Drag out a room beside an existing room. Release to build it.")
        elif self.tool.currentData() == "wall-select":
            self.coordinates.setText("Select a wall to adjust its opening · Delete to remove · Esc to cancel")
        elif self.tool.currentData() == "room-select":
            self.coordinates.setText("Drag rooms to move · Drag selected edges to resize · Esc to cancel")
        elif self.tool.currentData() == "corridor":
            self.coordinates.setText("Drag a narrow hallway from a room edge · Esc to cancel")
        elif self.tool.currentData() == "partition":
            self.coordinates.setText("Drag a horizontal or vertical wall inside a room · Esc to cancel")
        elif self.tool.currentData() == "opening":
            self.coordinates.setText("Click a wall to position its opening · Esc to cancel")
        elif self.tool.currentData() == "architecture-place":
            self.coordinates.setText("Click to place the piece · Esc to cancel")
        elif self.tool.currentData() == "architecture-select":
            self.coordinates.setText("Click to select · Drag to move · D to duplicate · Delete to remove")
        elif self.tool.currentData() == "entry":
            self.coordinates.setText("Choose a clear spot along the lower outside wall · Esc to cancel")
        elif self.tool.currentData() == "spouse_stand":
            self.coordinates.setText("Drag the heart or click a clear floor tile · Keep a path from the left opening · Esc to cancel")
        elif self.tool.currentData() == "select" and self.draft.data["kind"] == "spouse" and self.tabs.currentIndex() == 2:
            self.coordinates.setText("Drag the heart to move their standing spot.")
        elif self.tool.currentData() == "select":
            self.coordinates.setText("Click to select · Drag to move · R to rotate · Delete to put away")

    def connect_library(self):
        from pixelheart_core.interior_furniture import discover_furniture_libraries
        saved = self.remembered_library()
        cp = self.settings.value("localGame/contentPatcherExportFolder", "")
        candidates = (self.remembered_library_candidates() if saved
                      else discover_furniture_libraries([cp] if isinstance(cp, str) and cp else []))
        if candidates and self.load_catalog(candidates[0]):
            return
        message = (self.status.text() if candidates else
                   "The connected library is unavailable. Choose its new location, or connect another library." if saved else "")
        self.change_library(message=message)

    def remembered_library(self):
        source = self.settings.value("interiors/librarySource", "")
        if not isinstance(source, str) or not source:
            source = self.settings.value("interiors/libraryFolder", "")
        return source if isinstance(source, str) else ""

    def remembered_library_candidates(self):
        source = self.settings.value("interiors/librarySource", "")
        if isinstance(source, str) and source:
            path = Path(source).expanduser()
            return [path] if path.is_file() else []
        from pixelheart_core.interior_furniture import discover_furniture_libraries
        saved = self.remembered_library()
        return discover_furniture_libraries([saved], include_standard_paths=False) if saved else []

    def change_library(self, checked=False, *, message=""):
        from pixelheart_core.interior_furniture import resolve_furniture_library
        saved = self.settings.value("interiors/libraryFolder", "")
        setup = QDialog(self)
        setup.setWindowTitle("Connect your Stardew Valley library")
        setup.resize(540, 440)
        layout = QVBoxLayout(setup)
        layout.setSpacing(16)
        layout.addWidget(label("Your furniture. Your installed mods.", "profileName", True))
        layout.addWidget(label("Connect once, then decorate by picking pictures from the catalogue.", "muted", True))
        for text in ("1. Install Pixelheart Interiors alongside SMAPI and Content Patcher.",
                     "2. Open a save in Stardew Valley. The companion prepares your furniture and finishes automatically.",
                     "3. Choose your Stardew Valley folder here."):
            layout.addWidget(label(text, wrap=True))
        layout.addWidget(button("Library setup guide…", self.show_library_guide, "quiet"))
        if self.remembered_library():
            layout.addWidget(label("Connected source: " + self.remembered_library(), "hint", True))
        feedback = label(message, "notice", True)
        feedback.setVisible(bool(message))
        layout.addWidget(feedback)
        def choose_folder():
            directory = QFileDialog.getExistingDirectory(setup, "Choose Stardew Valley, Mods, or a Pixelheart library folder", saved if isinstance(saved, str) else "")
            if not directory:
                return
            try:
                source = resolve_furniture_library(directory)
                if not source:
                    raise ValueError("No prepared library yet. Open a save with Pixelheart Interiors installed, then choose this folder again.")
                if self.load_catalog(source):
                    self.settings.setValue("interiors/libraryFolder", directory)
                    setup.accept()
                else:
                    feedback.setText(self.status.text())
                    feedback.show()
            except (ValueError, OSError) as exc:
                feedback.setText(str(exc))
                feedback.show()
        layout.addWidget(button("Choose game folder…", choose_folder, "primary"))
        def choose_file():
            path, _ = QFileDialog.getOpenFileName(setup, "Choose a prepared Pixelheart library", "", "Library (*.json)")
            if path and self.load_catalog(path):
                self.settings.setValue("interiors/libraryFolder", str(Path(path).parent))
                setup.accept()
        layout.addWidget(button("I already have a library file…", choose_file, "quiet"))
        layout.addWidget(button("Keep planning the room", setup.reject, "quiet"))
        setup.exec()
        setup.deleteLater()

    def show_library_guide(self):
        guide = QDialog(self)
        guide.setWindowTitle("Game library setup — Pixelheart")
        guide.resize(620, 540)
        layout = QVBoxLayout(guide)
        layout.addWidget(label("Connect your local game artwork", "profileName", True))
        for text in (
            "Pixelheart Interiors is a separate SMAPI companion. This app includes its source, not a prebuilt companion. You need Stardew Valley 1.6.9 or newer, SMAPI 4.1 or newer, and Content Patcher.",
            "1. From the Pixelheart source folder, build the companion with the .NET SDK on a machine with Stardew Valley and SMAPI installed. Use the command below with your game folder.",
            "2. Put its built DLL and manifest together in a Pixelheart Interiors folder inside your game's Mods folder. Keep your exported NPC pack alongside it.",
            "3. Launch the game through SMAPI and load a save. The companion creates cache/library/library.json in its mod folder.",
            "4. Return here and choose your game folder, Mods folder, or the prepared library file. After changing game mods, load a save again and use Refresh game library.",
            "Refresh uses your connected source. Change library lets you choose another installation or snapshot. Your game artwork remains local.",
        ):
            layout.addWidget(label(text, wrap=True))
        command = QLineEdit('dotnet build runtime/Pixelheart.Interiors/Pixelheart.Interiors.csproj -c Release -p:GamePath="/path/to/Stardew Valley/game-folder"')
        command.setReadOnly(True)
        command.setAccessibleName("Companion build command; copy and replace the game path")
        command.setCursorPosition(0)
        layout.addWidget(command)
        layout.addStretch()
        layout.addWidget(button("Back to library setup", guide.accept, "primary"))
        guide.exec()
        guide.deleteLater()

    def _selected_room(self):
        selected = self.room_list.currentItem()
        identity = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        fallback = next(room for room in self.draft.data["rooms"] if room.get("kind") != "stairway")
        return next((r for r in self.draft.data["rooms"] if r["id"] == identity), fallback)

    def change_room_type(self, *_):
        raised = self.room_type.currentData() == "raised"
        self.room_preset.setToolTip("Drag above a lower room, leaving four tiles for steps and landings. Connect the native game library first." if raised
                                   else "Drag this room onto the canvas, beside an existing room.")
        self.room_preset.update()
        self.layout_help.setText("Place the raised room above a lower room, leaving four tiles for steps and landings. Connect your native game library first. The room and steps are built together." if raised
                                 else "Drag rooms to move. Drag selected edges to resize.")
        if hasattr(self, "canvas"):
            self.canvas.clear_room_interaction()
            if self.tabs.currentIndex() == 2 and self.layout_mode.currentData() == "rooms":
                self.set_tool("room-select")
                self.preview_feedback(True, "")

    def select_room_on_canvas(self, identity):
        room = next((room for room in self.draft.data["rooms"] if room["id"] == identity), None)
        if room is not None and room.get("kind") == "stairway":
            identity = room.get("upper_room_id", "")
        if identity:
            self.layout_mode.setCurrentIndex(0)
            self.selected_partition = ""
            self.canvas.selected_partition_id = ""
        for row in range(self.room_list.count()):
            if self.room_list.item(row).data(Qt.ItemDataRole.UserRole) == identity:
                self.room_list.setCurrentRow(row)
                break

    def room_selection_changed(self, *_):
        if hasattr(self, "canvas") and self.draft.data["kind"] == "spouse":
            self.canvas.selected_room_id = ""
            self.canvas.update()
            return
        if (hasattr(self, "canvas") and self.tabs.currentIndex() == 2 and not self._refreshing
                and self.layout_mode.currentData() == "rooms"):
            room = self._selected_room()
            self.canvas.selected_room_id = room["id"]
            self.canvas.selected_partition_id = ""
            self.canvas.set_room_preview(None)
            self.canvas.update()

    def _preview_data(self, preview):
        return self.canvas._collision_candidate() if preview else self.draft.data

    def _candidate_offsets(self, candidate, *, resizing=None):
        offsets = deepcopy(self._room_offsets)
        before = {r["id"]: r for r in self.draft.data["rooms"]}
        for room in candidate["rooms"]:
            previous = before.get(room["id"])
            if previous is None or room["id"] == resizing:
                continue
            dx, dy = room["x"] - previous["x"], room["y"] - previous["y"]
            if dx or dy:
                old = offsets.get(room["id"], (0, 0))
                offsets[room["id"]] = [old[0] + dx, old[1] + dy]
        return offsets

    def checked_layout(self, candidate, offsets=None):
        validate_architecture_rules(candidate, before=self.draft.data)
        validate_spouse_access(candidate, before=self.draft.data)
        if self.validate_layout is not None:
            self.validate_layout(candidate, self._room_offsets if offsets is None else offsets)
        return candidate

    def apply_layout(self, candidate, *, resizing=None):
        offsets = self._candidate_offsets(candidate, resizing=resizing)
        self.checked_layout(candidate, offsets)
        changed = self.draft.apply(candidate)
        if changed:
            self._room_offsets = offsets
        return changed

    def room_candidate(self, identity, x, y, width, height, *, preview=False, snap=True):
        if identity is None and self.room_type.currentData() == "raised":
            from pixelheart_core.interior_levels import raised_room_candidate
            candidate = raised_room_candidate(self._preview_data(preview), x, y, width, height,
                                   name=self.room_name.text().strip() or "Raised room",
                                   optional=self.room_optional.isChecked(), allow_rebase=self.allow_rebase)
        else:
            candidate = room_edit_candidate(self._preview_data(preview), room_id=identity or None,
                                   x=x, y=y, width=width, height=height,
                                   name=self.room_name.text().strip() or "New room",
                                   optional=self.room_optional.isChecked(),
                                   allow_rebase=self.allow_rebase, snap=snap)
        return self.checked_layout(candidate, self._candidate_offsets(candidate))

    def raised_room_top_edge_origin(self, x, width, height):
        if self.room_type.currentData() != "raised" or not self.allow_rebase:
            return None
        from pixelheart_core.interior_levels import STAIRWAY_HEIGHT
        lower = min((room for room in self.draft.data["rooms"] if room["enabled"]
                     and not room.get("level", 0) and room.get("kind") != "stairway"
                     and min(x+width, room["x"]+room["width"])-max(x, room["x"]) >= 2),
                    key=lambda room: room["y"], default=None)
        if lower is not None and lower["y"]-STAIRWAY_HEIGHT-height < 0:
            return x, lower["y"]-STAIRWAY_HEIGHT-height
        return None

    def resize_candidate(self, identity, x, y, width, height, *, preview=False):
        candidate = resize_room_candidate(self._preview_data(preview), identity, x, y, width, height)
        return self.checked_layout(candidate)

    def resize_room(self, identity, x, y, width, height):
        changed = self.run_change(lambda: self.apply_layout(
            self.resize_candidate(identity, x, y, width, height), resizing=identity))
        if changed:
            self.select_room_on_canvas(identity)
        return bool(changed)

    def hallway_candidate(self, x, y, width, height, *, preview=False):
        candidate = corridor_candidate(self._preview_data(preview), x, y, width, height,
                                       allow_rebase=self.allow_rebase)
        return self.checked_layout(candidate, self._candidate_offsets(candidate))

    def draw_hallway(self, x, y, width, height):
        candidate = None
        def change():
            nonlocal candidate
            candidate = self.hallway_candidate(x, y, width, height)
            return self.apply_layout(candidate)
        changed = self.run_change(change)
        if changed:
            self.select_room_on_canvas(candidate["rooms"][-1]["id"])
            self.set_tool("room-select")
        return bool(changed)

    def wall_thickness(self, axis):
        return native_partition_thickness(axis) if self.wall_type.currentData() == "room" else 1

    def wall_candidate(self, axis, x, y, length, *, preview=False):
        thickness = self.wall_thickness(axis)
        _, _, width, height = partition_rectangle(
            {"axis": axis, "x": x, "y": y, "length": length, "thickness": thickness})
        room = next((r for r in self.draft.data["rooms"] if r["enabled"]
                     and r["x"] <= x and r["y"] <= y
                     and x+width <= r["x"]+r["width"] and y+height <= r["y"]+r["height"]), None)
        if room is None:
            raise ValueError("Draw each wall inside one room, including its edges.")
        candidate = partition_candidate(self._preview_data(preview), room["id"], axis, x, y, length,
                                        opening_width=self.opening_width.currentData(), thickness=thickness)
        return self.checked_layout(candidate)

    def draw_partition(self, axis, x, y, length):
        candidate = None
        def change():
            nonlocal candidate
            candidate = self.wall_candidate(axis, x, y, length)
            return self.apply_layout(candidate)
        changed = self.run_change(change)
        if changed:
            self.select_partition_on_canvas(candidate["partitions"][-1]["id"])
            self.set_tool("wall-select")
        return bool(changed)

    def _partition_at(self, x, y):
        return self.canvas._partition_at(x, y)

    def opening_at_candidate(self, x, y, *, preview=False):
        wall = self._partition_at(x, y)
        if wall is None:
            raise ValueError("Choose a point on an interior wall.")
        width = max(1, self.opening_width.currentData())
        offset = (x-wall["x"] if wall["axis"] == "horizontal" else y-wall["y"]) - width // 2
        return self.checked_layout(opening_candidate(self._preview_data(preview), wall["id"], offset, width))

    def select_partition_on_canvas(self, identity):
        self.selected_partition = identity
        if not identity:
            self.partition_list.setCurrentRow(-1)
            self.canvas.selected_partition_id = ""
            return
        self.layout_mode.setCurrentIndex(1)
        for row in range(self.partition_list.count()):
            if self.partition_list.item(row).data(Qt.ItemDataRole.UserRole) == identity:
                self.partition_list.setCurrentRow(row)
                break
        self.partition_selection_changed()

    def partition_selection_changed(self, *_):
        if self._refreshing:
            return
        item = self.partition_list.currentItem()
        self.selected_partition = item.data(Qt.ItemDataRole.UserRole) if item else ""
        wall = next((p for p in self.draft.data.get("partitions", []) if p["id"] == self.selected_partition), None)
        self.remove_wall_button.setEnabled(wall is not None)
        self.opening_button.setEnabled(wall is not None)
        if wall is not None:
            self.wall_type.blockSignals(True)
            self.wall_type.setCurrentIndex(self.wall_type.findData("room" if wall.get("thickness", 1) > 1 else "slim"))
            self.wall_type.blockSignals(False)
            width = wall.get("openings", [{}])[0].get("width", 0) if wall.get("openings") else 0
            self.opening_width.blockSignals(True)
            self.opening_width.setCurrentIndex(self.opening_width.findData(width))
            self.opening_width.blockSignals(False)
        if hasattr(self, "canvas"):
            self.canvas.selected_partition_id = self.selected_partition
            if wall:
                self.canvas.selected_room_id = ""
            self.canvas.update()

    def change_wall_type(self, *_):
        if self._refreshing:
            return
        if hasattr(self, "canvas"):
            self.canvas.clear_room_interaction()
        wall = next((p for p in self.draft.data.get("partitions", []) if p["id"] == self.selected_partition), None)
        if wall is None:
            return
        thickness = self.wall_thickness(wall["axis"])
        if thickness == wall.get("thickness", 1):
            return
        changed = self.run_change(lambda: self.apply_layout(self.checked_layout(partition_candidate(
            self.draft.data, wall["room_id"], wall["axis"], wall["x"], wall["y"], wall["length"],
            openings=wall["openings"], partition_id=wall["id"], thickness=thickness))))
        if not changed:
            self.partition_selection_changed()

    def change_opening_width(self, *_):
        if self._refreshing:
            return
        wall = next((p for p in self.draft.data.get("partitions", []) if p["id"] == self.selected_partition), None)
        if wall is None:
            return
        width = self.opening_width.currentData()
        previous = wall.get("openings", [])
        center = (previous[0]["offset"] + previous[0]["width"] / 2) if previous else wall["length"] / 2
        offset = max(0, min(wall["length"]-width, int(center - width/2)))
        changed = self.run_change(lambda: self.apply_layout(self.checked_layout(
            opening_candidate(self.draft.data, wall["id"], offset, width))))
        if not changed:
            self.partition_selection_changed()

    def remove_partition(self):
        if self.selected_partition:
            self.run_change(lambda: self.apply_layout(remove_partition_candidate(self.draft.data, self.selected_partition)))

    def start_opening(self):
        if self.opening_width.currentData() == 0:
            self.opening_width.blockSignals(True)
            self.opening_width.setCurrentIndex(self.opening_width.findData(1))
            self.opening_width.blockSignals(False)
        self.set_tool("opening")

    def place_room_drop(self, x, y, width, height):
        candidate = None
        def change():
            nonlocal candidate
            candidate = self.room_candidate(None, x, y, width, height)
            return self.apply_layout(candidate)
        changed = self.run_change(change)
        if changed:
            self.select_room_on_canvas(next(room["id"] for room in reversed(candidate["rooms"])
                                            if room.get("kind") != "stairway"))
            self.set_tool("room-select")
            self.center_room()
        return bool(changed)

    def move_room(self, identity, x, y):
        room = next((room for room in self.draft.data["rooms"] if room["id"] == identity), None)
        if room is None:
            return False
        changed = self.run_change(lambda: self.apply_layout(
            self.room_candidate(identity, x, y, room["width"], room["height"])))
        if changed:
            self.select_room_on_canvas(identity)
        return bool(changed)

    def draw_room(self, x, y, width, height):
        if self.draft.data["kind"] != "spouse":
            self.place_room_drop(x, y, width, height)

    def refresh_surfaces(self):
        if not hasattr(self, "surface_list"):
            return
        kind = self.surface_kind.currentData()
        surfaces = [d for d in self.draft.data.get("surfaces", []) if d["kind"] == kind]
        signature = (repr(surfaces), self.draft.data["atlas"].get("asset"), kind)
        if signature == self._surface_signature:
            return
        self._surface_signature = signature
        if self.selected_surface not in {s["id"] for s in surfaces}:
            self.selected_surface = ""
            if hasattr(self, "canvas") and self.tool.currentData() == "surface":
                self.cancel_tool()
        self.surface_list.blockSignals(True)
        self.surface_list.clear()
        sheet = QPixmap(str(asset_path(self.draft.data["atlas"]["asset"], self.stage_root))) if self.draft.data["atlas"].get("asset") else QPixmap()
        columns = self.draft.data["atlas"]["columns"]
        for surface in surfaces:
            swatch = QPixmap(surface["width"] * 16, surface["height"] * 16)
            swatch.fill(Qt.GlobalColor.transparent)
            painter = QPainter(swatch)
            if columns and not sheet.isNull():
                for index, tile in enumerate(surface["tiles"]):
                    painter.drawPixmap(index % surface["width"] * 16, index // surface["width"] * 16, sheet, tile % columns * 16, tile // columns * 16, 16, 16)
            painter.end()
            item = QListWidgetItem(QIcon(swatch.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)), surface["name"])
            item.setData(Qt.ItemDataRole.UserRole, surface["id"])
            self.surface_list.addItem(item)
            if surface["id"] == self.selected_surface:
                self.surface_list.setCurrentItem(item)
        self.surface_list.blockSignals(False)
        self.surface_empty.setVisible(not surfaces)
        self.surface_all.setEnabled(bool(self.selected_surface))

    def choose_surface(self, item, previous=None):
        self.selected_surface = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.surface_all.setEnabled(bool(item))
        if item:
            self.canvas.clear_placement()
            self.set_tool("surface")
            self.coordinates.setText("Click a room to apply " + item.text() + ". Undo is always available.")

    def apply_surface_all(self):
        if self.selected_surface:
            from pixelheart_core.interior_surface_design import apply_surface
            self.run_change(lambda: self.draft.apply(apply_surface(self.draft.data, self.selected_surface)))

    def toggle_favorite(self):
        if not self.selected_catalog:
            return
        if self.selected_catalog in self.favorites:
            self.favorites.remove(self.selected_catalog)
        else:
            self.favorites.add(self.selected_catalog)
        self.settings.setValue("interiors/favorites", sorted(self.favorites))
        self.refresh_catalog()

    def begin_catalog_placement(self, item):
        self.choose_catalog_item(item)
        definition = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
        if not definition:
            return
        if not definition.get("footprint"):
            self.notice("This item's size was not supplied. Refresh the connected game library, or set it in Advanced.")
            return
        self.placement_rotation = 0
        self.canvas.set_placement(definition, 0, self.stage_root)
        self.set_tool("place")
        self.select_furniture("")
        self.coordinates.setText("Click to place · Right-click or R to rotate · Esc to stop")
        self.rotate_button.setEnabled(definition["rotations"] > 1)
        self.selection_label.setText("Placing " + definition["name"])

    def rotate_active(self):
        if self.tool.currentData().startswith("architecture-"):
            return
        if self.tool.currentData() == "place":
            definition = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
            if definition:
                self.placement_rotation = (self.placement_rotation + 1) % definition["rotations"]
                self.canvas.set_placement(definition, self.placement_rotation, self.stage_root)
        else:
            self.rotate_selected()

    def put_away(self):
        if self.tool.currentData() == "architecture-place":
            self.cancel_tool()
        elif self.tool.currentData() == "architecture-select":
            item = next((p for p in self.draft.data.get("architecture", []) if p["id"] == self.selected_architecture), None)
            if item and not self._connected_steps(item):
                self.run_change(lambda: self.apply_layout(remove_architecture_candidate(self.draft.data, self.selected_architecture)))
        elif self.tabs.currentIndex() == 2 and self.selected_partition:
            self.remove_partition()
        elif self.tool.currentData() == "place":
            self.cancel_tool()
        else:
            self.remove_selected()

    def duplicate_selected(self):
        if self.tool.currentData().startswith("architecture-"):
            item = next((p for p in self.draft.data.get("architecture", []) if p["id"] == self.selected_architecture), None)
            if item and not self._connected_steps(item):
                self.begin_architecture_placement(item["piece_id"])
            return
        item = next((d for d in self.draft.data["furniture"] if d["id"] == self.selected_furniture), None)
        if item:
            definition = next(d for d in self.draft.data["catalog"] if d["id"] == item["item_id"])
            self.selected_catalog = item["item_id"]
            self.placement_rotation = item["rotation"]
            self.canvas.set_placement(definition, self.placement_rotation, self.stage_root)
            self.select_furniture("")
            self.set_tool("place")
            self.selection_label.setText("Placing a copy of " + definition["name"])
            self.rotate_button.setEnabled(definition["rotations"] > 1)

    def notice(self, message):
        self.status.setText(str(message))
        self.status.setVisible(bool(message))

    def run_change(self, callback, *, remember=True):
        try:
            previous = self.draft.data
            previous_offsets = deepcopy(self._room_offsets)
            result = callback()
            if remember and self.draft.data is not previous:
                self._offsets_undo.append(previous_offsets)
                del self._offsets_undo[:-50]
                self._offsets_redo.clear()
            self.notice("")
            self.refresh()
            return result
        except (ValueError, OSError, TypeError) as exc:
            self.notice(str(exc))
            return None

    def refresh(self):
        self._refreshing = True
        access_issues = spouse_access_issues(self.draft.data)
        self.access_notice.setText(access_issues[0]["message"] if access_issues else "")
        self.access_notice.setVisible(bool(access_issues))
        self.farmhouse_caption.setText(
            "Farmhouse surroundings · preview only" if self.draft.data.get("spouse_context")
            else "Simplified farmhouse outline · this library has no surrounding farmhouse artwork")
        self.canvas_width.setValue(self.draft.data["width"])
        self.canvas_height.setValue(self.draft.data["height"])
        previous_room = self.room_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.room_list.currentItem() else None
        self.room_list.clear()
        for room in self.draft.data["rooms"]:
            if room.get("kind") == "stairway":
                continue
            item = QListWidgetItem(f"{room['name']} · {room['width']} × {room['height']}"
                                   + (" · raised" if room.get("level", 0) else "")
                                   + (" · optional" if room.get("optional") else ""))
            item.setData(Qt.ItemDataRole.UserRole, room["id"])
            self.room_list.addItem(item)
            if room["id"] == previous_room:
                self.room_list.setCurrentItem(item)
        self.room_list.setFixedHeight(min(96, max(48, self.room_list.count() * 26 + 8)))
        self.partition_list.clear()
        rooms = {r["id"]: r for r in self.draft.data["rooms"]}
        for wall in self.draft.data.get("partitions", []):
            name = rooms[wall["room_id"]]["name"]
            kind = "Room wall" if wall.get("thickness", 1) > 1 else "Slim divider"
            item = QListWidgetItem(f"{name} · {kind} · {wall['axis'].title()} · {wall['length']} tiles")
            item.setData(Qt.ItemDataRole.UserRole, wall["id"])
            self.partition_list.addItem(item)
            if wall["id"] == self.selected_partition:
                self.partition_list.setCurrentItem(item)
        self.animation_list.clear()
        for animation in self.draft.data["animations"]:
            item = QListWidgetItem(f"Tile {animation['tile_id']} · {len(animation['frames'])} frames")
            item.setData(Qt.ItemDataRole.UserRole, animation["tile_id"])
            self.animation_list.addItem(item)
        atlas = self.draft.data["atlas"]
        self.palette.load(atlas, self.stage_root)
        self.artwork_status.setText(("Furniture connected. Choose finishes, or add custom tile art in Advanced." if self.draft.data["catalog"] else "Plan the room now. Connect your game to start decorating.") if not atlas.get("asset") else "Pick a piece. Make it their place.")
        self.library_button.setText("Refresh game library…" if self.draft.data["catalog"] else "Connect game library…")
        self.change_library_button.setVisible(bool(self.draft.data["catalog"] or self.remembered_library()))
        self.library_welcome.setVisible(not self.draft.data["catalog"])
        self.plan_first.setVisible(not self.draft.data["catalog"])
        for widget in (self.search, self.category, self.catalog_list, self.catalog_details, self.favorite_button):
            widget.setVisible(bool(self.draft.data["catalog"]))
        room_count = sum(room.get("kind") != "stairway" for room in self.draft.data["rooms"])
        summary = f"{room_count} room{'s' if room_count != 1 else ''} · {len(self.draft.data['furniture'])} pieces of furniture"
        if self.draft.data.get("architecture"):
            summary += f" · {len(self.draft.data['architecture'])} built-in pieces"
        self.design_summary.setText(summary)
        self.undo_button.setEnabled(bool(self.draft._undo))
        self.redo_button.setEnabled(bool(self.draft._redo))
        self.refresh_surfaces()
        self.show_style_tile()
        self.refresh_catalog()
        self.architecture_panel.refresh(self.draft.data)
        self.select_furniture(self.selected_furniture)
        self.select_architecture(self.selected_architecture)
        if self.tool.currentData() == "place":
            held = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
            if held and held.get("footprint"):
                self.placement_rotation %= held["rotations"]
                self.canvas.set_placement(held, self.placement_rotation, self.stage_root)
            else:
                self.cancel_tool()
        elif self.tool.currentData() == "architecture-place":
            held = self.architecture_definition(self.selected_architecture_piece)
            if held:
                self.canvas.set_architecture_placement(held, self.stage_root)
            else:
                self.cancel_tool()
        self.canvas.refresh_size()
        self.center_room()
        self._refreshing = False
        self.render()
        self.room_selection_changed()
        self.partition_selection_changed()
        snapshot = self.draft.snapshot()
        if snapshot != self._last_emitted_draft:
            self._last_emitted_draft = snapshot
            self.draft_changed.emit()

    def preview_options(self):
        """Preview-only controls never become room data or undoable edits."""
        time_of_day = self.preview_time.currentData()
        lighting = self.preview_lights.currentData()
        return {"time_of_day": time_of_day,
                "lights_on": lighting == "on" or (lighting == "auto" and time_of_day != "day")}

    def render(self):
        from pixelheart_core.interiors import interior_background
        background = interior_background(self.draft.data)
        if getattr(self, "_stage_background", None) != background:
            self._stage_background = background
            self.canvas_scroll.setStyleSheet(
                f"QScrollArea#interiorStage {{ background: {background}; border: 2px solid #8e8068; border-radius: 4px; }} "
                f"QScrollArea#interiorStage > QWidget > QWidget {{ background: {background}; }}")
        try:
            options = self.preview_options()
            preview = render_interior(self.draft.data, self.stage_root, elapsed_ms=self.elapsed_ms,
                                      grid=self.grid.isChecked(), **options)
            self.canvas.grid = self.grid.isChecked()
            self.canvas.elapsed_ms = self.elapsed_ms
            self.canvas.time_of_day = options["time_of_day"]
            self.canvas.lights_on = options["lights_on"]
            self.canvas.set_image(preview)
            preview.close()
        except (ValueError, OSError) as exc:
            self.notice("Preview unavailable: " + str(exc))

    def change_tool(self):
        if hasattr(self, "canvas"):
            self.canvas.clear_room_interaction()
            self.canvas.tool = self.tool.currentData()
            if self.canvas.tool != "place":
                self.canvas.clear_placement()
            else:
                definition = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
                if definition and definition.get("footprint"):
                    self.canvas.set_placement(definition, self.placement_rotation, self.stage_root)
            if self.canvas.tool == "architecture-place":
                definition = self.architecture_definition(self.selected_architecture_piece)
                if definition:
                    self.canvas.set_architecture_placement(definition, self.stage_root)
            self.canvas.set_room_preview(None)
            if self.canvas.tool == "room":
                self.coordinates.setText("Drag beside a room to add space. Rooms must share an edge.")
            elif self.canvas.tool in ("room-select", "wall-select", "corridor", "partition", "opening", "architecture-place", "architecture-select"):
                self.preview_feedback(True, "")
            elif self.canvas.tool == "entry":
                self.coordinates.setText("Choose a clear spot along the lower outside wall. The doorway needs space on both sides.")
            elif self.canvas.tool == "spouse_stand":
                self.canvas.selected_room_id = ""
                self.preview_feedback(True, "")
            self.update_contextual_actions()

    def change_zoom(self):
        if hasattr(self, "canvas"):
            if not self._fitting:
                self._auto_fit = False
            self.canvas.scale = self.zoom.currentData()
            self.canvas.refresh_size()
            self.center_room()

    def choose_atlas(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an interior tilesheet", "", "PNG images (*.png)")
        if path:
            self.load_atlas(path)

    def load_atlas(self, path):
        def change():
            if self.draft.data.get("architecture"):
                raise ValueError("Remove the built-in pieces before replacing their tilesheet.")
            atlas = import_atlas(path, self.stage_root)
            candidate = self.draft.snapshot()
            candidate["atlas"] = atlas
            candidate.pop("surfaces", None)
            candidate.pop("room_styles", None)
            candidate.pop("room_frame", None)
            candidate.pop("architecture_catalog", None)
            candidate["style"] = {key: 0 for key in ("floor", "wall_top", "wall_middle", "wall_bottom")}
            candidate["animations"] = []
            self.selected_surface = ""
            self.draft.apply(candidate)
        self.run_change(change)

    def show_style_tile(self):
        if not hasattr(self, "palette"):
            return
        tile = self.draft.data["style"][self.style_target.currentData()]
        self.style_tile.setText(f"Tile {tile}")
        self.palette.selection = tile
        self.palette.update()

    def apply_tile(self, tile):
        def change():
            candidate = self.draft.snapshot()
            target = self.style_target.currentData()
            pattern = "floor_pattern" if target == "floor" else "wall_pattern"
            candidate["style"].pop(pattern, None)
            for override in candidate.get("room_styles", {}).values():
                override.pop(pattern, None)
                override.pop(target, None)
            candidate["style"][target] = tile
            self.draft.apply(candidate)
        self.run_change(change)

    def add_room(self):
        candidate = None
        def change():
            nonlocal candidate
            candidate = self.room_candidate(None, **{key: field.value() for key, field in self.room_fields.items()}, snap=False)
            return self.apply_layout(candidate)
        changed = self.run_change(change)
        if changed:
            self.select_room_on_canvas(next(room["id"] for room in reversed(candidate["rooms"])
                                            if room.get("kind") != "stairway"))
        return bool(changed)

    def resize_canvas(self):
        def change():
            if self.draft.data["kind"] == "spouse":
                return
            candidate = self.draft.snapshot()
            candidate.update(width=self.canvas_width.value(), height=self.canvas_height.value())
            self.apply_layout(candidate)
        self.run_change(change)

    def remove_room(self):
        selected = self.room_list.currentItem()
        if selected:
            def change():
                trial = InteriorDraft(self.draft.data)
                trial.remove_room(selected.data(Qt.ItemDataRole.UserRole))
                return self.apply_layout(trial.data)
            self.run_change(change)

    def choose_catalog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose exported furniture library or Data/Furniture", "", "JSON data (*.json)")
        if path:
            self.load_catalog(path)

    def load_catalog(self, path):
        warnings = []
        succeeded = False
        resource_fields = ("atlas", "catalog", "surfaces", "room_frame", "spouse_context",
                           "architecture_catalog", "architecture", "animations", "style", "room_styles")
        staged_resources = {}
        def update(candidate, imported):
            # Most edit snapshots share the same artwork. Reuse PNG staging,
            # including finish-dependent and kitchen-placement migrations, but
            # let update_resources validate every complete layout separately.
            cache_key = repr((candidate["kind"], [(key, candidate[key]) for key in resource_fields if key in candidate]))
            if cache_key in staged_resources:
                cached = staged_resources[cache_key]
                for key in resource_fields:
                    if key in cached:
                        candidate[key] = deepcopy(cached[key])
                    else:
                        candidate.pop(key, None)
                return candidate
            existing = {definition["id"]: definition for definition in candidate["catalog"]}
            for definition in imported["definitions"]:
                previous = existing.get(definition["id"], {})
                preserved = {key: previous[key] for key in ("preview_asset", "rotation_footprints", "dependency",
                                                           "collection", "description", "interaction_profiles")
                             if not definition.get(key) and previous.get(key)}
                if (not definition.get("frames") and previous.get("frames")
                        and (not definition.get("preview_asset")
                             or definition["preview_asset"] == previous.get("preview_asset"))):
                    preserved["frames"] = previous["frames"]
                if not definition.get("preview_asset") and previous.get("preview_asset"):
                    # Native metadata refreshes keep the existing atlas and
                    # its observed effects. A new atlas must supply its own
                    # effect rectangles; explicit empty metadata clears them.
                    preserved.update({key: previous[key] for key in ("preview_variants", "preview_lights")
                                      if key not in definition and key in previous})
                existing[definition["id"]] = {**definition, **preserved}
            candidate["catalog"] = list(existing.values())
            if imported.get("surfaces"):
                from pixelheart_core.interior_surface_design import stage_surface_library, apply_surface
                had_art = bool(candidate["atlas"].get("asset"))
                candidate = stage_surface_library(candidate, imported["surfaces"], self.stage_root)
                if not had_art:
                    for kind in ("wall", "floor"):
                        first = next((d for d in candidate["surfaces"] if d["kind"] == kind), None)
                        if first:
                            candidate = apply_surface(candidate, first["id"])
            if imported.get("room_frame") and candidate["kind"] == "residence":
                from pixelheart_core.interior_surface_design import stage_room_frame, stage_partition_frame
                candidate = stage_room_frame(candidate, imported["room_frame"], self.stage_root)
                candidate = stage_partition_frame(candidate, self.stage_root)
            if imported.get("spouse_context") and candidate["kind"] == "spouse":
                candidate["spouse_context"] = imported["spouse_context"]
            if imported.get("architecture"):
                candidate = stage_architecture_library(candidate, imported["architecture"], self.stage_root)
            staged_resources[cache_key] = {key: deepcopy(candidate[key]) for key in resource_fields if key in candidate}
            return candidate
        def change():
            nonlocal succeeded
            imported = import_furniture_library(path, self.stage_root)
            warnings.extend(imported["warnings"])
            self.draft.update_resources(lambda candidate: update(candidate, imported))
            succeeded = True
        self.run_change(change, remember=False)
        if succeeded:
            source = Path(path).expanduser().resolve()
            self.settings.setValue("interiors/librarySource", str(source))
            self.settings.setValue("interiors/libraryFolder", str(source.parent))
            self.change_library_button.show()
        if succeeded and warnings:
            self.notice("Library notes: " + "\n".join(warnings[:3]))
        return succeeded

    def load_spouse_context(self, path):
        """Refresh only the read-only farmhouse backdrop on older designs."""
        def change():
            imported = import_furniture_library(path, self.stage_root)
            if imported.get("spouse_context"):
                def update(candidate):
                    candidate["spouse_context"] = imported["spouse_context"]
                    return candidate
                self.draft.update_resources(update)
        self.run_change(change, remember=False)

    def load_room_frame(self, path):
        """Complete an older library-backed room without replacing its edits."""
        def update(candidate, frame):
            from pixelheart_core.interior_surface_design import stage_room_frame, stage_partition_frame
            existing = candidate.get("room_frame", {})
            candidate = stage_room_frame(candidate, frame, self.stage_root)
            # A remembered library may differ from this design's authored
            # trim. Complete missing roles without replacing existing art.
            candidate["room_frame"].update(existing)
            return stage_partition_frame(candidate, self.stage_root)
        def change():
            imported = import_furniture_library(path, self.stage_root)
            if imported.get("room_frame"):
                self.draft.update_resources(lambda candidate: update(candidate, imported["room_frame"]))
        self.run_change(change, remember=False)

    def choose_texture_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose exported furniture textures")
        if directory:
            self.load_texture_folder(directory)

    def load_texture_folder(self, directory):
        warnings = []
        def change():
            imported = import_catalog_textures(self.draft.data["catalog"], directory, self.stage_root)
            warnings.extend(imported["warnings"])
            candidate = self.draft.snapshot()
            candidate["catalog"] = imported["definitions"]
            self.draft.apply(candidate)
        self.run_change(change)
        if warnings:
            self.notice("\n".join(warnings[:4]))

    def refresh_catalog(self):
        if not hasattr(self, "catalog_list"):
            return
        query = self.search.text().casefold().strip()
        category = self.category.currentData()
        groups = {"seating": {"chair", "bench", "couch", "armchair", "stool"}, "tables": {"table", "long table", "long_table"},
                  "beds": {"bed", "double_bed", "double bed"}, "storage": {"dresser", "bookcase", "fish tank", "fishtank"},
                  "rugs": {"rug"}, "lights": {"lamp", "sconce", "fireplace", "torch"},
                  "windows": {"window"}, "wall": {"painting", "window", "sconce"}}
        definitions = self.draft.data["catalog"]
        collections = {item["collection"]["id"]: item["collection"]["name"]
                       for item in definitions if item.get("collection")}
        selected_collection = self.collection.currentData()
        options = [("All collections", "")] + [(name, identity) for identity, name in
                    sorted(collections.items(), key=lambda pair: (pair[1].casefold(), pair[0]))]
        if options != [(self.collection.itemText(i), self.collection.itemData(i)) for i in range(self.collection.count())]:
            self.collection.blockSignals(True)
            self.collection.clear()
            for name, identity in options:
                self.collection.addItem(name, identity)
            self.collection.setCurrentIndex(max(0, self.collection.findData(selected_collection)))
            self.collection.blockSignals(False)
        self.collection.setVisible(bool(collections))
        selected_collection = self.collection.currentData()
        signature = (repr(definitions), query, category, selected_collection, tuple(sorted(self.favorites)), tuple(self.recent))
        if signature == self._catalog_signature:
            return
        self._catalog_signature = signature
        previous = self.selected_catalog
        self.catalog_list.blockSignals(True)
        self.catalog_list.clear()
        if category == "recent":
            definitions = sorted(definitions, key=lambda d: self.recent.index(d["id"]) if d["id"] in self.recent else 99999)
        for definition in definitions:
            identity = definition["id"]
            if selected_collection and definition.get("collection", {}).get("id") != selected_collection:
                continue
            searchable = " ".join((definition["name"], identity, definition["kind"],
                                    self.catalog_description(definition)))
            if query and query not in searchable.casefold():
                continue
            if category == "favorites" and identity not in self.favorites or category == "recent" and identity not in self.recent:
                continue
            # Boarded Window is classified as a painting by the game. Keep
            # its placement behavior while making it discoverable as a window.
            named_window = definition["kind"] == "painting" and "window" in definition["name"].casefold()
            if category in groups and definition["kind"] not in groups[category] and not (category == "windows" and named_window):
                continue
            if category == "decor" and definition["kind"] in set().union(*groups.values()):
                continue
            item = QListWidgetItem(("♥ " if identity in self.favorites else "") + definition["name"])
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(self.catalog_description(definition))
            if definition.get("preview_asset"):
                try:
                    preview = preview_frame(definition, self.stage_root)
                    if preview is not None:
                        pixmap = QPixmap.fromImage(ImageQt(preview))
                        item.setIcon(QIcon(pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)))
                        preview.close()
                except (ValueError, OSError):
                    pass
            self.catalog_list.addItem(item)
            if identity == previous:
                self.catalog_list.setCurrentItem(item)
        self.catalog_list.blockSignals(False)
        self.choose_catalog_item(self.catalog_list.currentItem())
        if not self.catalog_list.count() and definitions:
            self.catalog_details.setText("No matches. Try a different collection, category or search.")

    @staticmethod
    def catalog_description(definition):
        parts = [definition["name"]]
        if definition.get("collection"):
            parts[0] += " · " + definition["collection"]["name"]
        if definition.get("description"):
            parts.append(definition["description"])
        for profile in definition.get("interaction_profiles", []):
            parts.append(profile["name"] + (": " + profile["description"] if profile.get("description") else ""))
        if definition.get("interaction_profiles"):
            parts.append("Room guides: A approach · S activity spot · ! required furniture · ✓ present.")
        return "\n".join(parts)

    def choose_catalog_item(self, item, previous=None):
        old_identity = self.selected_catalog
        self.selected_catalog = item.data(Qt.ItemDataRole.UserRole) if item else ""
        definition = next((entry for entry in self.draft.data["catalog"] if entry["id"] == self.selected_catalog), None)
        self.favorite_button.setEnabled(definition is not None)
        self.favorite_button.setText("♥ Favorited" if self.selected_catalog in self.favorites else "♡ Favorite")
        if definition:
            self.catalog_details.setText(self.catalog_description(definition) + ("\nDrag into the room, or click to pick up." if definition.get("footprint") else "\nSize unavailable; refresh the game library."))
        else:
            self.catalog_details.setText("Drag a piece into the room, or click to pick it up.")
        if hasattr(self, "canvas") and self.tool.currentData() == "place":
            if not definition:
                self.cancel_tool()
            elif old_identity != self.selected_catalog:
                self.placement_rotation = 0
                self.canvas.set_placement(definition, 0, self.stage_root)
                self.select_furniture("")

    def edit_definition(self, checked=False, *, new=False):
        definition = None if new else next((entry for entry in self.draft.data["catalog"] if entry["id"] == self.selected_catalog), None)
        if not new and definition is None:
            self.notice("Select a furniture item first.")
            return
        dialog = FurnitureDetails(definition, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def change():
                edited = dialog.result_definition
                if dialog.texture_source:
                    edited = attach_texture(edited, dialog.texture_source, self.stage_root)
                candidate = self.draft.snapshot()
                if definition and edited["id"] != definition["id"] and any(item["item_id"] == definition["id"] for item in candidate["furniture"]):
                    raise ValueError("Remove this item's placements before changing its item ID.")
                candidate["catalog"] = [item for item in candidate["catalog"] if item["id"] != (definition or edited)["id"]]
                candidate["catalog"].append(edited)
                self.draft.apply(candidate)
                self.selected_catalog = edited["id"]
            self.run_change(change)
        dialog.deleteLater()

    def place_catalog_drop(self, identity, x, y):
        return self.place_furniture_once(identity, x, y, 0)

    def architecture_definition(self, identity):
        return next((d for d in self.draft.data.get("architecture_catalog", []) if d["id"] == identity), None)

    def _connected_steps(self, item):
        return item is not None and any(room["id"] == item["room_id"] and room.get("kind") == "stairway"
                                        for room in self.draft.data["rooms"])

    def architecture_filter_changed(self, visible):
        if self.tool.currentData() == "architecture-place" and self.selected_architecture_piece not in visible:
            self.cancel_tool()

    def review_architecture_placement(self, identity):
        self.tabs.setCurrentIndex(2)
        self.layout_mode.setCurrentIndex(self.layout_mode.findData("architecture"))
        self.cancel_tool()
        self.select_architecture(identity)
        item = next((p for p in self.draft.data.get("architecture", []) if p["id"] == identity), None)
        if item is None:
            return
        definition = self.architecture_definition(item["piece_id"])
        cell = self.canvas.scale * 16
        self.canvas_scroll.ensureVisible(self.canvas.x() + round((item["x"] + definition["width"]/2)*cell),
                                         self.canvas.y() + round((item["y"] + definition["height"]/2)*cell),
                                         2*cell, 2*cell)
        issue = next((i for i in architecture_rule_issues(self.draft.data) if i["placement_id"] == identity), None)
        if issue:
            self.notice(issue["message"])

    def piece_candidate(self, piece_id, x, y, placement_id=None, *, preview=False):
        candidate = architecture_candidate(self._preview_data(preview), piece_id, x, y, placement_id=placement_id)
        return self.checked_layout(candidate)

    def begin_architecture_placement(self, identity):
        definition = self.architecture_definition(identity)
        if definition is None:
            return
        self.tabs.setCurrentIndex(2)
        if architecture_rule(definition) == "steps_corridor":
            self.layout_mode.setCurrentIndex(self.layout_mode.findData("rooms"))
            self.room_type.setCurrentIndex(self.room_type.findData("raised"))
            self.selected_architecture_piece = ""
            self.select_architecture("")
            self.set_tool("room-select")
            self.notice("Steps connect a raised room to the ground floor. Drag the raised-room card above a lower room; the steps and landings are added together.")
            self.preview_feedback(True, "Drag a raised room above a lower room · Leave four tiles for steps and landings")
            return
        self.layout_mode.setCurrentIndex(self.layout_mode.findData("architecture"))
        self.selected_architecture_piece = identity
        self.selected_architecture = ""
        self.select_furniture("")
        self.set_tool("architecture-place")
        self.select_architecture("")
        self.preview_feedback(True, "")

    def place_architecture(self, piece_id, x, y):
        identity = None
        def change():
            nonlocal identity
            candidate = self.piece_candidate(piece_id, x, y)
            identity = candidate["architecture"][-1]["id"]
            return self.apply_layout(candidate)
        changed = self.run_change(change)
        if changed:
            self.cancel_tool()
            self.select_architecture(identity)
        return bool(changed)

    def move_architecture(self, identity, x, y):
        item = next((p for p in self.draft.data.get("architecture", []) if p["id"] == identity), None)
        if item is None:
            return False
        changed = self.run_change(lambda: self.apply_layout(self.piece_candidate(item["piece_id"], x, y, identity)))
        if changed:
            self.select_architecture(identity)
        return bool(changed)

    def select_architecture(self, identity):
        item = next((p for p in self.draft.data.get("architecture", []) if p["id"] == identity), None)
        self.selected_architecture = identity if item else ""
        self.canvas.selected_architecture_id = self.selected_architecture
        if not self.tool.currentData().startswith("architecture-"):
            return
        definition = self.architecture_definition(item["piece_id"]) if item else None
        self.architecture_panel.show_rule(definition)
        self.selection_label.setText(definition["name"] if definition else "Click a built-in piece to select or move it")
        connected = self._connected_steps(item)
        self.duplicate_button.setEnabled(item is not None and not connected)
        self.remove_button.setEnabled(item is not None and not connected)
        self.duplicate_button.setToolTip("Add another raised room to create another connected stairway." if connected else "")
        self.remove_button.setToolTip("Remove the raised room to remove its connected steps." if connected else "")
        self.rotate_button.setEnabled(False)
        if self.tool.currentData() == "architecture-place":
            held = self.architecture_definition(self.selected_architecture_piece)
            self.architecture_panel.show_rule(held)
            self.selection_label.setText("Placing " + held["name"] if held else "Choose an architectural piece")
            self.duplicate_button.setEnabled(False)
            self.remove_button.setEnabled(held is not None)
        self.canvas.update()

    def place_furniture_once(self, identity, x, y, rotation):
        def place():
            placed_id = self.apply_furniture_change(lambda trial: trial.place_furniture(identity, x, y, rotation))
            self.recent = [identity] + [i for i in self.recent if i != identity][:23]
            self.selected_furniture = placed_id
            return placed_id
        placed_id = self.run_change(place)
        if not placed_id:
            return False
        self.canvas.clear_placement()
        self.canvas.clear_catalogue_drag()
        self.set_tool("select")
        self.select_furniture(placed_id)
        self.preview_feedback(True, "")
        return True

    def click_tile(self, x, y):
        tool = self.tool.currentData()
        if tool == "place":
            if not self.selected_catalog:
                self.notice("Choose furniture in the Furniture tab first.")
                return
            self.place_furniture_once(self.selected_catalog, x, y, self.placement_rotation)
        elif tool == "surface" and self.selected_surface:
            from pixelheart_core.interior_surface_design import apply_surface
            room = self.canvas.room_at(x, y, include_walls=True)
            if room:
                self.run_change(lambda: self.draft.apply(apply_surface(self.draft.data, self.selected_surface, room["id"])))
            else:
                self.notice("Click inside a room to change its finish.")
        elif tool == "entry":
            self.move_doorway(x, y)
        elif tool == "opening":
            wall = self._partition_at(x, y)
            if self.run_change(lambda: self.apply_layout(self.opening_at_candidate(x, y))):
                self.select_partition_on_canvas(wall["id"])
                self.set_tool("wall-select")
        elif tool == "spouse_stand":
            self.move_standing_spot(x, y)

    def standing_spot_candidate(self, x, y, *, preview=False):
        if self.draft.data["kind"] != "spouse":
            raise ValueError("Standing spots belong to spouse rooms.")
        candidate = self.canvas._collision_candidate() if preview else self.draft.snapshot()
        candidate["spouse_stand"] = [x, y]
        return self.checked_layout(normalize_interior(candidate))

    def move_standing_spot(self, x, y):
        return self.run_change(lambda: self.draft.apply(self.standing_spot_candidate(x, y)))

    def move_doorway(self, x, y):
        if self.run_change(lambda: self.apply_layout(place_doorway(self.draft.data, x, y))):
            self.cancel_tool()

    def select_furniture(self, identity):
        selected = next((entry for entry in self.draft.data["furniture"] if entry["id"] == identity), None)
        self.selected_furniture = identity if selected else ""
        self.canvas.selected_id = self.selected_furniture
        definition = next((d for d in self.draft.data["catalog"] if selected and d["id"] == selected["item_id"]), None)
        self.selection_label.setText(definition["name"] if definition else "Click an item in the room to move or change it")
        self.duplicate_button.setToolTip("")
        self.remove_button.setToolTip("")
        self.duplicate_button.setEnabled(selected is not None)
        for widget in (self.selected_x, self.selected_y, self.move_button, self.rotate_button, self.remove_button):
            widget.setEnabled(selected is not None)
        if selected:
            self.selected_x.setValue(selected["x"])
            self.selected_y.setValue(selected["y"])
        if self.tool.currentData() == "place":
            held = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
            self.canvas.selected_id = ""
            self.selection_label.setText("Placing " + held["name"] if held else "Choose furniture from the catalogue")
            self.rotate_button.setEnabled(bool(held and held["rotations"] > 1))
            self.duplicate_button.setEnabled(False)
            self.remove_button.setEnabled(held is not None)
        self.canvas.update()

    def move_furniture(self, identity, x, y):
        self.run_change(lambda: self.apply_furniture_change(lambda trial: trial.move_furniture(identity, x, y)))

    def apply_furniture_change(self, change):
        """Validate against authored destinations before committing or recording undo."""
        trial = InteriorDraft(self.draft.data)
        result = change(trial)
        self.apply_layout(trial.data)
        return result

    def move_selected(self):
        if self.selected_furniture:
            self.move_furniture(self.selected_furniture, self.selected_x.value(), self.selected_y.value())

    def rotate_selected(self):
        if self.selected_furniture:
            self.run_change(lambda: self.apply_furniture_change(lambda trial: trial.rotate_furniture(self.selected_furniture)))

    def remove_selected(self):
        if self.selected_furniture:
            self.run_change(lambda: self.draft.remove_furniture(self.selected_furniture))

    def select_animation(self, item, previous=None):
        if item is None:
            return
        animation = next((entry for entry in self.draft.data["animations"] if entry["tile_id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        if animation:
            self.animation_tile.setValue(animation["tile_id"])
            self.animation_frames.setText(", ".join(str(frame["tile_id"]) for frame in animation["frames"]))
            self.animation_duration.setValue(animation["frames"][0]["duration_ms"])

    def apply_animation(self):
        def change():
            values = [int(value.strip()) for value in self.animation_frames.text().split(",") if value.strip()]
            if not values:
                raise ValueError("Enter at least one frame tile ID.")
            candidate = self.draft.snapshot()
            identity = self.animation_tile.value()
            candidate["animations"] = [entry for entry in candidate["animations"] if entry["tile_id"] != identity]
            candidate["animations"].append({"tile_id": identity, "frames": [{"tile_id": value, "duration_ms": self.animation_duration.value()} for value in values]})
            self.draft.apply(candidate)
        self.run_change(change)

    def remove_animation(self):
        selected = self.animation_list.currentItem()
        if selected:
            def change():
                candidate = self.draft.snapshot()
                candidate["animations"] = [entry for entry in candidate["animations"] if entry["tile_id"] != selected.data(Qt.ItemDataRole.UserRole)]
                self.draft.apply(candidate)
            self.run_change(change)

    def toggle_playback(self, playing):
        if playing:
            self._playing_base = self.elapsed_ms
            self._started = time.monotonic()
            self.timer.start()
        else:
            self.timer.stop()
        self.play.setText("Pause" if playing else "Play")

    def advance_animation(self):
        self.elapsed_ms = self._playing_base + int((time.monotonic() - self._started) * 1000)
        self.render()

    def undo(self):
        self.canvas.clear_room_interaction()
        def change():
            if not self.draft.undo():
                return False
            self._offsets_redo.append(deepcopy(self._room_offsets))
            self._room_offsets = self._offsets_undo.pop() if self._offsets_undo else {}
            return True
        self.run_change(change, remember=False)

    def redo(self):
        self.canvas.clear_room_interaction()
        def change():
            if not self.draft.redo():
                return False
            self._offsets_undo.append(deepcopy(self._room_offsets))
            self._room_offsets = self._offsets_redo.pop() if self._offsets_redo else {}
            return True
        self.run_change(change, remember=False)

    def prepare_design(self, project_file=None):
        """Validate and publish referenced assets without closing the workspace."""
        created = []
        self.result_design = None
        target_project = Path(project_file) if project_file is not None else self.project_file
        try:
            design = self.draft.snapshot()
            validate_spouse_access(design)
            issues = architecture_rule_issues(design)
            if issues:
                self.review_architecture_placement(issues[0]["placement_id"])
                raise ValueError(issues[0]["message"])
            self.checked_layout(design)
            if design["kind"] == "residence" and "doorway" not in design:
                raise ValueError("Place a doorway along a clear lower outside wall before saving the home.")
            pending = []
            for reference in sorted(set(_asset_references(design))):
                source = asset_path(reference, self.stage_root)
                if not source.is_file():
                    raise ValueError("A referenced interior texture is missing. Attach it again before saving.")
                payload = _read_asset(source)
                destination = asset_path(reference, target_project.parent)
                if destination.is_symlink():
                    raise ValueError("An interior texture destination is a symlink.")
                if destination.exists() and _read_asset(destination) != payload:
                    raise ValueError("An interior texture destination has different contents; it was not overwritten.")
                pending.append((destination, payload, destination.exists()))
            for destination, payload, existed in pending:
                _write_new_file(destination, payload)
                if not existed:
                    created.append(destination)
            self.result_design = design
            self.result_room_translations = deepcopy(self._room_offsets)
            self.project_file = target_project
            return True
        except (ValueError, OSError) as exc:
            for destination in created:
                destination.unlink(missing_ok=True)
            self.notice(str(exc))
            return False

    def save_design(self):
        if self.prepare_design():
            self.accept()

    def dispose(self):
        """Release staged assets and timers when the owning workspace closes."""
        if self._disposed:
            return
        self._disposed = True
        self.timer.stop()
        self._fit_timer.stop()
        self._temporary.cleanup()

    def _finish(self, result):
        self.dispose()
