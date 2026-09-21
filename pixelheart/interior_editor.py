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
from PySide6.QtCore import Qt, QRect, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter,
    QTabWidget, QScrollArea, QComboBox, QSpinBox, QCheckBox, QLineEdit,
    QListWidget, QListWidgetItem, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from pixelheart_core.interiors import InteriorDraft, import_atlas, render_interior, footprint
from pixelheart_core.interior_furniture import (
    import_furniture_library, validate_definition, import_catalog_textures,
    attach_texture, preview_frame,
)
from pixelheart_core.world import asset_path, _read_asset, _write_new_file
from .widgets import label, button


def _number(minimum=0, maximum=255, initial=0):
    field = QSpinBox()
    field.setRange(minimum, maximum)
    field.setValue(initial)
    return field


def _asset_references(design):
    if design.get("atlas", {}).get("asset"):
        yield design["atlas"]["asset"]
    for definition in design.get("catalog", []):
        if definition.get("preview_asset"):
            yield definition["preview_asset"]


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


class InteriorCanvas(QWidget):
    tile_clicked = Signal(int, int)
    moved = Signal(str, int, int)
    selected = Signal(str)
    hovered = Signal(int, int)

    def __init__(self, draft):
        super().__init__()
        self.draft = draft
        self.scale = 2
        self.image = QPixmap()
        self.tool = "select"
        self.selected_id = ""
        self.drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Interior canvas; select or place furniture")
        self.refresh_size()

    def refresh_size(self):
        self.setFixedSize(self.draft.data["width"] * 16 * self.scale,
                          self.draft.data["height"] * 16 * self.scale)
        self.update()

    def set_image(self, image):
        self.image = QPixmap.fromImage(ImageQt(image))
        self.update()

    def footprint(self, placed):
        definition = next((item for item in self.draft.data["catalog"] if item["id"] == placed["item_id"]), {})
        return footprint(definition, placed.get("rotation", 0))

    def _at(self, x, y):
        for placed in reversed(self.draft.data["furniture"]):
            width, height = self.footprint(placed)
            if placed["x"] <= x < placed["x"] + width and placed["y"] <= y < placed["y"] + height:
                return placed
        return None

    def _position(self, event):
        return int(event.position().x()) // (16 * self.scale), int(event.position().y()) // (16 * self.scale)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#e8e2d5"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if not self.image.isNull():
            painter.drawPixmap(self.rect(), self.image)
        cell = 16 * self.scale
        for key, caption, color in (("entry", "IN", "#61734d"), ("spouse_stand", "S", "#92693e")):
            if key == "spouse_stand" and self.draft.data["kind"] != "spouse":
                continue
            if key == "entry" and self.draft.data["kind"] == "spouse":
                continue
            x, y = self.draft.data[key]
            rect = QRect(x * cell, y * cell, cell, cell)
            tint = QColor(color)
            tint.setAlpha(140)
            painter.fillRect(rect, tint)
            painter.setPen(QColor("#fffdf5"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, caption)
        placed = next((item for item in self.draft.data["furniture"] if item["id"] == self.selected_id), None)
        if placed:
            width, height = self.footprint(placed)
            painter.setPen(QPen(QColor("#d38b36"), 2))
            painter.drawRect(placed["x"] * cell + 1, placed["y"] * cell + 1,
                             width * cell - 2, height * cell - 2)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        x, y = self._position(event)
        if self.tool == "select":
            placed = self._at(x, y)
            self.selected_id = placed["id"] if placed else ""
            self.drag = (placed["id"], x, y, placed["x"], placed["y"]) if placed else None
            self.selected.emit(self.selected_id)
            self.update()
        else:
            self.tile_clicked.emit(x, y)

    def mouseMoveEvent(self, event):
        self.hovered.emit(*self._position(event))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag:
            identity, start_x, start_y, original_x, original_y = self.drag
            self.drag = None
            x, y = self._position(event)
            if (x, y) != (start_x, start_y):
                self.moved.emit(identity, original_x + x - start_x, original_y + y - start_y)


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
            self.add_frame([frame["rotation"], *frame["rect"], frame["duration_ms"]])
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
    def add_frame(self, values=None):
        values = values or [0, 0, 0, 16, 16, 150]
        row = self.frames.rowCount()
        self.frames.insertRow(row)
        for column, value in enumerate(values):
            self.frames.setItem(row, column, QTableWidgetItem(str(value)))

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
                frames.append({"rotation": values[0], "rect": values[1:5], "duration_ms": values[5]})
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
    def __init__(self, project_file, design=None, kind="residence", parent=None):
        super().__init__(parent)
        self.project_file = Path(project_file)
        self.result_design = None
        self.draft = InteriorDraft(deepcopy(design), kind=kind)
        self._temporary = tempfile.TemporaryDirectory(prefix="pixelheart-interior-")
        self.stage_root = Path(self._temporary.name)
        for reference in set(_asset_references(self.draft.data)):
            source = asset_path(reference, self.project_file.parent)
            if source.is_file():
                _write_new_file(asset_path(reference, self.stage_root), _read_asset(source))
        self.selected_furniture = ""
        self.selected_catalog = ""
        self.elapsed_ms = 0
        self._started = 0.0
        self._playing_base = 0
        self._refreshing = False
        self.setWindowTitle("Spouse room designer — Pixelheart" if self.draft.data["kind"] == "spouse" else "Residence interior — Pixelheart")
        self.resize(1280, 880)
        self.setMinimumSize(980, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.addWidget(label("Their room in the farmhouse" if self.draft.data["kind"] == "spouse" else "An interior that belongs to them", "profileName"))
        self.artwork_status = label("", "muted", True)
        root.addWidget(self.artwork_status)
        toolbar = QHBoxLayout()
        self.tool = QComboBox()
        self.tool.addItem("Select / move furniture", "select")
        self.tool.addItem("Place selected furniture", "place")
        if self.draft.data["kind"] == "spouse":
            self.tool.addItem("Set spouse standing tile", "spouse_stand")
        else:
            self.tool.addItem("Set interior arrival tile", "entry")
        self.tool.setAccessibleName("Interior editing tool")
        self.tool.currentIndexChanged.connect(self.change_tool)
        toolbar.addWidget(self.tool)
        self.undo_button = button("Undo", self.undo)
        self.redo_button = button("Redo", self.redo)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        toolbar.addStretch()
        self.play = button("Play animation", lambda: None)
        self.play.setCheckable(True)
        self.play.toggled.connect(self.toggle_playback)
        toolbar.addWidget(self.play)
        self.zoom = QComboBox()
        for scale in (2, 3, 4):
            self.zoom.addItem(f"{scale * 100}%", scale)
        self.zoom.setAccessibleName("Interior preview zoom")
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        toolbar.addWidget(self.zoom)
        self.grid = QCheckBox("Grid")
        self.grid.setChecked(True)
        self.grid.toggled.connect(lambda *_: self.render())
        toolbar.addWidget(self.grid)
        root.addLayout(toolbar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(330)
        self.tabs.setMaximumWidth(440)
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { padding: 10px 9px; min-width: 0px; }")
        self._build_surfaces()
        self._build_rooms()
        self._build_furniture()
        self._build_animation()
        split.addWidget(self.tabs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        self.canvas = InteriorCanvas(self.draft)
        self.canvas.tile_clicked.connect(self.click_tile)
        self.canvas.selected.connect(self.select_furniture)
        self.canvas.moved.connect(self.move_furniture)
        self.canvas.hovered.connect(lambda x, y: self.coordinates.setText(f"Tile {x}, {y}"))
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.canvas_scroll, 1)
        self.coordinates = label("Select an item to move it. Drag it or edit its tile coordinates.", "hint", True)
        right_layout.addWidget(self.coordinates)
        selected = QHBoxLayout()
        self.selection_label = label("No furniture selected", "hint", True)
        selected.addWidget(self.selection_label, 1)
        self.selected_x = _number()
        self.selected_y = _number()
        self.selected_x.setAccessibleName("Selected furniture X")
        self.selected_y.setAccessibleName("Selected furniture Y")
        selected.addWidget(label("X"))
        selected.addWidget(self.selected_x)
        selected.addWidget(label("Y"))
        selected.addWidget(self.selected_y)
        self.move_button = button("Move", self.move_selected)
        self.rotate_button = button("Rotate", self.rotate_selected)
        self.remove_button = button("Remove", self.remove_selected, "quiet")
        for widget in (self.move_button, self.rotate_button, self.remove_button):
            selected.addWidget(widget)
        right_layout.addLayout(selected)
        right_layout.addWidget(label("Furniture stays separate from map tiles. Item behavior and mod animations are supplied by the installed game; test interactions there.", "muted", True))
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        self.status = label("", "notice", True)
        self.status.hide()
        root.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addWidget(label("Supplied artwork is staged until you save this design.", "hint", True), 1)
        bottom.addWidget(button("Cancel", self.reject, "quiet"))
        self.save_button = button("Save interior design", self.save_design, "primary")
        bottom.addWidget(self.save_button)
        root.addLayout(bottom)
        self.timer = QTimer(self)
        self.timer.setInterval(75)
        self.timer.timeout.connect(self.advance_animation)
        self.shortcuts = []
        for sequence, callback in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        delete = QShortcut(QKeySequence("Delete"), self.canvas)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.remove_selected)
        self.shortcuts.append(delete)
        self.finished.connect(self._finish)
        self.refresh()

    def _tab(self, title):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return content, layout

    def _build_surfaces(self):
        _, layout = self._tab("Surfaces")
        layout.addWidget(label("Choose an actual 16-pixel tilesheet for the room's floor and three wall rows.", "muted", True))
        layout.addWidget(button("Choose tilesheet PNG…", self.choose_atlas, "primary"))
        self.style_target = QComboBox()
        for name, key in (("Floor", "floor"), ("Wall · top", "wall_top"), ("Wall · middle", "wall_middle"), ("Wall · bottom", "wall_bottom")):
            self.style_target.addItem(name, key)
        self.style_target.setAccessibleName("Surface to change")
        self.style_target.currentIndexChanged.connect(self.show_style_tile)
        layout.addWidget(self.style_target)
        self.style_tile = label("Tile 0", "hint")
        layout.addWidget(self.style_tile)
        self.palette = InteriorPalette()
        self.palette.selected.connect(self.apply_tile)
        scroll = QScrollArea()
        scroll.setMinimumHeight(240)
        scroll.setWidget(self.palette)
        layout.addWidget(scroll, 1)
        layout.addWidget(label("Tile numbers begin at 0. Supplied images are copied unchanged.", "hint", True))

    def _build_rooms(self):
        _, layout = self._tab("Rooms")
        self.canvas_size_controls = QWidget()
        size_layout = QVBoxLayout(self.canvas_size_controls)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(label("Canvas size · tiles", "hint"))
        size_row = QHBoxLayout()
        self.canvas_width = _number(6, 96, self.draft.data["width"])
        self.canvas_height = _number(6, 96, self.draft.data["height"])
        self.canvas_width.setAccessibleName("Interior canvas width in tiles")
        self.canvas_height.setAccessibleName("Interior canvas height in tiles")
        size_row.addWidget(self.canvas_width)
        size_row.addWidget(label("×"))
        size_row.addWidget(self.canvas_height)
        size_row.addWidget(button("Resize", self.resize_canvas))
        size_layout.addLayout(size_row)
        layout.addWidget(self.canvas_size_controls)
        self.room_list = QListWidget()
        self.room_list.setAccessibleName("Interior rooms")
        self.room_list.setMaximumHeight(150)
        layout.addWidget(self.room_list)
        self.room_controls = QWidget()
        controls = QVBoxLayout(self.room_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.room_name = QLineEdit("New room")
        self.room_fields = {"x": _number(0, 255, 12), "y": _number(3, 255, 5),
                            "width": _number(2, 64, 8), "height": _number(2, 64, 8)}
        form.addRow("Room name", self.room_name)
        for key, widget in self.room_fields.items():
            widget.setAccessibleName("New room " + key)
            form.addRow(key.title(), widget)
        controls.addLayout(form)
        self.room_optional = QCheckBox("Player can toggle this room in-game")
        self.room_optional.setChecked(True)
        controls.addWidget(self.room_optional)
        controls.addWidget(button("Add room", self.add_room))
        controls.addWidget(button("Remove selected room", self.remove_room, "quiet"))
        layout.addWidget(self.room_controls)
        spouse = self.draft.data["kind"] == "spouse"
        self.canvas_size_controls.setVisible(not spouse)
        self.room_controls.setVisible(not spouse)
        layout.addWidget(label("The spouse section is fixed at 6 × 9 tiles, with three wall rows and six floor rows." if spouse else "Rooms must connect to the interior. A room containing furniture or the arrival tile cannot be removed.", "hint", True))
        layout.addStretch()

    def _build_furniture(self):
        _, layout = self._tab("Furniture")
        layout.addWidget(button("Import game furniture library…", self.choose_catalog))
        layout.addWidget(button("Attach exported textures folder…", self.choose_texture_folder))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search furniture or item ID…")
        self.search.setAccessibleName("Search interior furniture")
        self.search.textChanged.connect(self.refresh_catalog)
        layout.addWidget(self.search)
        self.catalog_list = QListWidget()
        self.catalog_list.setIconSize(QSize(48, 48))
        self.catalog_list.setAccessibleName("Furniture catalogue")
        self.catalog_list.currentItemChanged.connect(self.choose_catalog_item)
        self.catalog_list.itemDoubleClicked.connect(lambda *_: self.tool.setCurrentIndex(self.tool.findData("place")))
        self.catalog_list.setMinimumHeight(200)
        layout.addWidget(self.catalog_list, 1)
        self.catalog_details = label("Choose furniture, then click the interior to place it.", "hint", True)
        layout.addWidget(self.catalog_details)
        row = QHBoxLayout()
        row.addWidget(button("Add item…", lambda: self.edit_definition(new=True)))
        row.addWidget(button("Edit details…", self.edit_definition))
        layout.addLayout(row)
        layout.addWidget(label('With Pixelheart Interiors installed, run pixelheart_export_furniture in the SMAPI console, then import its JSON library. This includes loaded vanilla and mod furniture previews. Raw Data/Furniture JSON is also accepted; missing preview details can be filled in manually.', "hint", True))

    def _build_animation(self):
        _, layout = self._tab("Animate")
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

    def notice(self, message):
        self.status.setText(str(message))
        self.status.setVisible(bool(message))

    def run_change(self, callback):
        try:
            result = callback()
            self.notice("")
            self.refresh()
            return result
        except (ValueError, OSError, TypeError) as exc:
            self.notice(str(exc))
            return None

    def refresh(self):
        self._refreshing = True
        self.canvas_width.setValue(self.draft.data["width"])
        self.canvas_height.setValue(self.draft.data["height"])
        previous_room = self.room_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.room_list.currentItem() else None
        self.room_list.clear()
        for room in self.draft.data["rooms"]:
            item = QListWidgetItem(f"{room['name']} · {room['width']} × {room['height']}" + (" · optional" if room.get("optional") else ""))
            item.setData(Qt.ItemDataRole.UserRole, room["id"])
            self.room_list.addItem(item)
            if room["id"] == previous_room:
                self.room_list.setCurrentItem(item)
        self.animation_list.clear()
        for animation in self.draft.data["animations"]:
            item = QListWidgetItem(f"Tile {animation['tile_id']} · {len(animation['frames'])} frames")
            item.setData(Qt.ItemDataRole.UserRole, animation["tile_id"])
            self.animation_list.addItem(item)
        atlas = self.draft.data["atlas"]
        self.palette.load(atlas, self.stage_root)
        self.artwork_status.setText("Choose a tilesheet for game artwork. The canvas currently shows a layout guide." if not atlas.get("asset") else "16-pixel tiles · Furniture is stored as placed items · No exterior changes")
        self.show_style_tile()
        self.refresh_catalog()
        self.select_furniture(self.selected_furniture)
        self.canvas.refresh_size()
        self._refreshing = False
        self.render()

    def render(self):
        try:
            preview = render_interior(self.draft.data, self.stage_root, elapsed_ms=self.elapsed_ms, grid=self.grid.isChecked())
            self.canvas.set_image(preview)
            preview.close()
        except (ValueError, OSError) as exc:
            self.notice("Preview unavailable: " + str(exc))

    def change_tool(self):
        if hasattr(self, "canvas"):
            self.canvas.tool = self.tool.currentData()

    def change_zoom(self):
        if hasattr(self, "canvas"):
            self.canvas.scale = self.zoom.currentData()
            self.canvas.refresh_size()

    def choose_atlas(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an interior tilesheet", "", "PNG images (*.png)")
        if path:
            self.load_atlas(path)

    def load_atlas(self, path):
        def change():
            atlas = import_atlas(path, self.stage_root)
            candidate = self.draft.snapshot()
            candidate["atlas"] = atlas
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
            candidate["style"][self.style_target.currentData()] = tile
            self.draft.apply(candidate)
        self.run_change(change)

    def add_room(self):
        self.run_change(lambda: self.draft.add_room(self.room_name.text().strip(), **{key: field.value() for key, field in self.room_fields.items()}, optional=self.room_optional.isChecked()))

    def resize_canvas(self):
        def change():
            if self.draft.data["kind"] == "spouse":
                return
            candidate = self.draft.snapshot()
            candidate.update(width=self.canvas_width.value(), height=self.canvas_height.value())
            self.draft.apply(candidate)
        self.run_change(change)

    def remove_room(self):
        selected = self.room_list.currentItem()
        if selected:
            self.run_change(lambda: self.draft.remove_room(selected.data(Qt.ItemDataRole.UserRole)))

    def choose_catalog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose exported furniture library or Data/Furniture", "", "JSON data (*.json)")
        if path:
            self.load_catalog(path)

    def load_catalog(self, path):
        warnings = []
        def change():
            imported = import_furniture_library(path, self.stage_root)
            warnings.extend(imported["warnings"])
            candidate = self.draft.snapshot()
            existing = {definition["id"]: definition for definition in candidate["catalog"]}
            for definition in imported["definitions"]:
                previous = existing.get(definition["id"], {})
                # A resolved export updates previews; a raw data refresh retains
                # the author's existing preview when it supplies none itself.
                existing[definition["id"]] = {**definition, **{key: previous[key] for key in ("preview_asset", "frames", "rotation_footprints") if not definition.get(key) and previous.get(key)}}
            candidate["catalog"] = list(existing.values())
            self.draft.apply(candidate)
        self.run_change(change)
        if warnings:
            self.notice("\n".join(warnings[:4]))

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
        previous = self.selected_catalog
        self.catalog_list.blockSignals(True)
        self.catalog_list.clear()
        for definition in self.draft.data["catalog"]:
            if query and query not in (definition["name"] + " " + definition["id"]).casefold():
                continue
            item = QListWidgetItem(definition["name"] + "\n" + definition["id"])
            item.setData(Qt.ItemDataRole.UserRole, definition["id"])
            if definition.get("preview_asset"):
                try:
                    preview = preview_frame(definition, self.stage_root)
                    if preview is not None:
                        item.setIcon(QIcon(QPixmap.fromImage(ImageQt(preview))))
                        preview.close()
                except (ValueError, OSError):
                    pass
            self.catalog_list.addItem(item)
            if definition["id"] == previous:
                self.catalog_list.setCurrentItem(item)
        self.catalog_list.blockSignals(False)
        self.choose_catalog_item(self.catalog_list.currentItem())

    def choose_catalog_item(self, item, previous=None):
        self.selected_catalog = item.data(Qt.ItemDataRole.UserRole) if item else ""
        definition = next((entry for entry in self.draft.data["catalog"] if entry["id"] == self.selected_catalog), None)
        if definition:
            footprint = definition.get("footprint")
            shape = f"{footprint[0]} × {footprint[1]} tiles" if footprint else "Set footprint in Edit details before placing"
            self.catalog_details.setText(f"{shape} · {definition['rotations']} rotation states\n" + ("Preview texture attached" if definition.get("preview_asset") else "Attach a texture to preview its artwork"))
        else:
            self.catalog_details.setText("Choose furniture, then use Place selected furniture and click the interior.")

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

    def click_tile(self, x, y):
        tool = self.tool.currentData()
        if tool == "place":
            if not self.selected_catalog:
                self.notice("Choose furniture in the Furniture tab first.")
                return
            def place():
                self.selected_furniture = self.draft.place_furniture(self.selected_catalog, x, y)
            self.run_change(place)
        elif tool in ("entry", "spouse_stand"):
            def change():
                candidate = self.draft.snapshot()
                candidate[tool] = [x, y]
                self.draft.apply(candidate)
            self.run_change(change)

    def select_furniture(self, identity):
        selected = next((entry for entry in self.draft.data["furniture"] if entry["id"] == identity), None)
        self.selected_furniture = identity if selected else ""
        self.canvas.selected_id = self.selected_furniture
        self.selection_label.setText(selected["item_id"] if selected else "No furniture selected")
        for widget in (self.selected_x, self.selected_y, self.move_button, self.rotate_button, self.remove_button):
            widget.setEnabled(selected is not None)
        if selected:
            self.selected_x.setValue(selected["x"])
            self.selected_y.setValue(selected["y"])
        self.canvas.update()

    def move_furniture(self, identity, x, y):
        self.run_change(lambda: self.draft.move_furniture(identity, x, y))

    def move_selected(self):
        if self.selected_furniture:
            self.move_furniture(self.selected_furniture, self.selected_x.value(), self.selected_y.value())

    def rotate_selected(self):
        if self.selected_furniture:
            self.run_change(lambda: self.draft.rotate_furniture(self.selected_furniture))

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
        self.play.setText("Pause animation" if playing else "Play animation")

    def advance_animation(self):
        self.elapsed_ms = self._playing_base + int((time.monotonic() - self._started) * 1000)
        self.render()

    def undo(self):
        self.run_change(self.draft.undo)

    def redo(self):
        self.run_change(self.draft.redo)

    def save_design(self):
        created = []
        try:
            design = self.draft.snapshot()
            pending = []
            for reference in sorted(set(_asset_references(design))):
                source = asset_path(reference, self.stage_root)
                if not source.is_file():
                    raise ValueError("A referenced interior texture is missing. Attach it again before saving.")
                payload = _read_asset(source)
                destination = asset_path(reference, self.project_file.parent)
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
            self.accept()
        except (ValueError, OSError) as exc:
            for destination in created:
                destination.unlink(missing_ok=True)
            self.notice(str(exc))

    def _finish(self, result):
        self.timer.stop()
        self._temporary.cleanup()
