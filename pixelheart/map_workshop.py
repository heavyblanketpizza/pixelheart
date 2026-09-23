"""A small local Tiled-map painter using the creator's own PNG tilesheet.

The sheet is copied unchanged. Painting records tile IDs in a finite TMX map;
this tool does not generate or redistribute game artwork.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox,
    QScrollArea, QFileDialog, QCheckBox, QSplitter, QLabel,
)

from pixelheart_core.world import WorldError, import_map, map_bundle, asset_path
from .widgets import label, button


LAYERS = ("Back", "Buildings", "Front", "Paths")
PRESETS = {"small_location": (20, 20), "spouse_room": (6, 9)}
TILE_SIZE = 16
MAX_SHEET_SIZE = 2048
MAX_SHEET_BYTES = 16 * 1024 * 1024


def inspect_tilesheet(path):
    """Read a bounded supplied PNG; never resize or modify its pixels."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_SHEET_BYTES:
            raise WorldError("Choose a PNG tilesheet of at most 16 MiB.")
        payload = path.read_bytes()
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            if image.format != "PNG":
                raise WorldError("Choose an actual PNG image for the tilesheet.")
            if not (16 <= width <= MAX_SHEET_SIZE and 16 <= height <= MAX_SHEET_SIZE):
                raise WorldError("The tilesheet must be between 16 and 2048 pixels on each side.")
            if width % TILE_SIZE or height % TILE_SIZE:
                raise WorldError("Tilesheets use 16×16 pixel cells. Both dimensions must be multiples of 16.")
            if getattr(image, "n_frames", 1) != 1:
                raise WorldError("Choose a still PNG tilesheet, not an animated PNG.")
            image.verify()
        # verify() checks container integrity; load() checks the actual pixels.
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, WorldError):
            raise
        raise WorldError(f"Cannot read the tilesheet: {exc}") from exc
    return {"bytes": payload, "width": width, "height": height,
            "columns": width // TILE_SIZE, "tile_count": (width // TILE_SIZE) * (height // TILE_SIZE)}


class TileMapDraft:
    """Bounded map state with one undo entry per stroke or fill operation."""

    def __init__(self, preset="small_location"):
        if preset not in PRESETS:
            raise WorldError("Choose a small location or spouse room.")
        self.width, self.height = PRESETS[preset]
        self.layers = {name: [0] * (self.width * self.height) for name in LAYERS}
        self.history = []
        self._stroke = None
        self._stroke_recorded = False

    def snapshot(self):
        return self.width, self.height, deepcopy(self.layers)

    def _remember(self):
        if self._stroke is not None:
            if self._stroke_recorded:
                return
            state = self._stroke
            self._stroke_recorded = True
        else:
            state = self.snapshot()
        self.history.append(state)
        del self.history[:-50]

    def begin_stroke(self):
        self._stroke = self.snapshot()
        self._stroke_recorded = False

    def end_stroke(self):
        self._stroke = None
        self._stroke_recorded = False

    def _position(self, layer, x, y, tile):
        if layer not in LAYERS:
            raise WorldError("Choose a map layer.")
        if type(x) is not int or type(y) is not int or not 0 <= x < self.width or not 0 <= y < self.height:
            raise WorldError("Choose a tile inside the map.")
        if type(tile) is not int or not 0 <= tile <= 16384:
            raise WorldError("Choose a tile from the supplied tilesheet.")
        return y * self.width + x

    def paint(self, layer, x, y, tile):
        index = self._position(layer, x, y, tile)
        if self.layers[layer][index] == tile:
            return False
        self._remember()
        self.layers[layer][index] = tile
        return True

    def fill(self, layer, tile):
        self._position(layer, 0, 0, tile)
        if all(value == tile for value in self.layers[layer]):
            return False
        self._remember()
        self.layers[layer] = [tile] * (self.width * self.height)
        return True

    def flood(self, layer, x, y, tile):
        index = self._position(layer, x, y, tile)
        previous = self.layers[layer][index]
        if previous == tile:
            return False
        self._remember()
        queue = deque([(x, y)])
        self.layers[layer][index] = tile
        while queue:
            column, row = queue.popleft()
            for next_x, next_y in ((column - 1, row), (column + 1, row), (column, row - 1), (column, row + 1)):
                if 0 <= next_x < self.width and 0 <= next_y < self.height:
                    next_index = next_y * self.width + next_x
                    if self.layers[layer][next_index] == previous:
                        self.layers[layer][next_index] = tile
                        queue.append((next_x, next_y))
        return True

    def resize(self, preset):
        if preset not in PRESETS:
            raise WorldError("Choose a small location or spouse room.")
        width, height = PRESETS[preset]
        if (width, height) == (self.width, self.height):
            return False
        self.end_stroke()
        self._remember()
        resized = {name: [0] * (width * height) for name in LAYERS}
        for name in LAYERS:
            for row in range(min(height, self.height)):
                for column in range(min(width, self.width)):
                    resized[name][row * width + column] = self.layers[name][row * self.width + column]
        self.width, self.height, self.layers = width, height, resized
        return True

    def undo(self):
        self.end_stroke()
        if not self.history:
            return False
        self.width, self.height, self.layers = self.history.pop()
        return True

    def to_tmx(self, sheet):
        if not isinstance(sheet, dict) or any(key not in sheet for key in ("width", "height", "columns", "tile_count")):
            raise WorldError("Choose a tilesheet before saving the map.")
        if any(tile == 0 for tile in self.layers["Back"]):
            raise WorldError("Cover the Back layer with floor or ground tiles before saving. Choose Back and Fill layer to begin.")
        if any(type(tile) is not int or not 0 <= tile <= sheet["tile_count"] for tiles in self.layers.values() for tile in tiles):
            raise WorldError("The map refers to a tile outside the selected tilesheet.")
        root = ET.Element("map", {"version": "1.10", "orientation": "orthogonal", "renderorder": "right-down",
            "width": str(self.width), "height": str(self.height), "tilewidth": "16", "tileheight": "16",
            "infinite": "0", "nextlayerid": "5", "nextobjectid": "1"})
        tileset = ET.SubElement(root, "tileset", {"firstgid": "1", "name": "pixelheart_tiles",
            "tilewidth": "16", "tileheight": "16", "tilecount": str(sheet["tile_count"]), "columns": str(sheet["columns"])})
        ET.SubElement(tileset, "image", {"source": "tiles.png", "width": str(sheet["width"]), "height": str(sheet["height"])})
        for index, name in enumerate(LAYERS, 1):
            attributes = {"id": str(index), "name": name, "width": str(self.width), "height": str(self.height)}
            if name == "Paths":
                attributes["visible"] = "0"
            layer = ET.SubElement(root, "layer", attributes)
            data = ET.SubElement(layer, "data", {"encoding": "csv"})
            data.text = "\n" + ",\n".join(",".join(str(tile) for tile in self.layers[name][row * self.width:(row + 1) * self.width]) for row in range(self.height)) + "\n"
        ET.indent(root)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def read_painted_map(path):
    """Load only the painter's lossless subset; never discard imported features.

    Rich Tiled maps remain importable/exportable by the World page, but cannot
    be flattened into this small editor without losing objects or properties.
    """
    path = Path(path)
    bundle = map_bundle(path)
    message = ("This map uses features outside Pixelheart's tile painter. Keep editing it in Tiled and import the revised map. "
               "No map files or project references were changed.")
    try:
        xml = ET.fromstring(bundle["files"][bundle["entry"]])
        allowed = {"version", "tiledversion", "orientation", "renderorder", "width", "height", "tilewidth", "tileheight", "infinite", "nextlayerid", "nextobjectid"}
        if (set(xml.attrib) - allowed or xml.get("renderorder", "right-down") != "right-down"
                or any(child.tag not in ("tileset", "layer") for child in xml)):
            raise WorldError(message)
        size = bundle["width"], bundle["height"]
        preset = next((key for key, dimensions in PRESETS.items() if size == dimensions), None)
        tilesets = xml.findall("tileset")
        if preset is None or len(tilesets) != 1:
            raise WorldError(message)
        tileset = tilesets[0]
        if (tileset.get("name") != "pixelheart_tiles" or tileset.get("firstgid") != "1"
                or set(tileset.attrib) - {"firstgid", "name", "tilewidth", "tileheight", "tilecount", "columns"}
                or tileset.get("tilewidth") != "16" or tileset.get("tileheight") != "16"
                or len(tileset) != 1 or tileset[0].tag != "image"):
            raise WorldError(message)
        image = tileset[0]
        if image.get("source") != "tiles.png" or set(image.attrib) - {"source", "width", "height"} or len(image):
            raise WorldError(message)
        sheet = inspect_tilesheet(path.parent / "tiles.png")
        if (int(tileset.get("tilecount", "0")) != sheet["tile_count"] or int(tileset.get("columns", "0")) != sheet["columns"]
                or int(image.get("width", "0")) != sheet["width"] or int(image.get("height", "0")) != sheet["height"]):
            raise WorldError("The map and its tilesheet dimensions do not match. No files were changed.")
        layers = xml.findall("layer")
        if len(layers) != 4 or {layer.get("name") for layer in layers} != set(LAYERS):
            raise WorldError(message)
        # Keep layer order too: reordering layers is a real rendering change.
        if [layer.get("name") for layer in layers] != list(LAYERS):
            raise WorldError(message)
        draft = TileMapDraft(preset)
        for layer in layers:
            name = layer.get("name")
            if (set(layer.attrib) - {"id", "name", "width", "height", "visible"}
                    or int(layer.get("width", "0")) != draft.width or int(layer.get("height", "0")) != draft.height
                    or layer.get("visible", "1") != ("0" if name == "Paths" else "1")
                    or len(layer) != 1 or layer[0].tag != "data"):
                raise WorldError(message)
            data = layer[0]
            if data.attrib != {"encoding": "csv"} or len(data):
                raise WorldError(message)
            values = [int(value.strip()) for value in (data.text or "").split(",")]
            if len(values) != draft.width * draft.height or any(not 0 <= tile <= sheet["tile_count"] for tile in values):
                raise WorldError("This map contains invalid or transformed tiles that the simple painter cannot edit. No files were changed.")
            draft.layers[name] = values
        return draft, sheet
    except (ET.ParseError, TypeError, ValueError) as exc:
        if isinstance(exc, WorldError):
            raise
        raise WorldError(message) from exc


class TilesetPalette(QWidget):
    selected = Signal(int)
    CELL = 34
    COLUMNS = 8

    def __init__(self):
        super().__init__()
        self.sheet = None
        self.pixmap = QPixmap()
        self.current = 1
        self.setAccessibleName("Tilesheet palette; select a tile to paint")
        self.setFixedWidth(self.COLUMNS * self.CELL)
        self.setMinimumHeight(120)

    def load(self, sheet):
        self.sheet = sheet
        self.pixmap.loadFromData(sheet["bytes"], "PNG")
        self.current = 1
        self.setFixedHeight(((sheet["tile_count"] + self.COLUMNS - 1) // self.COLUMNS) * self.CELL)
        self.update()
        self.selected.emit(1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#e7e4dd"))
        if self.sheet is None:
            painter.setPen(QColor("#635c6a"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open your tilesheet\nto choose a tile.")
            return
        start = max(0, event.rect().top() // self.CELL) * self.COLUMNS
        end = min(self.sheet["tile_count"], (event.rect().bottom() // self.CELL + 1) * self.COLUMNS)
        for index in range(start, end):
            x, y = index % self.COLUMNS * self.CELL, index // self.COLUMNS * self.CELL
            source = QRect(index % self.sheet["columns"] * 16, index // self.sheet["columns"] * 16, 16, 16)
            painter.drawPixmap(QRect(x + 1, y + 1, 32, 32), self.pixmap, source)
            painter.setPen(QPen(QColor("#ab677e") if index + 1 == self.current else QColor("#c6c1b8"), 3 if index + 1 == self.current else 1))
            painter.drawRect(x + 1, y + 1, self.CELL - 2, self.CELL - 2)

    def mousePressEvent(self, event):
        if self.sheet is None or event.button() != Qt.MouseButton.LeftButton:
            return
        index = int(event.position().y()) // self.CELL * self.COLUMNS + int(event.position().x()) // self.CELL
        if 0 <= index < self.sheet["tile_count"]:
            self.current = index + 1
            self.update()
            self.selected.emit(self.current)


class MapCanvas(QWidget):
    changed = Signal()
    hovered = Signal(int, int)

    def __init__(self, draft):
        super().__init__()
        self.draft = draft
        self.sheet = None
        self.pixmap = QPixmap()
        self.layer = "Back"
        self.tool = "paint"
        self.tile = 1
        self.scale = 2
        self.grid = True
        self.hover = None
        self.setMouseTracking(True)
        self.setAccessibleName("Map canvas; paint the selected layer")
        self.refresh_size()

    def refresh_size(self):
        self.setFixedSize(self.draft.width * 16 * self.scale + 1, self.draft.height * 16 * self.scale + 1)
        self.update()

    def load(self, sheet):
        self.sheet = sheet
        self.pixmap.loadFromData(sheet["bytes"], "PNG")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        cell = 16 * self.scale
        painter.fillRect(event.rect(), QColor("#ebe6dc"))
        for y in range(self.draft.height):
            for x in range(self.draft.width):
                target = QRect(x * cell, y * cell, cell, cell)
                if not target.intersects(event.rect()):
                    continue
                if (x + y) % 2:
                    painter.fillRect(target, QColor("#e0dacf"))
                if self.sheet is not None:
                    for layer in LAYERS:
                        if layer == "Paths" and self.layer != "Paths":
                            continue
                        tile = self.draft.layers[layer][y * self.draft.width + x]
                        if tile:
                            index = tile - 1
                            painter.setOpacity(0.55 if layer == "Paths" else 1)
                            source = QRect(index % self.sheet["columns"] * 16, index // self.sheet["columns"] * 16, 16, 16)
                            painter.drawPixmap(target, self.pixmap, source)
                painter.setOpacity(1)
                if self.grid:
                    painter.setPen(QPen(QColor(56, 41, 66, 65), 1))
                    painter.drawRect(target)
        if self.hover is not None:
            x, y = self.hover
            painter.setPen(QPen(QColor("#ae5479"), 2))
            painter.drawRect(x * cell + 1, y * cell + 1, cell - 2, cell - 2)

    def _tile_at(self, event):
        cell = 16 * self.scale
        x, y = int(event.position().x()) // cell, int(event.position().y()) // cell
        if 0 <= x < self.draft.width and 0 <= y < self.draft.height:
            return x, y
        return None

    def _paint(self, position):
        if position is None or self.sheet is None:
            return
        x, y = position
        tile = 0 if self.tool == "erase" else self.tile
        changed = self.draft.flood(self.layer, x, y, tile) if self.tool == "fill" else self.draft.paint(self.layer, x, y, tile)
        if changed:
            self.changed.emit()
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.draft.begin_stroke()
            self._paint(self._tile_at(event))

    def mouseMoveEvent(self, event):
        self.hover = self._tile_at(event)
        if self.hover:
            self.hovered.emit(*self.hover)
        if event.buttons() & Qt.MouseButton.LeftButton and self.tool != "fill":
            self._paint(self.hover)
        self.update()

    def mouseReleaseEvent(self, event):
        self.draft.end_stroke()

    def leaveEvent(self, event):
        self.hover = None
        self.update()


class MapWorkshop(QDialog):
    """Paint and import a new immutable TMX bundle into a saved project."""

    def __init__(self, project_file, parent=None):
        super().__init__(parent)
        self.project_file = Path(project_file)
        self.result_reference = None
        self.result_size = None
        self.result_is_spouse_room = False
        self.sheet = None
        self.draft = TileMapDraft()
        self.setWindowTitle("Create a place — Pixelheart")
        self.resize(1120, 850)
        self.setMinimumSize(960, 700)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.addWidget(label("A place you can build tile by tile", "profileName", True))
        root.addWidget(label("Open your own PNG tilesheet, fill the ground, and paint walls or furniture. Each tilesheet cell must already be a 16×16 game tile.", "muted", True))
        top = QHBoxLayout()
        self.open_button = button("Open tilesheet PNG…", self.choose_sheet, "primary")
        top.addWidget(self.open_button)
        self.preset = QComboBox()
        self.preset.addItem("Small location · 20 × 20 tiles", "small_location")
        self.preset.addItem("Spouse room · 6 × 9 tiles", "spouse_room")
        self.preset.setAccessibleName("Map size preset")
        self.preset.currentIndexChanged.connect(self.resize_map)
        top.addWidget(self.preset)
        top.addStretch()
        self.undo_button = button("Undo", self.undo, "quiet")
        self.undo_button.setEnabled(False)
        top.addWidget(self.undo_button)
        root.addLayout(top)
        self.notice = label("Use artwork you have permission to include in your mod. Your supplied PNG is copied unchanged.", "notice", True)
        root.addWidget(self.notice)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left.setFixedWidth(294)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(label("1  CHOOSE A TILE", "eyebrow"))
        self.palette = TilesetPalette()
        self.palette_scroll = QScrollArea()
        self.palette_scroll.setWidget(self.palette)
        self.palette_scroll.setWidgetResizable(False)
        self.palette_scroll.setFixedHeight(200)
        left_layout.addWidget(self.palette_scroll)
        self.tile_label = label("No tilesheet selected", "hint", True)
        left_layout.addWidget(self.tile_label)
        self.palette.selected.connect(self.select_tile)
        left_layout.addWidget(label("2  CHOOSE A LAYER", "eyebrow"))
        self.layer = QComboBox()
        for name, caption in (("Back", "Ground and floors"), ("Buildings", "Walls and solid furniture"), ("Front", "Details above characters"), ("Paths", "Hidden game metadata")):
            self.layer.addItem(f"{name} · {caption}", name)
        self.layer.setAccessibleName("Map layer")
        self.layer.currentIndexChanged.connect(self.select_layer)
        left_layout.addWidget(self.layer)
        self.layer_note = label("Fill Back first so every map tile has ground. Add solid objects on Buildings.", "muted", True)
        left_layout.addWidget(self.layer_note)
        self.tool = QComboBox()
        for caption, name in (("Paint tile", "paint"), ("Erase tile", "erase"), ("Fill connected area", "fill")):
            self.tool.addItem(caption, name)
        self.tool.setAccessibleName("Map painting tool")
        self.tool.currentIndexChanged.connect(lambda *_: setattr(self.canvas, "tool", self.tool.currentData()))
        left_layout.addWidget(self.tool)
        fills = QHBoxLayout()
        self.fill_button = button("Fill layer", self.fill_layer)
        self.clear_button = button("Clear layer", lambda: self.fill_layer(clear=True), "quiet")
        fills.addWidget(self.fill_button)
        fills.addWidget(self.clear_button)
        left_layout.addLayout(fills)
        left_layout.addStretch()
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        options = QHBoxLayout()
        options.addWidget(label("3  PAINT YOUR PLACE", "eyebrow"))
        options.addStretch()
        self.zoom = QComboBox()
        for multiplier in (1, 2, 3, 4):
            self.zoom.addItem(f"{multiplier * 100}%", multiplier)
        self.zoom.setCurrentIndex(1)
        self.zoom.setAccessibleName("Map zoom")
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        options.addWidget(self.zoom)
        self.grid = QCheckBox("Grid")
        self.grid.setChecked(True)
        self.grid.toggled.connect(self.toggle_grid)
        options.addWidget(self.grid)
        right_layout.addLayout(options)
        self.canvas = MapCanvas(self.draft)
        self.canvas.changed.connect(self.changed)
        self.canvas.hovered.connect(lambda x, y: self.position.setText(f"Tile X {x} · Y {y}  |  {self.canvas.layer}"))
        canvas_scroll = QScrollArea()
        canvas_scroll.setWidget(self.canvas)
        canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(canvas_scroll, 1)
        self.position = label("Hover over a map tile to see its coordinates.", "hint", True)
        right_layout.addWidget(self.position)
        right_layout.addWidget(label("The preview shows your painted layers. Doors, entry points, routes, and walkability still need a game test. Configure the entrance after saving in Home & places.", "muted", True))
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(label("Changing size preserves overlapping tiles. Undo restores the previous size.", "hint", True), 1)
        bottom.addWidget(button("Cancel", self.reject, "quiet"))
        self.save_button = button("Save place to project", self.save_map, "primary")
        self.save_button.setEnabled(False)
        bottom.addWidget(self.save_button)
        root.addLayout(bottom)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo)

    def set_preset(self, preset):
        index = self.preset.findData(preset)
        if index < 0:
            raise WorldError("Choose a small location or spouse room.")
        self.preset.setCurrentIndex(index)

    def choose_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open your 16-pixel tilesheet", "", "PNG tilesheet (*.png)")
        if path:
            try:
                self.load_tilesheet(path)
            except WorldError as exc:
                self.notice.setText(str(exc))

    def load_tilesheet(self, path):
        sheet = inspect_tilesheet(path)
        highest = max(tile for tiles in self.draft.layers.values() for tile in tiles)
        if highest > sheet["tile_count"]:
            raise WorldError("This sheet has fewer tiles than your map uses. Keep the original sheet or clear those map tiles first.")
        self.sheet = sheet
        self.canvas.load(sheet)
        self.palette.load(sheet)
        self.palette_scroll.setFixedHeight(min(320, max(100, self.palette.height() + 4)))
        self.save_button.setEnabled(True)
        self.notice.setText(f"{Path(path).name} · {sheet['width']} × {sheet['height']} pixels · {sheet['tile_count']} tiles. Choose a floor tile and fill Back to begin.")

    def load_map(self, reference):
        """Open a saved painter map; saving creates another immutable revision."""
        source = asset_path(reference, self.project_file.parent)
        draft, sheet = read_painted_map(source)
        # Apply state only after every imported feature has been checked. A
        # rejected external map leaves the current editing session untouched.
        self.draft, self.sheet = draft, sheet
        self.result_reference = None
        self.result_size = None
        self.result_is_spouse_room = False
        self.canvas.draft = draft
        self.canvas.load(sheet)
        self.palette.load(sheet)
        self.palette_scroll.setFixedHeight(min(320, max(100, self.palette.height() + 4)))
        preset = "spouse_room" if (draft.width, draft.height) == PRESETS["spouse_room"] else "small_location"
        blocked = self.preset.blockSignals(True)
        self.preset.setCurrentIndex(self.preset.findData(preset))
        self.preset.blockSignals(blocked)
        self.zoom.setCurrentIndex(self.zoom.findData(3 if preset == "spouse_room" else 2))
        self.canvas.refresh_size()
        self.save_button.setEnabled(True)
        self.undo_button.setEnabled(False)
        self.setWindowTitle("Edit your painted place — Pixelheart")
        self.notice.setText("Editing the saved map. Save place to project creates a new revision; the previous map and supplied PNG stay intact.")

    def select_tile(self, tile):
        self.canvas.tile = tile
        if self.sheet:
            index = tile - 1
            self.tile_label.setText(f"Selected tile {tile} · sheet column {index % self.sheet['columns']}, row {index // self.sheet['columns']}")

    def select_layer(self):
        self.canvas.layer = self.layer.currentData()
        self.canvas.update()
        descriptions = {"Back": "Ground and floors go here. Cover every tile before saving.",
            "Buildings": "Walls and solid furniture usually block walking. Leave room for paths and doorways.",
            "Front": "These details draw above characters. Keep doorways and player visibility in mind.",
            "Paths": "Hidden game metadata, not decorative ground. Leave this layer empty unless you know the game's tile meanings."}
        self.layer_note.setText(descriptions[self.canvas.layer])

    def resize_map(self):
        if self.draft.resize(self.preset.currentData()):
            self.zoom.setCurrentIndex(self.zoom.findData(3 if self.preset.currentData() == "spouse_room" else 2))
            self.canvas.refresh_size()
            self.changed()

    def change_zoom(self):
        self.canvas.scale = self.zoom.currentData()
        self.canvas.refresh_size()

    def toggle_grid(self, checked):
        self.canvas.grid = checked
        self.canvas.update()

    def changed(self):
        self.undo_button.setEnabled(bool(self.draft.history))
        self.canvas.update()

    def fill_layer(self, checked=False, *, clear=False):
        if self.sheet is None:
            self.notice.setText("Open a tilesheet first.")
            return
        self.draft.end_stroke()
        if self.draft.fill(self.canvas.layer, 0 if clear else self.canvas.tile):
            self.changed()

    def undo(self):
        if self.draft.undo():
            selected = "spouse_room" if (self.draft.width, self.draft.height) == PRESETS["spouse_room"] else "small_location"
            blocked = self.preset.blockSignals(True)
            self.preset.setCurrentIndex(self.preset.findData(selected))
            self.preset.blockSignals(blocked)
            self.canvas.refresh_size()
            self.changed()

    def save_map(self):
        try:
            if self.sheet is None:
                raise WorldError("Open a tilesheet before saving.")
            payload = self.draft.to_tmx(self.sheet)
            with tempfile.TemporaryDirectory(prefix="pixelheart-map-") as folder:
                source = Path(folder)
                (source / "tiles.png").write_bytes(self.sheet["bytes"])
                (source / "place.tmx").write_bytes(payload)
                reference = import_map(source / "place.tmx", self.project_file)
            self.result_reference = reference
            self.result_size = self.draft.width, self.draft.height
            self.result_is_spouse_room = self.result_size == PRESETS["spouse_room"]
            self.accept()
            return reference
        except (WorldError, OSError) as exc:
            self.notice.setText(str(exc))
            return None
