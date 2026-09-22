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
from PySide6.QtCore import Qt, QRect, QSize, QPoint, Signal, QTimer, QMimeData
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QIcon, QShortcut, QKeySequence, QDrag
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QSplitter,
    QTabWidget, QScrollArea, QComboBox, QSpinBox, QCheckBox, QLineEdit,
    QListWidget, QListWidgetItem, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QListView, QStackedWidget,
    QStyledItemDelegate, QStyle,
)

from pixelheart_core.interiors import InteriorDraft, import_atlas, render_interior, footprint
from pixelheart_core.interior_furniture import (
    import_furniture_library, validate_definition, import_catalog_textures,
    attach_texture, preview_frame,
)
from pixelheart_core.world import asset_path, _read_asset, _write_new_file
from .widgets import label, button
from .game_import import game_import_settings
from .interior_canvas import FURNITURE_MIME, InteriorCanvas


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


class CatalogueTile(QStyledItemDelegate):
    """Fixed picture cards keep labels stable when an item is selected."""

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
    def __init__(self, project_file, design=None, kind="residence", parent=None, *, resident_name="", allow_rebase=False):
        super().__init__(parent)
        self.project_file = Path(project_file)
        self.result_design = None
        self.allow_rebase = allow_rebase
        self.draft = InteriorDraft(deepcopy(design), kind=kind)
        self._temporary = tempfile.TemporaryDirectory(prefix="pixelheart-interior-")
        self.stage_root = Path(self._temporary.name)
        for reference in set(_asset_references(self.draft.data)):
            source = asset_path(reference, self.project_file.parent)
            if source.is_file():
                _write_new_file(asset_path(reference, self.stage_root), _read_asset(source))
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
        self._catalog_signature = None
        self._surface_signature = None
        self.favorites = set(self.settings.value("interiors/favorites", [], type=list))
        self.recent = []
        spouse = self.draft.data["kind"] == "spouse"
        self.setWindowTitle("Decorate their spouse room — Pixelheart" if spouse else "Decorate their home — Pixelheart")
        self.resize(1320, 880)
        self.setMinimumSize(1000, 700)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = (resident_name + "’s " if resident_name else "Their ") + ("spouse room" if spouse else "home")
        titles.addWidget(label(title, "profileName"))
        self.artwork_status = label("", "muted", True)
        titles.addWidget(self.artwork_status)
        heading.addLayout(titles, 1)
        self.library_button = button("Connect game library…", self.connect_library)
        heading.addWidget(self.library_button)
        root.addLayout(heading)

        # The hidden tool model also preserves the editor's programmatic contract.
        # People choose visible actions; no technical tool dropdown is required.
        self.tool = QComboBox(self)
        for title, value in (("Move furniture", "select"), ("Place furniture", "place"),
                             ("Draw a room", "room"), ("Choose a finish", "surface")):
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
        self.canvas = InteriorCanvas(self.draft, root=self.stage_root)
        self.canvas.catalogue_source = self.catalog_list
        self.canvas.place_catalog_drop = self.place_catalog_drop
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
        bottom = QHBoxLayout()
        bottom.addWidget(button("Advanced…", self.advanced.exec, "quiet"))
        self.design_summary = label("", "hint")
        bottom.addWidget(self.design_summary, 1)
        bottom.addWidget(button("Cancel", self.reject, "quiet"))
        self.save_button = button("Save room" if spouse else "Save home", self.save_design, "primary")
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
        for sequence, callback in (("Delete", self.put_away), ("Backspace", self.put_away),
                                   ("R", self.rotate_active), ("D", self.duplicate_selected)):
            shortcut = QShortcut(QKeySequence(sequence), self.canvas)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(self.canvas.cancel_interaction)
        self.shortcuts.append(escape)
        self.finished.connect(self._finish)
        self.refresh()
        if not self.draft.data["catalog"] or (self.draft.data["kind"] == "residence"
                and self.draft.data.get("surfaces") and not self.draft.data.get("room_frame")):
            saved_library = self.settings.value("interiors/libraryFolder", "")
            if isinstance(saved_library, str) and saved_library:
                from pixelheart_core.interior_furniture import discover_furniture_libraries
                candidates = discover_furniture_libraries([saved_library], include_standard_paths=False)
                if candidates:
                    if not self.draft.data["catalog"]:
                        self.load_catalog(candidates[0])
                    else:
                        self.load_room_frame(candidates[0])
        QTimer.singleShot(0, self.fit_room)

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
        self.room_list = QListWidget()
        self.room_list.setAccessibleName("Choose a room")
        self.room_list.setMaximumHeight(190)
        self.room_list.currentItemChanged.connect(self.room_selection_changed)
        layout.addWidget(self.room_list)
        self.room_controls = QWidget()
        controls = QVBoxLayout(self.room_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(label("Add a room", "sectionTitle"))
        self.room_name = QLineEdit("New room")
        self.room_name.setPlaceholderText("Room name")
        self.room_name.setAccessibleName("New room name")
        controls.addWidget(self.room_name)
        self.room_size = QComboBox()
        for name, dimensions in (("Small · 4 × 4", (4, 4)), ("Medium · 6 × 6", (6, 6)), ("Large · 8 × 6", (8, 6))):
            self.room_size.addItem(name, dimensions)
        self.room_size.setCurrentIndex(1)
        controls.addWidget(self.room_size)
        controls.addWidget(label("Attach it to the selected room", "hint"))
        sides = QHBoxLayout()
        for caption, direction in (("← Left", "left"), ("Right →", "right"), ("↓ Below", "below")):
            sides.addWidget(button(caption, lambda checked=False, d=direction: self.add_adjacent_room(d)))
        controls.addLayout(sides)
        controls.addWidget(button("Draw a room…", lambda: self.set_tool("room")))
        self.room_optional = QCheckBox("Let the player add or remove this room")
        self.room_optional.setChecked(True)
        controls.addWidget(self.room_optional)
        controls.addWidget(button("Remove selected room", self.remove_room, "quiet"))
        layout.addWidget(self.room_controls)
        spouse = self.draft.data["kind"] == "spouse"
        self.room_controls.setVisible(not spouse)
        layout.addWidget(label("Your spouse room fits the farmhouse. The marked standing spot stays clear for the NPC." if spouse else "Rooms snap together. Furniture and the doorway are protected when a room is removed.", "muted", True))
        layout.addWidget(button("Choose their standing spot" if spouse else "Move the doorway", lambda: self.set_tool("spouse_stand" if spouse else "entry")))
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
        self.tool.setCurrentIndex(self.tool.findData(value))
        self.canvas.setFocus()

    def cancel_tool(self):
        self.canvas.clear_placement()
        self.set_tool("select")
        self.select_furniture("")
        self.coordinates.setText("Click to select · Drag to move · R to rotate · Delete to put away")

    def change_mode(self, index):
        if not hasattr(self, "canvas"):
            return
        self.cancel_tool()
        if index == 1:
            self.coordinates.setText("Pick wallpaper or flooring, then click the room you want to change.")
        elif index == 2:
            self.coordinates.setText("Select a room to add space beside it, or draw a connected room.")

    def center_room(self):
        if not hasattr(self, "canvas_scroll"):
            return
        rooms = [r for r in self.draft.data["rooms"] if r["enabled"]]
        left, top = min(r["x"] for r in rooms), min(r["y"] - 3 for r in rooms)
        right = max(r["x"] + r["width"] for r in rooms)
        bottom = max(r["y"] + r["height"] for r in rooms)
        cell = self.canvas.scale * 16
        viewport = self.canvas_scroll.viewport()
        cx, cy = (left + right) * cell / 2, (top + bottom) * cell / 2
        # Add only the padding needed to center the *room*, including when its
        # map coordinates are close to an edge. Mouse coordinates stay local.
        px, py = max(0, round(viewport.width()/2 - cx)), max(0, round(viewport.height()/2 - cy))
        rx = max(0, round(viewport.width()/2 - (self.canvas.width() - cx)))
        by = max(0, round(viewport.height()/2 - (self.canvas.height() - cy)))
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
        viewport = self.canvas_scroll.viewport()
        scale = max(1, min(4, viewport.width() // (width * 16), viewport.height() // (height * 16)))
        self._auto_fit = True
        self._fitting = True
        self.zoom.setCurrentIndex(self.zoom.findData(scale))
        self._fitting = False
        self.center_room()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_auto_fit", False) and hasattr(self, "canvas_scroll"):
            QTimer.singleShot(0, self.fit_room)

    def preview_feedback(self, valid, message):
        if message:
            self.coordinates.setText(message)
        elif self.canvas.catalogue_drag:
            self.coordinates.setText("Release to place · Esc to cancel")
        elif self.tool.currentData() == "place":
            self.coordinates.setText("Click to place · Right-click or R to rotate · Esc to stop")
        elif self.tool.currentData() == "room":
            self.coordinates.setText("Drag out a room beside an existing room. Release to build it.")
        elif self.tool.currentData() == "select":
            self.coordinates.setText("Click to select · Drag to move · R to rotate · Delete to put away")

    def connect_library(self):
        from pixelheart_core.interior_furniture import discover_furniture_libraries, resolve_furniture_library
        saved = self.settings.value("interiors/libraryFolder", "")
        cp = self.settings.value("localGame/contentPatcherExportFolder", "")
        candidates = discover_furniture_libraries([p for p in (saved, cp) if isinstance(p, str) and p])
        if candidates and self.load_catalog(candidates[0]):
            self.settings.setValue("interiors/libraryFolder", str(candidates[0].parent))
            return
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
        feedback = label("", "notice", True)
        feedback.hide()
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

    def _selected_room(self):
        selected = self.room_list.currentItem()
        identity = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        return next((r for r in self.draft.data["rooms"] if r["id"] == identity), self.draft.data["rooms"][0])

    def select_room_on_canvas(self, identity):
        for row in range(self.room_list.count()):
            if self.room_list.item(row).data(Qt.ItemDataRole.UserRole) == identity:
                self.room_list.setCurrentRow(row)
                break

    def room_selection_changed(self, *_):
        if hasattr(self, "canvas") and self.tabs.currentIndex() == 2:
            room = self._selected_room()
            self.canvas.selected_room_id = room["id"]
            self.canvas.set_room_preview(None)
            self.canvas.update()

    def add_adjacent_room(self, direction):
        room = self._selected_room()
        width, height = self.room_size.currentData()
        x, y = room["x"], room["y"]
        if direction == "left":
            x -= width
        elif direction == "right":
            x += room["width"]
        else:
            y += room["height"]
        # Leave room for a border when expanding left. Move all authored points
        # together, keeping every placement and anchor aligned with the room.
        shift = max(0, 1 - x)
        self._add_visual_room(x, y, width, height, shift_x=shift)

    def _add_visual_room(self, x, y, width, height, *, shift_x=0):
        def change():
            if shift_x and not self.allow_rebase:
                raise ValueError("There isn’t enough space on this side. Add the room to the right or below to keep existing routes and doorways in place.")
            candidate = self.draft.snapshot()
            if shift_x:
                for room in candidate["rooms"]:
                    room["x"] += shift_x
                for item in candidate["furniture"]:
                    item["x"] += shift_x
                for anchor in ("entry", "spouse_stand"):
                    candidate[anchor][0] += shift_x
            px = x + shift_x
            candidate["width"] = max(candidate["width"] + shift_x, px + width + 1)
            candidate["height"] = max(candidate["height"], y + height + 1)
            working = InteriorDraft(candidate)
            working.add_room(self.room_name.text().strip() or "New room", x=px, y=y, width=width, height=height, optional=self.room_optional.isChecked())
            self.draft.apply(working.snapshot())
        before = len(self.draft.data["rooms"])
        self.run_change(change)
        if len(self.draft.data["rooms"]) > before:
            self.room_list.setCurrentRow(self.room_list.count() - 1)
            self.center_room()
            self.cancel_tool()

    def draw_room(self, x, y, width, height):
        if self.draft.data["kind"] != "spouse":
            self._add_visual_room(x, y, width, height)

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
        if self.tool.currentData() == "place":
            definition = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
            if definition:
                self.placement_rotation = (self.placement_rotation + 1) % definition["rotations"]
                self.canvas.set_placement(definition, self.placement_rotation, self.stage_root)
        else:
            self.rotate_selected()

    def put_away(self):
        if self.tool.currentData() == "place":
            self.cancel_tool()
        else:
            self.remove_selected()

    def duplicate_selected(self):
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
        self.artwork_status.setText(("Furniture connected. Choose finishes, or add custom tile art in Advanced." if self.draft.data["catalog"] else "Plan the room now. Connect your game to start decorating.") if not atlas.get("asset") else "Pick a piece. Make it their place.")
        self.library_button.setText("Refresh game library…" if self.draft.data["catalog"] else "Connect game library…")
        self.library_welcome.setVisible(not self.draft.data["catalog"])
        self.plan_first.setVisible(not self.draft.data["catalog"])
        for widget in (self.search, self.category, self.catalog_list, self.catalog_details, self.favorite_button):
            widget.setVisible(bool(self.draft.data["catalog"]))
        self.design_summary.setText(f"{len(self.draft.data['rooms'])} room{'s' if len(self.draft.data['rooms']) != 1 else ''} · {len(self.draft.data['furniture'])} pieces of furniture")
        self.undo_button.setEnabled(bool(self.draft._undo))
        self.redo_button.setEnabled(bool(self.draft._redo))
        self.refresh_surfaces()
        self.show_style_tile()
        self.refresh_catalog()
        self.select_furniture(self.selected_furniture)
        if self.tool.currentData() == "place":
            held = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
            if held and held.get("footprint"):
                self.placement_rotation %= held["rotations"]
                self.canvas.set_placement(held, self.placement_rotation, self.stage_root)
            else:
                self.cancel_tool()
        self.canvas.refresh_size()
        self.center_room()
        self._refreshing = False
        self.render()
        self.room_selection_changed()

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
            self.canvas.tool = self.tool.currentData()
            if self.canvas.tool != "place":
                self.canvas.clear_placement()
            else:
                definition = next((d for d in self.draft.data["catalog"] if d["id"] == self.selected_catalog), None)
                if definition and definition.get("footprint"):
                    self.canvas.set_placement(definition, self.placement_rotation, self.stage_root)
            self.canvas.set_room_preview(None)
            if self.canvas.tool == "room":
                self.coordinates.setText("Drag beside a room to add space. Rooms must share an edge.")
            elif self.canvas.tool in ("entry", "spouse_stand"):
                self.coordinates.setText("Click a clear spot on the floor. Keep a path to it open.")

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
            atlas = import_atlas(path, self.stage_root)
            candidate = self.draft.snapshot()
            candidate["atlas"] = atlas
            candidate.pop("surfaces", None)
            candidate.pop("room_styles", None)
            candidate.pop("room_frame", None)
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
        succeeded = False
        def change():
            nonlocal succeeded
            imported = import_furniture_library(path, self.stage_root)
            warnings.extend(imported["warnings"])
            candidate = self.draft.snapshot()
            existing = {definition["id"]: definition for definition in candidate["catalog"]}
            for definition in imported["definitions"]:
                previous = existing.get(definition["id"], {})
                preserved = {key: previous[key] for key in ("preview_asset", "frames", "rotation_footprints")
                             if not definition.get(key) and previous.get(key)}
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
                from pixelheart_core.interior_surface_design import stage_room_frame
                candidate = stage_room_frame(candidate, imported["room_frame"], self.stage_root)
            self.draft.apply(candidate)
            succeeded = True
        self.run_change(change)
        if succeeded and warnings:
            self.notice("Library notes: " + "\n".join(warnings[:3]))
        return succeeded

    def load_room_frame(self, path):
        """Complete an older library-backed room without replacing its edits."""
        def change():
            imported = import_furniture_library(path, self.stage_root)
            if imported.get("room_frame"):
                from pixelheart_core.interior_surface_design import stage_room_frame
                self.draft.apply(stage_room_frame(self.draft.data, imported["room_frame"], self.stage_root))
        self.run_change(change)

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
        signature = (repr(definitions), query, category, tuple(sorted(self.favorites)), tuple(self.recent))
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
            if query and query not in (definition["name"] + " " + identity + " " + definition["kind"]).casefold():
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
            item.setToolTip(definition["name"])
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
            self.catalog_details.setText("No matches. Try a different category or search.")

    def choose_catalog_item(self, item, previous=None):
        old_identity = self.selected_catalog
        self.selected_catalog = item.data(Qt.ItemDataRole.UserRole) if item else ""
        definition = next((entry for entry in self.draft.data["catalog"] if entry["id"] == self.selected_catalog), None)
        self.favorite_button.setEnabled(definition is not None)
        self.favorite_button.setText("♥ Favorited" if self.selected_catalog in self.favorites else "♡ Favorite")
        if definition:
            self.catalog_details.setText(definition["name"] + (" · Drag into the room, or click to pick up" if definition.get("footprint") else " · Size unavailable; refresh the game library"))
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

    def place_furniture_once(self, identity, x, y, rotation):
        def place():
            placed_id = self.draft.place_furniture(identity, x, y, rotation)
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
            room = next((r for r in self.draft.data["rooms"] if r["x"] <= x < r["x"] + r["width"] and r["y"] - 3 <= y < r["y"] + r["height"]), None)
            if room:
                self.run_change(lambda: self.draft.apply(apply_surface(self.draft.data, self.selected_surface, room["id"])))
            else:
                self.notice("Click inside a room to change its finish.")
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
        definition = next((d for d in self.draft.data["catalog"] if selected and d["id"] == selected["item_id"]), None)
        self.selection_label.setText(definition["name"] if definition else "Click an item in the room to move or change it")
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
        self.play.setText("Pause" if playing else "Play")

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
