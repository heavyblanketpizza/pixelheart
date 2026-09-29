"""A routine's stops on the map of their place: click where the character stands."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .skin import pixel_family
from .stage_canvas import StageCanvas, image_from_pixels
from .widgets import button, label

FACINGS = (("up", "↑"), ("right", "→"), ("down", "↓"), ("left", "←"))


def _stop_name(row):
    return f"stop{row}"


class RouteCanvas(StageCanvas):
    """Stops on one place, numbered in routine order and joined by dotted lines."""

    empty_caption = "Select a stop to place it on the map."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setAccessibleName("Routine map. Click a tile to move the selected stop there, or drag a stop.")

    def route_labels(self):
        return {actor["name"]: str(row + 1) for actor, row in zip(self.actors, self.rows)}

    def paint_order(self):
        # The selected stop is drawn last, so it wins a shared tile when clicked.
        return sorted(range(len(self.actors)),
                      key=lambda index: (self.actors[index].get("y", 0), index == self.selected, index))

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.actors:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(self.scene_rect())
        scale = self.geometry_grid()[2]
        points = [self.tile_point(actor.get("x", 0), actor.get("y", 0)) for actor in self.actors]
        pen = QPen(QColor(255, 240, 175, 200), max(1.5, min(3.0, scale / 8)), Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)
        font = QFont(pixel_family())
        font.setPixelSize(11)
        painter.setFont(font)
        for index, (point, row) in enumerate(zip(points, self.rows)):
            badge = QRectF(point.x() + scale * 0.35, point.y() - scale * 1.55, 18, 16)
            selected = index == self.selected
            painter.setPen(QPen(QColor("#26221d"), 1))
            painter.setBrush(QColor("#fff0af") if selected else QColor("#fdfbf7"))
            painter.drawRoundedRect(badge, 3, 3)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(row + 1))
        painter.end()


class RouteMapPanel(QWidget):
    """The selected stop's place, its stops, facing buttons and Fit map."""

    stopSelected = Signal(int)
    stopMoved = Signal(int, int, int)
    facingChosen = Signal(int, str)
    gestureStarted = Signal()
    gestureFinished = Signal()

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.map_source = None
        self.sprite = None
        self._location = None
        self._selected_row = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        heading = QHBoxLayout()
        self.place_label = label("Choose a stop", "sectionTitle")
        heading.addWidget(self.place_label)
        heading.addStretch()
        self.fit_button = button("Fit map", self.fit, "quiet")
        heading.addWidget(self.fit_button)
        root.addLayout(heading)
        self.canvas = RouteCanvas()
        self.canvas.header_visible = False
        self.canvas.setMinimumHeight(240 if compact else 340)
        root.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        controls.addWidget(label("Facing", "muted"))
        self.facing_buttons = {}
        for facing, arrow in FACINGS:
            widget = button(arrow, lambda _=False, facing=facing: self._face(facing), "quiet")
            widget.setAccessibleName(f"Face {facing}")
            widget.setToolTip(f"Stand facing {facing}")
            controls.addWidget(widget)
            self.facing_buttons[facing] = widget
        controls.addStretch()
        root.addLayout(controls)
        self.note = label("", "hint", True)
        root.addWidget(self.note)
        self.canvas.actorSelected.connect(self._actor_selected)
        self.canvas.actorMoved.connect(self._actor_moved)
        self.canvas.gestureStarted.connect(self.gestureStarted)
        self.canvas.gestureFinished.connect(self.gestureFinished)
        self._set_enabled(False)

    def _set_enabled(self, enabled):
        for widget in self.facing_buttons.values():
            widget.setEnabled(enabled)

    def set_map_source(self, source):
        """``source(location)`` returns a ``load_route_map`` result."""
        self.map_source = source
        self._location = None

    def set_sprite(self, image):
        self.sprite = image if image is not None and not image.isNull() else None
        self._location = None

    def fit(self):
        self.canvas.fit_map()

    def _load_place(self, location):
        result = self.map_source(location) if self.map_source is not None and location else None
        if result is None:
            result = {"image": None, "foreground": None, "map_size": None, "label": location or "No place",
                      "note": f"Connect your game to see {location}." if location else "Choose a place for this stop."}
        self.place_label.setText(result["label"] or location)
        self.note.setText(result["note"])
        self.canvas.set_scene_preview(background=image_from_pixels(result["image"]), map_size=result["map_size"],
                                      location_label=result["label"] or location, source_note=result["note"],
                                      foreground=image_from_pixels(result.get("foreground")))
        self._location = location
        return result["map_size"] is not None

    def show_route(self, records, selected_row):
        """Show the selected stop's place and every stop there."""
        if selected_row is None or not 0 <= selected_row < len(records):
            self._selected_row = None
            self.canvas.rows = []
            self.canvas.load([])
            self.place_label.setText("Choose a stop")
            self._set_enabled(False)
            return
        location = records[selected_row].get("location", "")
        fresh = location != self._location
        if fresh:
            has_map = self._load_place(location)
        rows = [row for row, record in enumerate(records) if record.get("location", "") == location]
        actors = [{"name": _stop_name(row), "x": int(records[row].get("x", 0) or 0),
                   "y": int(records[row].get("y", 0) or 0), "facing": records[row].get("facing", "down")}
                  for row in rows]
        self.canvas.rows = rows
        self.canvas.names = {_stop_name(row): f"Stop {row + 1}" for row in rows}
        if self.sprite is not None:
            self.canvas.sprites = {_stop_name(row): self.sprite for row in rows}
        else:
            self.canvas.sprites = {}
        self.canvas.selected = rows.index(selected_row)
        self.canvas.load(actors, fit=False)
        if fresh:
            self.canvas.fit_map() if has_map else self.canvas.fit()
        self._selected_row = selected_row
        self._set_enabled(True)

    def _actor_selected(self, index):
        if 0 <= index < len(self.canvas.rows):
            self._selected_row = self.canvas.rows[index]
            self.stopSelected.emit(self._selected_row)

    def _actor_moved(self, index, x, y):
        if 0 <= index < len(self.canvas.rows):
            self.stopMoved.emit(self.canvas.rows[index], x, y)

    def _face(self, facing):
        if self._selected_row is None or not self.canvas.actors:
            return
        self.canvas.actors[self.canvas.selected]["facing"] = facing
        self.canvas.update()
        self.facingChosen.emit(self._selected_row, facing)
