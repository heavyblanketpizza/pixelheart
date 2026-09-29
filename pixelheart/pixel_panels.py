"""Painter side panels: tools, colors, layers and the live game-size preview."""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QGridLayout, QHBoxLayout, QInputDialog, QLineEdit,
    QListWidget, QListWidgetItem, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget, QToolTip,
)

from pixelheart_core.pixel_document import PixelError
from .artwork_browser import DIRECTIONS, EXPRESSION_NAMES
from .pixel_canvas import CHECKER, qimage_from_image
from .skin import COLORS
from .widgets import button, label


# 12 × 12 tool icons, drawn in ink at twice their size.
ICONS = {
    "pencil": """
........##..
.......#..#.
......#..#..
.....#..#...
....#..#....
...#..#.....
..#..#......
.##.#.......
.###........
##..........
............
............""",
    "eraser": """
............
.....####...
....#...##..
...#...#.#..
..#...#..#..
.#...#..#...
.####..#....
.#..#.#.....
.#..##......
.####.......
............
............""",
    "line": """
............
..........#.
.........#..
........#...
.......#....
......#.....
.....#......
....#.......
...#........
..#.........
.#..........
............""",
    "rectangle": """
............
............
.##########.
.#........#.
.#........#.
.#........#.
.#........#.
.#........#.
.##########.
............
............
............""",
    "ellipse": """
............
....####....
..##....##..
.#........#.
.#........#.
#..........#
#..........#
.#........#.
.#........#.
..##....##..
....####....
............""",
    "fill": """
............
....#.......
...#.#......
..#...#.....
.#.....#....
#.......#...
.#.....#.#..
..#...#..#..
...#.#...#..
....#.......
............
............""",
    "picker": """
.........##.
........####
.......####.
......#.##..
.....#..#...
....#..#....
...#..#.....
..#..#......
.#..#.......
#.##........
##..........
............""",
    "select": """
............
.##.##.##.#.
.#........#.
............
.#........#.
.#........#.
............
.#........#.
.#.##.##.##.
............
............
............""",
}
TOOL_NAMES = {"pencil": ("Pencil", "B"), "eraser": ("Eraser", "E"), "line": ("Line", "L"),
              "rectangle": ("Rectangle", "U"), "ellipse": ("Ellipse", "O"), "fill": ("Fill", "G"),
              "picker": ("Pick color", "I"), "select": ("Select and move", "M")}


def pixel_icon(name, color=None):
    rows = ICONS[name].strip().splitlines()
    image = QImage(len(rows[0]), len(rows), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    ink = QColor(color or COLORS["text"])
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "#":
                image.setPixelColor(x, y, ink)
    return QIcon(QPixmap.fromImage(image.scaled(image.width() * 2, image.height() * 2)))


def format_hex(color):
    red, green, blue, alpha = color
    text = f"#{red:02x}{green:02x}{blue:02x}"
    return text if alpha == 255 else text + f"{alpha:02x}"


def parse_hex(text):
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", text.strip())
    if not match:
        return None
    value = match.group(1) + (match.group(2) or "ff")
    return tuple(int(value[index:index + 2], 16) for index in range(0, 8, 2))


def heading(text):
    return label(text, "painterHeading")


def _paint_swatch(painter, rect, color):
    light, dark = (QColor(value) for value in CHECKER["light"])
    painter.fillRect(rect, light)
    half = rect.width() // 2
    painter.fillRect(rect.left() + half, rect.top(), rect.width() - half, rect.height() // 2, dark)
    painter.fillRect(rect.left(), rect.top() + rect.height() // 2, half, rect.height() - rect.height() // 2, dark)
    painter.fillRect(rect, QColor(*color))


class ToolStrip(QWidget):
    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, name in enumerate(TOOL_NAMES):
            caption, key = TOOL_NAMES[name]
            tool = QToolButton()
            tool.setObjectName("painterTool")
            tool.setCheckable(True)
            tool.setIcon(pixel_icon(name))
            tool.setIconSize(QSize(24, 24))
            tool.setToolTip(f"{caption} ({key})")
            tool.setAccessibleName(caption)
            tool.clicked.connect(lambda checked=False, n=name: self.selected.emit(n))
            self.group.addButton(tool)
            self.buttons[name] = tool
            grid.addWidget(tool, index // 2, index % 2)
        grid.setRowStretch(len(TOOL_NAMES) // 2, 1)
        self.buttons["pencil"].setChecked(True)

    def set_tool(self, name):
        self.buttons[name].setChecked(True)


class SwatchButton(QWidget):
    clicked = Signal()

    def __init__(self, size, name, parent=None):
        super().__init__(parent)
        self.color = (0, 0, 0, 0)
        self.setFixedSize(size, size)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color):
        self.color = tuple(color)
        self.setToolTip(f"{self.accessibleName()}: {format_hex(self.color)}")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        _paint_swatch(painter, self.rect().adjusted(2, 2, -2, -2), self.color)
        painter.setPen(QPen(QColor(COLORS["outline"]), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.end()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


class SwatchGrid(QWidget):
    """A compact grid of colors; left click chooses, right click sets the second color."""
    chosen = Signal(tuple)
    chosen_secondary = Signal(tuple)
    CELL = 20
    COLUMNS = 12

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.colors, self.counts, self.current = [], [], None
        self.setAccessibleName(name)
        self.setFixedWidth(self.COLUMNS * self.CELL)
        self.set_colors([])

    def set_colors(self, colors, counts=None):
        self.colors = [tuple(color) for color in colors]
        self.counts = list(counts or [])
        rows = max(1, (len(self.colors) + self.COLUMNS - 1) // self.COLUMNS)
        self.setFixedHeight(rows * self.CELL)
        self.update()

    def index_at(self, point):
        index = point.y() // self.CELL * self.COLUMNS + point.x() // self.CELL
        return index if 0 <= point.x() < self.width() and 0 <= index < len(self.colors) else None

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.colors:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, "No colors yet")
        for index, color in enumerate(self.colors):
            rect = QRect(index % self.COLUMNS * self.CELL, index // self.COLUMNS * self.CELL, self.CELL, self.CELL)
            _paint_swatch(painter, rect.adjusted(1, 1, -1, -1), color)
            selected = color == self.current
            painter.setPen(QPen(QColor(COLORS["outline" if selected else "rule"]), 2 if selected else 1))
            painter.drawRect(rect.adjusted(1, 1, -1, -1) if selected else rect.adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event):
        index = self.index_at(event.position().toPoint())
        if index is None:
            return
        color = self.colors[index]
        if event.button() == Qt.MouseButton.RightButton:
            self.chosen_secondary.emit(color)
        else:
            self.chosen.emit(color)

    def event(self, event):
        if event.type() == event.Type.ToolTip:
            index = self.index_at(event.pos())
            if index is not None:
                text = format_hex(self.colors[index])
                if index < len(self.counts):
                    text += f" · {self.counts[index]} px"
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class ColorPanel(QWidget):
    colors_changed = Signal()
    edited = Signal()
    message = Signal(str)
    SHEET_COLOR_LIMIT = 96

    def __init__(self, document, tools, parent=None):
        super().__init__(parent)
        self.document, self.tools = document, tools
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(heading("COLOR"))
        row = QHBoxLayout()
        self.primary = SwatchButton(40, "First color")
        self.secondary = SwatchButton(28, "Second color")
        self.primary.clicked.connect(lambda: self.choose("primary"))
        self.secondary.clicked.connect(lambda: self.choose("secondary"))
        row.addWidget(self.primary)
        row.addWidget(self.secondary, 0, Qt.AlignmentFlag.AlignBottom)
        column = QVBoxLayout()
        self.hex = QLineEdit()
        self.hex.setAccessibleName("First color as hex, with optional alpha")
        self.hex.setPlaceholderText("#rrggbbaa")
        self.hex.setMaximumWidth(120)
        self.hex.editingFinished.connect(self.apply_hex)
        column.addWidget(self.hex)
        self.swap_button = button("Swap (X)", self.swap, "quiet")
        column.addWidget(self.swap_button)
        row.addLayout(column)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("Right-click paints the second color. It starts transparent, so it erases.", "hint", True))
        layout.addWidget(heading("RECENT"))
        self.recent = SwatchGrid("Recent colors")
        self.recent.chosen.connect(lambda color: self.set_color("primary", color))
        self.recent.chosen_secondary.connect(lambda color: self.set_color("secondary", color))
        layout.addWidget(self.recent)
        layout.addWidget(heading("IN THIS SHEET"))
        self.sheet_swatches = SwatchGrid("Colors in this sheet")
        self.sheet_swatches.chosen.connect(self.choose_sheet_color)
        self.sheet_swatches.chosen_secondary.connect(lambda color: self.set_color("secondary", color))
        layout.addWidget(self.sheet_swatches)
        replace_row = QHBoxLayout()
        self.color_count = label("", "hint")
        replace_row.addWidget(self.color_count, 1)
        self.all_layers = QCheckBox("All layers")
        self.all_layers.setToolTip("Replace the color on every unlocked layer, not just the current one.")
        replace_row.addWidget(self.all_layers)
        self.replace_button = button("Replace…", self.replace_color, "quiet")
        self.replace_button.setToolTip("Replace the selected color everywhere it appears, or only inside the selection.")
        replace_row.addWidget(self.replace_button)
        layout.addLayout(replace_row)
        self.refresh()

    def refresh(self):
        self.refresh_colors()
        self.refresh_sheet()

    def refresh_colors(self):
        self.primary.set_color(self.tools.primary)
        self.secondary.set_color(self.tools.secondary)
        if not self.hex.hasFocus():
            self.hex.setText(format_hex(self.tools.primary))
        self.recent.set_colors(self.tools.recent)

    def refresh_sheet(self):
        colors = self.document.sheet_colors()
        shown = colors[:self.SHEET_COLOR_LIMIT]
        self.sheet_swatches.set_colors([color for color, _ in shown], [count for _, count in shown])
        more = f", {self.SHEET_COLOR_LIMIT} most used shown" if len(colors) > self.SHEET_COLOR_LIMIT else ""
        self.color_count.setText(f"{len(colors)} color{'s' if len(colors) != 1 else ''}{more}")
        self.replace_button.setEnabled(bool(colors))

    def set_color(self, which, color):
        setattr(self.tools, which, tuple(color))
        self.refresh_colors()
        self.colors_changed.emit()

    def choose(self, which):
        current = QColor(*getattr(self.tools, which))
        title = "First color" if which == "primary" else "Second color"
        chosen = QColorDialog.getColor(current, self, title, QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if chosen.isValid():
            self.set_color(which, chosen.getRgb())

    def choose_sheet_color(self, color):
        self.sheet_swatches.current = tuple(color)
        self.sheet_swatches.update()
        self.set_color("primary", color)

    def swap(self):
        self.tools.swap_colors()
        self.refresh_colors()
        self.colors_changed.emit()

    def apply_hex(self):
        color = parse_hex(self.hex.text())
        if color is None:
            self.hex.setText(format_hex(self.tools.primary))
            return
        if color != self.tools.primary:
            self.set_color("primary", color)

    def replace_color(self):
        old = self.sheet_swatches.current or self.tools.primary
        chosen = QColorDialog.getColor(QColor(*old), self, f"Replace {format_hex(old)} with",
                                       QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if not chosen.isValid():
            return
        new = tuple(chosen.getRgb())
        try:
            count = self.document.replace_color(old, new, all_layers=self.all_layers.isChecked())
        except PixelError as exc:
            self.message.emit(str(exc))
            return
        where = " in the selection" if self.document.selection else ""
        self.message.emit(f"Replaced {count} pixel{'s' if count != 1 else ''} of {format_hex(old)} with {format_hex(new)}{where}.")
        self.sheet_swatches.current = new
        self.edited.emit()


class LayerPanel(QWidget):
    """Layers listed top first, as they stack on the sheet."""
    edited = Signal()
    message = Signal(str)

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self._refreshing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(heading("LAYERS"))
        self.list = QListWidget()
        self.list.setObjectName("painterLayers")
        self.list.setAccessibleName("Layers, top first. Tick to show; double-click to rename.")
        self.list.setMinimumHeight(112)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self.choose)
        self.list.itemChanged.connect(self.toggle_visibility)
        self.list.itemDoubleClicked.connect(lambda item: self.rename())
        layout.addWidget(self.list)
        opacity = QHBoxLayout()
        opacity.addWidget(label("Opacity", "hint"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setAccessibleName("Layer opacity")
        self.opacity.valueChanged.connect(self.set_opacity)
        opacity.addWidget(self.opacity, 1)
        self.opacity_value = label("100%", "hint")
        self.opacity_value.setMinimumWidth(36)
        opacity.addWidget(self.opacity_value)
        layout.addLayout(opacity)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.add_button = button("New", lambda: self.run(self.document.add_layer), "quiet")
        self.duplicate_button = button("Duplicate", lambda: self.run(self.document.duplicate_layer), "quiet")
        self.delete_button = button("Delete", lambda: self.run(self.document.delete_layer), "quiet")
        self.up_button = button("Move up", lambda: self.move(1), "quiet")
        self.down_button = button("Move down", lambda: self.move(-1), "quiet")
        self.merge_button = button("Merge down", lambda: self.run(self.document.merge_down), "quiet")
        self.lock_button = button("Lock", self.toggle_lock, "quiet")
        self.rename_button = button("Rename…", self.rename, "quiet")
        for index, widget in enumerate((self.add_button, self.duplicate_button, self.delete_button, self.rename_button,
                                        self.up_button, self.down_button, self.merge_button, self.lock_button)):
            grid.addWidget(widget, index // 4, index % 4)
        layout.addLayout(grid)
        self.refresh()

    def _row(self, index):
        return len(self.document.layers) - 1 - index

    def refresh(self):
        self._refreshing = True
        document = self.document
        self.list.clear()
        for index in reversed(range(len(document.layers))):
            layer = document.layers[index]
            notes = [note for note, present in (("reference", layer.reference), ("locked", layer.locked),
                                                (f"{layer.opacity}%", layer.opacity < 100)) if present]
            item = QListWidgetItem(layer.name + ("  · " + " · ".join(notes) if notes else ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked)
            item.setToolTip(layer.name + (" — a reference layer: it helps you trace and never reaches the game."
                                          if layer.reference else ""))
            self.list.addItem(item)
        self.list.setCurrentRow(self._row(document.active_index))
        active = document.active_layer
        self.opacity.setValue(active.opacity)
        self.opacity_value.setText(f"{active.opacity}%")
        count = len(document.layers)
        self.delete_button.setEnabled(count > 1)
        self.up_button.setEnabled(document.active_index < count - 1)
        self.down_button.setEnabled(document.active_index > 0)
        below = document.layers[document.active_index - 1] if document.active_index > 0 else None
        self.merge_button.setEnabled(below is not None and not active.reference and not below.reference)
        self.lock_button.setText("Unlock" if active.locked else "Lock")
        self._refreshing = False

    def run(self, action, *arguments, **keywords):
        try:
            action(*arguments, **keywords)
        except PixelError as exc:
            self.message.emit(str(exc))
        self.refresh()
        self.edited.emit()

    def choose(self, row):
        if self._refreshing or row < 0:
            return
        self.document.set_active(self._row(row))
        self.refresh()
        self.edited.emit()

    def toggle_visibility(self, item):
        if self._refreshing:
            return
        index = self._row(self.list.row(item))
        self.run(self.document.update_layer, index, visible=item.checkState() == Qt.CheckState.Checked)

    def set_opacity(self, value):
        if self._refreshing:
            return
        self.opacity_value.setText(f"{value}%")
        self.run(self.document.update_layer, opacity=value, merge=True)

    def toggle_lock(self):
        self.run(self.document.update_layer, locked=not self.document.active_layer.locked)

    def move(self, step):
        index = self.document.active_index
        self.run(self.document.move_layer, index, index + step)

    def rename(self):
        layer = self.document.active_layer
        name, accepted = QInputDialog.getText(self, "Rename layer", "Layer name", text=layer.name)
        if accepted:
            self.run(self.document.update_layer, name=name)


class PreviewView(QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setMinimumHeight(120)
        self.setAccessibleName("Game-size preview")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        light, dark = (QColor(value) for value in CHECKER["light"])
        painter.fillRect(self.rect(), light)
        for y in range(0, self.height(), 8):
            for x in range((y // 8) % 2 * 8, self.width(), 16):
                painter.fillRect(x, y, 8, 8, dark)
        painter.setPen(QPen(QColor(COLORS["rule"]), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        images = self.panel.images
        if not images:
            painter.end()
            return
        image = images[self.panel.step % len(images)]
        width, height = image.width(), image.height()
        area = self.rect().adjusted(10, 10, -10, -10)
        if self.panel.kind == "tilesheet" and self.panel.framed:
            zoom = max(1, min(3, area.height() // (height * 3)))
            left = area.left() + width + 16
            painter.drawImage(QRect(area.left(), area.top(), width, height), image)
            for row in range(3):
                for column in range(3):
                    painter.drawImage(QRect(left + column * width * zoom, area.top() + row * height * zoom,
                                            width * zoom, height * zoom), image)
        elif self.panel.framed:
            zoom = max(1, min(3, area.height() // height, (area.width() - width - 16) // max(1, width)))
            top = area.top() + max(0, (area.height() - height * zoom) // 2)
            painter.drawImage(QRect(area.left(), top + height * zoom - height, width, height), image)
            painter.drawImage(QRect(area.left() + width + 16, top, width * zoom, height * zoom), image)
        else:
            scale = min(area.width() / width, area.height() / height, 1.0 if max(width, height) > 256 else 4.0)
            target = QRect(0, 0, max(1, int(width * scale)), max(1, int(height * scale)))
            target.moveCenter(area.center())
            painter.drawImage(target, image)
        painter.end()


class PreviewPanel(QWidget):
    """The flattened sheet at game size: a walking row, an expression, or a repeating tile."""

    def __init__(self, document, kind, parent=None):
        super().__init__(parent)
        self.document, self.kind = document, kind
        self.frame = 0
        self.step = 0
        self.images = []
        self.framed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(heading("PREVIEW"))
        self.caption = label("", "hint", True)
        layout.addWidget(self.caption)
        self.view = PreviewView(self)
        layout.addWidget(self.view)
        controls = QHBoxLayout()
        self.play = QCheckBox("Play")
        self.play.setChecked(True)
        self.play.toggled.connect(self.update_timer)
        controls.addWidget(self.play)
        self.speed = QSpinBox()
        self.speed.setRange(1, 12)
        self.speed.setValue(6)
        self.speed.setSuffix(" fps")
        self.speed.setAccessibleName("Preview speed in frames per second")
        self.speed.valueChanged.connect(self.update_timer)
        controls.addWidget(self.speed)
        controls.addStretch()
        layout.addLayout(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        animated = kind == "sprite"
        self.play.setVisible(animated)
        self.speed.setVisible(animated)
        self.refresh()

    def set_frame(self, index):
        if index != self.frame:
            self.frame = index
            self.step = 0
            self.refresh()

    def _frames(self):
        document = self.document
        if document.frame_count <= 1:
            return [None]
        if self.kind == "sprite":
            columns = document.frame_columns
            start = min(self.frame, document.frame_count - 1) // columns * columns
            return list(range(start, start + columns))
        return [min(self.frame, document.frame_count - 1)]

    def refresh(self):
        document = self.document
        frames = self._frames()
        self.framed = frames != [None]
        self.images = [qimage_from_image(document.flatten() if index is None
                                         else document.composite(document.frame_box(index), include_reference=False))
                       for index in frames]
        self.caption.setText(self._caption())
        self.update_timer()
        self.view.update()

    def _caption(self):
        document = self.document
        if not self.framed:
            return f"Whole sheet · {document.width} × {document.height}"
        index = min(self.frame, document.frame_count - 1)
        column, row = index % document.frame_columns, index // document.frame_columns
        if self.kind == "sprite":
            where = f"Walking {DIRECTIONS[row]}" if row < len(DIRECTIONS) else f"Row {row}"
            return f"{where} · frames {row * document.frame_columns}–{row * document.frame_columns + document.frame_columns - 1}"
        if self.kind == "portrait":
            name = EXPRESSION_NAMES[index] if index < len(EXPRESSION_NAMES) else "Expression"
            return f"{name} · ${index}"
        return f"Tile column {column}, row {row} · repeated to check its edges"

    def update_timer(self, *_):
        playing = self.kind == "sprite" and self.play.isChecked() and len(self.images) > 1
        if playing:
            self.timer.start(round(1000 / self.speed.value()))
        else:
            self.timer.stop()
            self.step = 0
            self.view.update()

    def advance(self):
        self.step = (self.step + 1) % max(1, len(self.images))
        self.view.update()
