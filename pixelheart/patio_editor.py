"""Decorate the spouse's 4×4 patio behind the farmhouse.

Game pieces are read from the player's own installation for the palette and the
farm preview; the exported patio names those tilesheets and never copies them.
The creator's own tilesheet works without the game.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from pixelheart_core.spouse_patio import (
    LAYERS, OWN_KEY, SIZE, erase, farm_context, farm_ground, fill, game_patios, game_sheet_choices,
    game_texture, import_own_sheet, new_patio, normalize_patio, own_sheet_image, paint, patio_issues,
    prune_sheets, render_patio, sheet_key, use_own_sheet,
)
from pixelheart_core.world import WorldError
from .game_connection import game_connection, open_find_game
from .skin import COLORS
from .widgets import button, card, label

LAYER_NAMES = {"Back": "Ground", "Buildings": "Objects", "Front": "Above characters"}
TOOLS = (("paint", "Paint"), ("erase", "Erase"), ("stand", "Standing spot"))
SEASON_NAMES = (("spring", "Spring"), ("summer", "Summer"), ("fall", "Fall"), ("winter", "Winter"))
FRAME_SIZE = (16, 32)


def to_pixmap(image):
    return QPixmap.fromImage(ImageQt(image))


def game_folder():
    connection = game_connection()
    return connection.folder() if connection.state() == "connected" else None


class PatioPalette(QWidget):
    """A tilesheet in its own layout; drag to pick a block so larger pieces stay whole."""

    selected = Signal()
    SCALE = 2

    def __init__(self):
        super().__init__()
        self.image = None
        self.pixmap = QPixmap()
        self.columns = self.rows = 0
        self.selection = (0, 0, 1, 1)
        self._anchor = None
        self.setAccessibleName("Patio pieces; drag to choose several tiles")
        self.setMinimumSize(16 * self.SCALE * 4, 120)

    def load(self, image, columns, rows):
        self.image, self.columns, self.rows = image, columns, rows
        self.pixmap = to_pixmap(image) if image is not None else QPixmap()
        self.selection = (0, 0, 1, 1)
        if image is not None:
            self.setFixedSize(columns * 16 * self.SCALE, rows * 16 * self.SCALE)
        else:
            self.setFixedSize(16 * self.SCALE * 8, 120)
        self.update()

    def tiles(self):
        x, y, width, height = self.selection
        return [[(y + row) * self.columns + x + column for column in range(width)] for row in range(height)]

    def select(self, x, y, width=1, height=1):
        if self.image is None:
            return
        x, y = max(0, min(x, self.columns - 1)), max(0, min(y, self.rows - 1))
        self.selection = (x, y, max(1, min(width, self.columns - x, SIZE)), max(1, min(height, self.rows - y, SIZE)))
        self.update()
        self.selected.emit()

    def _cell(self, event):
        cell = 16 * self.SCALE
        return int(event.position().x()) // cell, int(event.position().y()) // cell

    def mousePressEvent(self, event):
        if self.image is not None and event.button() == Qt.MouseButton.LeftButton:
            self._anchor = self._cell(event)
            self.select(*self._anchor)

    def mouseMoveEvent(self, event):
        if self._anchor is not None and event.buttons() & Qt.MouseButton.LeftButton:
            (ax, ay), (bx, by) = self._anchor, self._cell(event)
            self.select(min(ax, bx), min(ay, by), abs(bx - ax) + 1, abs(by - ay) + 1)

    def mouseReleaseEvent(self, event):
        self._anchor = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(COLORS["highlight"]))
        if self.image is None:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "Choose where\npieces come from.")
            return
        painter.drawPixmap(self.rect(), self.pixmap)
        cell = 16 * self.SCALE
        x, y, width, height = self.selection
        painter.setPen(QPen(QColor(COLORS["text"]), 3))
        painter.drawRect(x * cell + 1, y * cell + 1, width * cell - 2, height * cell - 2)


class PatioCanvas(QWidget):
    """The farm around the patio with the patio painted in; only the 4×4 area is editable."""

    pressed = Signal(int, int)
    dragged = Signal(int, int)
    released = Signal()

    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.origin = (16, 16)
        self.scale = 3
        self.stand = (2, 3)
        self.missing = set()
        self.hover = None
        self.show_grid = True
        self.setMouseTracking(True)
        self.setAccessibleName("Patio; paint the 4 by 4 area")

    def load(self, image, origin):
        self.pixmap, self.origin = to_pixmap(image), origin
        self.setFixedSize(image.width * self.scale, image.height * self.scale)
        self.update()

    def set_scale(self, scale):
        self.scale = scale
        if not self.pixmap.isNull():
            self.setFixedSize(self.pixmap.width() * scale, self.pixmap.height() * scale)
        self.update()

    def cell_rect(self, x, y):
        cell = 16 * self.scale
        return QRect(self.origin[0] * self.scale + x * cell, self.origin[1] * self.scale + y * cell, cell, cell)

    def cell_at(self, point):
        cell = 16 * self.scale
        x = (int(point.x()) - self.origin[0] * self.scale) // cell
        y = (int(point.y()) - self.origin[1] * self.scale) // cell
        return (x, y) if 0 <= x < SIZE and 0 <= y < SIZE else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#ebe6dc"))
        painter.drawPixmap(self.rect(), self.pixmap)
        area = self.cell_rect(0, 0).united(self.cell_rect(SIZE - 1, SIZE - 1))
        for x, y in self.missing:
            target = self.cell_rect(x, y)
            painter.fillRect(target, QColor(COLORS["highlight"]))
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter, "?")
        if self.show_grid:
            painter.setPen(QPen(QColor(56, 41, 66, 70), 1))
            for x in range(SIZE):
                for y in range(SIZE):
                    painter.drawRect(self.cell_rect(x, y))
        painter.setPen(QPen(QColor(COLORS["text"]), 2))
        painter.drawRect(area.adjusted(-1, -1, 1, 1))
        stand = self.cell_rect(*self.stand).adjusted(6, 6, -6, -6)
        painter.setPen(QPen(QColor("#d03a3a"), 3))
        painter.drawEllipse(stand)
        if self.hover is not None:
            painter.setPen(QPen(QColor("#ae5479"), 2))
            painter.drawRect(self.cell_rect(*self.hover).adjusted(1, 1, -1, -1))

    def mousePressEvent(self, event):
        position = self.cell_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and position is not None:
            self.pressed.emit(*position)

    def mouseMoveEvent(self, event):
        self.hover = self.cell_at(event.position())
        if self.hover is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(*self.hover)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.released.emit()

    def leaveEvent(self, event):
        self.hover = None
        self.update()


class SpriteFrames(QWidget):
    """Their walking sheet as 16×32 frames; click one to add it to the patio animation."""

    chosen = Signal(int)
    SCALE = 2

    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.columns = self.rows = 0
        self.setAccessibleName("Their sprite frames; click one to add it")

    def load(self, path):
        self.pixmap = QPixmap(str(path)) if path else QPixmap()
        self.columns = self.pixmap.width() // FRAME_SIZE[0]
        self.rows = self.pixmap.height() // FRAME_SIZE[1]
        self.setFixedSize(max(1, self.columns * FRAME_SIZE[0] * self.SCALE), max(1, self.rows * FRAME_SIZE[1] * self.SCALE))
        self.update()

    def frame(self, index):
        if not self.columns or not 0 <= index < self.columns * self.rows:
            return QPixmap()
        return self.pixmap.copy(index % self.columns * FRAME_SIZE[0], index // self.columns * FRAME_SIZE[1], *FRAME_SIZE)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(COLORS["highlight"]))
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.setPen(QPen(QColor(COLORS["rule"]), 1))
        for index in range(self.columns * self.rows):
            painter.drawRect(index % self.columns * FRAME_SIZE[0] * self.SCALE, index // self.columns * FRAME_SIZE[1] * self.SCALE,
                             FRAME_SIZE[0] * self.SCALE - 1, FRAME_SIZE[1] * self.SCALE - 1)

    def mousePressEvent(self, event):
        if not self.columns or event.button() != Qt.MouseButton.LeftButton:
            return
        x = int(event.position().x()) // (FRAME_SIZE[0] * self.SCALE)
        y = int(event.position().y()) // (FRAME_SIZE[1] * self.SCALE)
        if 0 <= x < self.columns and 0 <= y < self.rows:
            self.chosen.emit(y * self.columns + x)


class GamePatioPicker(QDialog):
    """Choose one of the game's own patios to start from."""

    def __init__(self, patios, textures, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start from a game patio — Pixelheart")
        self.patios = patios
        layout = QVBoxLayout(self)
        layout.addWidget(label("Pick a patio to start from. You can change every tile afterwards.", "muted", True))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(128, 128))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(8)
        self.list.setAccessibleName("Game patios")
        for index, patio in enumerate(patios, 1):
            back, front = render_patio(patio, textures)
            back.alpha_composite(front)
            item = QListWidgetItem(QIcon(to_pixmap(back.resize((128, 128), Image.Resampling.NEAREST))), f"Patio {index}")
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Start from this patio", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, 480)

    def chosen(self):
        row = self.list.currentRow()
        return deepcopy(self.patios[row]) if 0 <= row < len(self.patios) else None


class PatioEditor(QWidget):
    """Home → Spouse patio. ``changed`` carries the new patio, or None when it is removed."""

    changed = Signal(object)

    def __init__(self, patio, project_file, character, sprite_path=None, parent=None, *, view=None, ensure_saved=None):
        super().__init__(parent)
        self.patio = normalize_patio(patio) if patio is not None else None
        self.project_file = Path(project_file) if project_file else None
        self.character = character
        self.ensure_saved = ensure_saved
        view = view or {}
        self.season = view.get("season", "spring")
        self.layer = view.get("layer", "Back")
        self.tool = view.get("tool", "paint")
        self.own_sheet = view.get("own_sheet") or (self.patio or {}).get("sheets", {}).get(OWN_KEY)
        self.choices = []
        self._textures = {}
        self._context = None
        self._stroke = False
        self._stroke_changed = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.stack_host = QVBoxLayout()
        root.addLayout(self.stack_host, 1)
        self._build_empty()
        self._build_editor(view)
        game_connection().changed.connect(self.refresh_game)
        self.refresh_game()

    # --- layout ---------------------------------------------------------

    def _build_empty(self):
        self.empty, content = card("They don't have a patio yet",
                                   "After marriage, a 4×4 corner behind the farmhouse becomes theirs. "
                                   "Decorate it with the game's own pieces or your own tilesheet.")
        row = QHBoxLayout()
        self.farm_button = button("Start from the farm's ground", self.start_from_farm, "primary")
        self.game_patio_button = button("Start from a game patio…", self.start_from_game_patio)
        self.empty_button = button("Start empty", self.start_empty, "quiet")
        for widget in (self.farm_button, self.game_patio_button, self.empty_button):
            row.addWidget(widget)
        row.addStretch()
        content.addLayout(row)
        self.empty_game_note = label("", "hint", True)
        content.addWidget(self.empty_game_note)
        self.empty_find_button = button("Find Stardew Valley…", self.find_game, "quiet")
        content.addWidget(self.empty_find_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.empty_page = QWidget()
        page = QVBoxLayout(self.empty_page)
        page.setContentsMargins(0, 0, 0, 0)
        page.addWidget(self.empty)
        page.addStretch()
        self.stack_host.addWidget(self.empty_page)

    def _build_editor(self, view):
        # One scroll area for the whole patio, so a short window never overlaps its parts.
        self.editor = QScrollArea()
        self.editor.setWidgetResizable(True)
        self.editor.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        self.editor.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("After marriage, this 4×4 corner behind the farmhouse becomes theirs. The red ring is where they stand.", "hint", True), 1)
        self.start_over_button = QPushButton("Start over ▾")
        self.start_over_button.setObjectName("quiet")
        menu = QMenu(self.start_over_button)
        self.start_farm_action = menu.addAction("From the farm's ground", self.start_from_farm)
        self.start_game_action = menu.addAction("From a game patio…", self.start_from_game_patio)
        menu.addAction("Empty", self.start_empty)
        self.start_over_button.setMenu(menu)
        top.addWidget(self.start_over_button)
        top.addWidget(button("Remove patio", self.remove_patio, "quiet"))
        layout.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        self.canvas = PatioCanvas()
        self.canvas.pressed.connect(self.press)
        self.canvas.dragged.connect(self.drag)
        self.canvas.released.connect(self.release)
        self.canvas_area = QScrollArea()
        self.canvas_area.setObjectName("painterCanvasArea")
        self.canvas_area.setWidget(self.canvas)
        self.canvas_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.canvas_area, 1)
        tools = QHBoxLayout()
        tools.addWidget(label("Layer", "muted"))
        self.layer_group = QButtonGroup(self)
        self.layer_buttons = {}
        for name in LAYERS:
            widget = QToolButton()
            widget.setText(LAYER_NAMES[name])
            widget.setCheckable(True)
            widget.setObjectName("painterToggle")
            widget.setAccessibleName(f"{LAYER_NAMES[name]} layer")
            self.layer_group.addButton(widget)
            self.layer_buttons[name] = widget
            widget.clicked.connect(lambda _=False, name=name: self.set_layer(name))
            tools.addWidget(widget)
        tools.addStretch()
        left.addLayout(tools)
        tools = QHBoxLayout()
        tools.addWidget(label("Tool", "muted"))
        self.tool_group = QButtonGroup(self)
        self.tool_buttons = {}
        for name, text in TOOLS:
            widget = QToolButton()
            widget.setText(text)
            widget.setCheckable(True)
            widget.setObjectName("painterToggle")
            self.tool_group.addButton(widget)
            self.tool_buttons[name] = widget
            widget.clicked.connect(lambda _=False, name=name: self.set_tool(name))
            tools.addWidget(widget)
        self.fill_button = button("Fill layer", self.fill_layer, "quiet")
        tools.addWidget(self.fill_button)
        tools.addStretch()
        left.addLayout(tools)
        view_row = QHBoxLayout()
        self.season_choice = QComboBox()
        self.season_choice.setAccessibleName("Season to preview")
        for key, text in SEASON_NAMES:
            self.season_choice.addItem(text, key)
        self.season_choice.setCurrentIndex(max(0, self.season_choice.findData(self.season)))
        self.season_choice.currentIndexChanged.connect(self.change_season)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Zoom")
        for scale in (2, 3, 4):
            self.zoom.addItem(f"{scale}×", scale)
        self.zoom.setCurrentIndex(max(0, self.zoom.findData(view.get("zoom", 3))))
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        self.canvas.scale = self.zoom.currentData()
        view_row.addWidget(label("Season", "muted"))
        view_row.addWidget(self.season_choice)
        view_row.addWidget(label("Zoom", "muted"))
        view_row.addWidget(self.zoom)
        view_row.addStretch()
        left.addLayout(view_row)
        body.addLayout(left, 1)

        side = QVBoxLayout()
        side.addWidget(label("Pieces", "sectionTitle"))
        self.source = QComboBox()
        self.source.setAccessibleName("Where pieces come from")
        self.source.currentIndexChanged.connect(self.select_source)
        side.addWidget(self.source)
        self.palette = PatioPalette()
        palette_scroll = QScrollArea()
        palette_scroll.setWidget(self.palette)
        palette_scroll.setMinimumWidth(16 * PatioPalette.SCALE * 8 + 24)
        palette_scroll.setMinimumHeight(260)
        side.addWidget(palette_scroll, 1)
        own_row = QHBoxLayout()
        self.open_sheet_button = button("Open tilesheet PNG…", lambda: self.open_own_sheet(), "quiet")
        self.paint_sheet_button = button("Paint tilesheet…", self.paint_own_sheet, "quiet")
        own_row.addWidget(self.open_sheet_button)
        own_row.addWidget(self.paint_sheet_button)
        side.addLayout(own_row)
        self.game_note = label("", "hint", True)
        side.addWidget(self.game_note)
        self.find_button = button("Find Stardew Valley…", self.find_game, "quiet")
        side.addWidget(self.find_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addLayout(side)
        layout.addLayout(body, 1)
        self.notice = label("", "notice", True)
        layout.addWidget(self.notice)
        layout.addWidget(self._build_activity())
        self.stack_host.addWidget(self.editor)
        self.layer_buttons[self.layer].setChecked(True)
        self.tool_buttons[self.tool].setChecked(True)

    def _build_activity(self):
        frame, content = card()
        content.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(label("On the patio they", "sectionTitle"))
        self.activity = QComboBox()
        self.activity.setAccessibleName("What they do on the patio")
        self.activity.addItem("stand and look around", "stand")
        self.activity.addItem("play an animation", "animate")
        self.activity.currentIndexChanged.connect(self.change_activity)
        row.addWidget(self.activity)
        row.addStretch()
        content.addLayout(row)
        self.animation_panel = QWidget()
        panel = QHBoxLayout(self.animation_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(14)
        self.sprite_frames = SpriteFrames()
        self.sprite_frames.chosen.connect(self.add_frame)
        frames_scroll = QScrollArea()
        frames_scroll.setWidget(self.sprite_frames)
        frames_scroll.setFixedSize(4 * FRAME_SIZE[0] * SpriteFrames.SCALE + 24, 160)
        picker = QVBoxLayout()
        picker.addWidget(label("Click frames from their sprite sheet", "muted", True))
        picker.addWidget(frames_scroll)
        self.no_sprite = label("Add their sprite sheet in Artwork to choose frames.", "hint", True)
        picker.addWidget(self.no_sprite)
        panel.addLayout(picker)
        chosen = QVBoxLayout()
        self.frame_list = QListWidget()
        self.frame_list.setAccessibleName("Animation frames in order")
        self.frame_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.frame_list.setFlow(QListWidget.Flow.LeftToRight)
        self.frame_list.setWrapping(False)
        self.frame_list.setMovement(QListWidget.Movement.Static)
        self.frame_list.setIconSize(QSize(32, 64))
        self.frame_list.setSpacing(4)
        self.frame_list.setFixedHeight(128)
        self.frame_list.currentRowChanged.connect(self.select_frame)
        chosen.addWidget(self.frame_list)
        frame_row = QHBoxLayout()
        self.duration = QSpinBox()
        self.duration.setRange(1, 60000)
        self.duration.setSingleStep(50)
        self.duration.setSuffix(" ms")
        self.duration.setAccessibleName("Frame duration")
        self.duration.valueChanged.connect(self.change_duration)
        frame_row.addWidget(self.duration)
        self.remove_frame_button = button("Remove frame", self.remove_frame, "quiet")
        frame_row.addWidget(self.remove_frame_button)
        chosen.addLayout(frame_row)
        panel.addLayout(chosen, 1)
        offsets = QVBoxLayout()
        offsets.addWidget(label("Nudge (pixels)", "muted"))
        self.offset_x, self.offset_y = QSpinBox(), QSpinBox()
        for axis, widget in (("Right", self.offset_x), ("Down", self.offset_y)):
            widget.setRange(-128, 128)
            widget.setAccessibleName(f"Nudge {axis.lower()}")
            widget.valueChanged.connect(self.change_offset)
            line = QHBoxLayout()
            line.addWidget(label(axis, "muted"))
            line.addWidget(widget)
            offsets.addLayout(line)
        self.preview = label("")
        self.preview.setFixedSize(FRAME_SIZE[0] * 3 + 16, FRAME_SIZE[1] * 3 + 16)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        offsets.addWidget(self.preview)
        offsets.addStretch()
        panel.addLayout(offsets)
        content.addWidget(self.animation_panel)
        self._preview_frame = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.advance_preview)
        return frame

    # --- state ----------------------------------------------------------

    @property
    def root(self):
        return self.project_file.parent if self.project_file else None

    def view_state(self):
        return {"season": self.season, "layer": self.layer, "tool": self.tool,
                "zoom": self.zoom.currentData(), "own_sheet": self.own_sheet, "source": self.source.currentText()}

    def set_sprite(self, path):
        self.sprite_frames.load(path if path and Path(path).is_file() else None)
        self.no_sprite.setVisible(not self.sprite_frames.columns)
        self.refresh_frames()

    def refresh_game(self):
        folder = game_folder()
        message = ("" if folder else
                   "Connect your game to use its own outdoor pieces and see your farm around the patio.")
        for widget in (self.game_note, self.empty_game_note):
            widget.setText(message)
            widget.setVisible(bool(message))
        for widget in (self.find_button, self.empty_find_button):
            widget.setVisible(not folder)
        for widget in (self.farm_button, self.game_patio_button):
            widget.setVisible(bool(folder))
        self.empty_button.setObjectName("quiet" if folder else "primary")
        self.empty_button.style().unpolish(self.empty_button)
        self.empty_button.style().polish(self.empty_button)
        self.start_farm_action.setEnabled(bool(folder))
        self.start_game_action.setEnabled(bool(folder))
        self._textures.clear()
        self._context = None
        previous = self.source.currentText()
        self.choices = []
        if folder:
            try:
                self.choices = [(text, sheet, sheet_key(sheet["image"])) for text, sheet in game_sheet_choices(folder)]
            except (OSError, ValueError) as exc:
                self.game_note.setText(f"Pixelheart couldn't read the game's pieces: {exc}")
                self.game_note.show()
        self.choices.append(("Your tilesheet", self.own_sheet, OWN_KEY))
        self.source.blockSignals(True)
        self.source.clear()
        for text, *_ in self.choices:
            self.source.addItem(text)
        self.source.setCurrentIndex(max(0, self.source.findText(previous)))
        self.source.blockSignals(False)
        self.select_source()
        self.show_state()

    def show_state(self):
        self.empty_page.setVisible(self.patio is None)
        self.editor.setVisible(self.patio is not None)
        if self.patio is None:
            return
        animation = self.patio["animation"]
        self.activity.blockSignals(True)
        self.activity.setCurrentIndex(1 if animation["frames"] else self.activity.currentIndex())
        self.activity.blockSignals(False)
        self.animation_panel.setVisible(self.activity.currentData() == "animate")
        for widget, value in ((self.offset_x, animation["offset"][0]), (self.offset_y, animation["offset"][1])):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.refresh_frames()
        self.render()

    def texture(self, key):
        """The picture for one of the patio's sheets in the previewed season, or None."""
        sheet = (self.patio or {}).get("sheets", {}).get(key) or next(
            (record for _text, record, choice in self.choices if choice == key and record), None)
        if sheet is None:
            return None
        cache = (key, sheet.get("asset") or sheet.get("image"), self.season)
        if cache not in self._textures:
            try:
                if sheet["source"] == "own":
                    self._textures[cache] = own_sheet_image({"sheets": {OWN_KEY: sheet}}, self.root) if self.root else None
                else:
                    folder = game_folder()
                    self._textures[cache] = game_texture(folder, sheet["image"], self.season) if folder else None
            except (OSError, ValueError):
                self._textures[cache] = None
        return self._textures[cache]

    def context(self):
        if self._context is None:
            folder = game_folder()
            self._context = False
            if folder:
                try:
                    self._context = farm_context(folder, self.season)
                except (OSError, ValueError):
                    self._context = False
        return self._context or None

    def render(self):
        textures = {key: image for key in self.patio["sheets"] if (image := self.texture(key)) is not None}
        back, front = render_patio(self.patio, textures)
        context = self.context()
        if context:
            image, origin = context["back"].copy(), context["origin"]
            outside = context["front"].copy()
            outside.paste((0, 0, 0, 0), (*origin, origin[0] + SIZE * 16, origin[1] + SIZE * 16))
        else:
            image, origin = Image.new("RGBA", ((SIZE + 2) * 16, (SIZE + 2) * 16), "#e0dacf"), (16, 16)
            outside = None
        image.paste((0, 0, 0, 0), (*origin, origin[0] + SIZE * 16, origin[1] + SIZE * 16))
        image.alpha_composite(back, origin)
        if outside is not None:
            image.alpha_composite(outside)
        image.alpha_composite(front, origin)
        self.canvas.missing = {(index % SIZE, index // SIZE) for name in LAYERS
                               for index, cell in enumerate(self.patio["layers"][name])
                               if cell and cell[0] not in textures}
        self.canvas.stand = tuple(self.patio["stand"])
        self.canvas.load(image, origin)
        self._fit_canvas_area()
        issues = patio_issues(self.patio, self.character, self.root)
        self.notice.setText(next((message for level, _field, message in issues if level == "error"), ""))
        self.notice.setVisible(bool(self.notice.text()))

    def change_zoom(self, *_):
        self.canvas.set_scale(self.zoom.currentData())
        self._fit_canvas_area()

    def _fit_canvas_area(self):
        self.canvas_area.setMinimumHeight(min(self.canvas.height() + 8, 720))

    def commit(self):
        if self.patio is not None:
            self.render()
        self.changed.emit(prune_sheets(deepcopy(self.patio)) if self.patio is not None else None)

    # --- starting points --------------------------------------------------

    def start_from_farm(self):
        folder = game_folder()
        if not folder:
            return
        try:
            patio = farm_ground(folder)
        except (OSError, ValueError) as exc:
            self.show_problem(f"Pixelheart couldn't read your farm: {exc}")
            return
        self._start(patio)

    def start_from_game_patio(self, index=None):
        folder = game_folder()
        if not folder:
            return
        try:
            patios = game_patios(folder)
        except (OSError, ValueError) as exc:
            self.show_problem(f"Pixelheart couldn't read the game's patios: {exc}")
            return
        if not patios:
            self.show_problem("The game's patio map has no patios to start from.")
            return
        if index is None:
            textures = {}
            for patio in patios:
                for key, sheet in patio["sheets"].items():
                    if key not in textures:
                        try:
                            textures[key] = game_texture(folder, sheet["image"], self.season)
                        except (OSError, ValueError):
                            pass
            dialog = GamePatioPicker(patios, textures, self)
            try:
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                patio = dialog.chosen()
            finally:
                dialog.deleteLater()
        else:
            patio = deepcopy(patios[index])
        if patio is not None:
            self._start(patio)

    def start_empty(self):
        self._start(new_patio())

    def _start(self, patio):
        if self.own_sheet and self.patio is None:
            patio["sheets"].setdefault(OWN_KEY, dict(self.own_sheet))
        self.patio = normalize_patio(patio)
        self.show_state()
        self.commit()

    def remove_patio(self):
        self.patio = None
        self.show_state()
        self.commit()

    def show_problem(self, message):
        target = self.notice if self.patio is not None else self.empty_game_note
        target.setText(message)
        target.show()

    def find_game(self):
        open_find_game(self.window())

    # --- palette ----------------------------------------------------------

    def current_choice(self):
        index = self.source.currentIndex()
        return self.choices[index] if 0 <= index < len(self.choices) else (None, None, None)

    def select_source(self, *_):
        _text, sheet, key = self.current_choice()
        own = key == OWN_KEY
        self.open_sheet_button.setVisible(own)
        self.paint_sheet_button.setVisible(own)
        self.paint_sheet_button.setText("Paint tilesheet…")
        image = self.texture(key) if sheet else None
        self.palette.load(image, sheet["columns"] if sheet else 0, sheet["rows"] if sheet else 0)

    def _use_own(self, sheet):
        if self.patio is not None:
            use_own_sheet(self.patio, sheet)
        self.own_sheet = sheet
        self.choices[-1] = ("Your tilesheet", sheet, OWN_KEY)
        self.source.setCurrentIndex(len(self.choices) - 1)
        self.select_source()

    def _saved_root(self):
        if self.root is None and self.ensure_saved is not None and self.ensure_saved():
            project_file = getattr(self.window(), "project_file", None)
            self.project_file = Path(project_file) if project_file else None
        if self.root is None:
            self.show_problem("Save your character first so your tilesheet can be kept with them.")
        return self.root

    def open_own_sheet(self, path=None):
        from .map_workshop import inspect_tilesheet
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open your 16-pixel tilesheet", "", "PNG tilesheet (*.png)")
            if not path:
                return False
        return self._take_own_png(lambda: inspect_tilesheet(path))

    def _take_own_png(self, inspect):
        if self._saved_root() is None:
            return False
        try:
            sheet = inspect()
            record = import_own_sheet(sheet["bytes"], self.root, sheet["columns"], sheet["height"] // 16)
            self._use_own(record)
        except (OSError, ValueError) as exc:
            self.show_problem(str(exc))
            return False
        if self.patio is not None:
            self.commit()
        return True

    def paint_own_sheet(self):
        from pixelheart_core.pixel_layers import LayerFileError, blank_painting, open_painting, save_layers
        from .map_workshop import MAX_SHEET_BYTES, inspect_tilesheet_bytes
        from .pixel_painter import PixelPainterDialog, choose_tilesheet_size
        if self._saved_root() is None:
            return False
        try:
            if self.own_sheet:
                image = own_sheet_image({"sheets": {OWN_KEY: self.own_sheet}}, self.root)
                from io import BytesIO
                stream = BytesIO()
                image.save(stream, format="PNG")
                document, restored = open_painting(stream.getvalue(), "tilesheet", project_root=self.root)
            else:
                size = choose_tilesheet_size(self)
                if size is None:
                    return False
                document, restored = blank_painting("tilesheet", *size), False
        except (OSError, ValueError) as exc:
            self.show_problem(str(exc))
            return False

        def check(payload):
            sheet = inspect_tilesheet_bytes(payload)
            if self.patio is not None:
                use_own_sheet(deepcopy(self.patio), {"columns": sheet["columns"], "rows": sheet["height"] // 16})
        dialog = PixelPainterDialog(document, kind="tilesheet", title="Paint your patio tilesheet", context="16 × 16 patio tiles",
                                    save_text="Use painted tilesheet", max_bytes=MAX_SHEET_BYTES, validate=check, parent=self)
        if restored:
            dialog.show_message("Your layers from last time are back.")
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_png is None:
                return False
            payload = dialog.result_png
            if not self._take_own_png(lambda: inspect_tilesheet_bytes(payload)):
                return False
            try:
                save_layers(self.root, document, payload)
            except LayerFileError as exc:
                self.show_problem(f"Your tilesheet is in use, but its layers could not be kept: {exc}")
            return True
        finally:
            dialog.deleteLater()

    # --- painting ---------------------------------------------------------

    def set_layer(self, name):
        self.layer = name
        self.layer_buttons[name].setChecked(True)

    def set_tool(self, name):
        self.tool = name
        self.tool_buttons[name].setChecked(True)

    def change_season(self, *_):
        self.season = self.season_choice.currentData()
        self._context = None
        self.select_source()
        if self.patio is not None:
            self.render()

    def _apply(self, x, y):
        if self.patio is None:
            return False
        if self.tool == "stand":
            if self.patio["stand"] == [x, y]:
                return False
            self.patio["stand"] = [x, y]
            return True
        if self.tool == "erase":
            return erase(self.patio, self.layer, x, y)
        _text, sheet, key = self.current_choice()
        if sheet is None or self.palette.image is None:
            self.show_problem("Choose where pieces come from first." if key != OWN_KEY else
                              "Open or paint your tilesheet first.")
            return False
        try:
            return paint(self.patio, self.layer, x, y, key, sheet, self.palette.tiles())
        except WorldError as exc:
            self.show_problem(str(exc))
            return False

    def press(self, x, y):
        self._stroke, self._stroke_changed = True, False
        if self._apply(x, y):
            self._stroke_changed = True
            self.render()

    def drag(self, x, y):
        if self._stroke and self.tool != "stand" and self._apply(x, y):
            self._stroke_changed = True
            self.render()

    def release(self):
        if self._stroke and self._stroke_changed:
            self.commit()
        self._stroke = self._stroke_changed = False

    def fill_layer(self):
        _text, sheet, key = self.current_choice()
        if self.patio is None or sheet is None or self.palette.image is None:
            self.show_problem("Choose a piece to fill with first.")
            return
        if fill(self.patio, self.layer, key, sheet, self.palette.tiles()[0][0]):
            self.commit()

    # --- activity ---------------------------------------------------------

    def change_activity(self, *_):
        animate = self.activity.currentData() == "animate"
        self.animation_panel.setVisible(animate)
        if not animate and self.patio is not None and self.patio["animation"]["frames"]:
            self.patio["animation"]["frames"] = []
            self.refresh_frames()
            self.commit()

    def refresh_frames(self):
        frames = self.patio["animation"]["frames"] if self.patio else []
        row = self.frame_list.currentRow()
        self.frame_list.blockSignals(True)
        self.frame_list.clear()
        for index, (sprite, duration) in enumerate(frames, 1):
            item = QListWidgetItem(f"{duration} ms")
            item.setToolTip(f"Step {index}: sprite frame {sprite} for {duration} ms")
            pixmap = self.sprite_frames.frame(sprite)
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap.scaled(32, 64)))
            self.frame_list.addItem(item)
        self.frame_list.setCurrentRow(min(max(row, 0), len(frames) - 1) if frames else -1)
        self.frame_list.blockSignals(False)
        self.select_frame(self.frame_list.currentRow())
        self._preview_frame = 0
        self.advance_preview()

    def select_frame(self, row):
        frames = self.patio["animation"]["frames"] if self.patio else []
        valid = 0 <= row < len(frames)
        self.duration.blockSignals(True)
        self.duration.setValue(frames[row][1] if valid else 100)
        self.duration.blockSignals(False)
        self.duration.setEnabled(valid)
        self.remove_frame_button.setEnabled(valid)

    def add_frame(self, sprite):
        if self.patio is None or len(self.patio["animation"]["frames"]) >= 32:
            return
        self.patio["animation"]["frames"].append([sprite, 100])
        self.refresh_frames()
        self.frame_list.setCurrentRow(self.frame_list.count() - 1)
        self.commit()

    def remove_frame(self):
        row = self.frame_list.currentRow()
        if self.patio is not None and 0 <= row < len(self.patio["animation"]["frames"]):
            del self.patio["animation"]["frames"][row]
            self.refresh_frames()
            self.commit()

    def change_duration(self, value):
        row = self.frame_list.currentRow()
        if self.patio is not None and 0 <= row < len(self.patio["animation"]["frames"]):
            self.patio["animation"]["frames"][row][1] = value
            self.frame_list.item(row).setText(f"{value} ms")
            self.commit()

    def change_offset(self, *_):
        if self.patio is not None:
            self.patio["animation"]["offset"] = [self.offset_x.value(), self.offset_y.value()]
            self.advance_preview(restart=False)
            self.commit()

    def advance_preview(self, restart=True):
        frames = self.patio["animation"]["frames"] if self.patio else []
        canvas = QPixmap(self.preview.size())
        canvas.fill(QColor(COLORS["highlight"]))
        if frames and self.sprite_frames.columns:
            index = self._preview_frame % len(frames)
            sprite, duration = frames[index]
            frame = self.sprite_frames.frame(sprite).scaled(FRAME_SIZE[0] * 3, FRAME_SIZE[1] * 3)
            painter = QPainter(canvas)
            x, y = self.patio["animation"]["offset"]
            painter.drawPixmap(8 + x * 3 // 4, 8 + y * 3 // 4, frame)
            painter.end()
            if restart:
                self._preview_frame += 1
                self.preview_timer.start(max(30, duration))
        self.preview.setPixmap(canvas)
