"""A routine's stops on the map of their place: click where the character stands."""
from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, QPointF, QRectF, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .skin import pixel_family
from .stage_canvas import StageCanvas, image_from_pixels
from .widgets import button, label

FACINGS = (("up", "↑"), ("right", "→"), ("down", "↓"), ("left", "←"))


def _stop_name(row):
    return f"stop{row}"


def _no_map(location, note):
    return {"image": None, "foreground": None, "map_size": None, "label": location or "No place", "note": note}


class _MapRelay(QObject):
    """Carries a finished map from the loading thread back to the panel."""

    ready = Signal(int, object)


def _load_map(relay, token, location, job):
    try:
        result = job()
        result = {**result, "image": image_from_pixels(result["image"]),
                  "foreground": image_from_pixels(result.get("foreground"))}
    except Exception as exc:
        # Report loading failures on the panel; never raise through Qt.
        result = _no_map(location, f"This map couldn't be read: {exc}")
    try:
        relay.ready.emit(token, result)
    except RuntimeError:
        pass  # The panel closed while the map was loading.


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
        self.loading = False
        self._location = None
        self._selected_row = None
        self._token = 0
        self._relay = _MapRelay(self)
        self._relay.ready.connect(self._map_ready)
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
        """``source(location)`` returns a job that makes a ``load_route_map`` result.

        Jobs run away from the UI thread, since a large game map takes a moment to draw.
        """
        self.map_source = source
        self._location = None

    def set_sprite(self, image):
        self.sprite = image if image is not None and not image.isNull() else None

    def fit(self):
        self.canvas.fit_map()

    def _load_place(self, location):
        self._location = location
        self._token += 1
        job = self.map_source(location) if self.map_source is not None and location else None
        if job is None:
            self.loading = False
            self._show_place(_no_map(location, f"Connect your game to see {location}." if location
                                     else "Choose a place for this stop."))
            return
        self.loading = True
        self._show_place(_no_map(location, f"Loading the map of {location}…"))
        QThreadPool.globalInstance().start(partial(_load_map, self._relay, self._token, location, job))

    def _show_place(self, result):
        location = result["label"] or self._location
        self.place_label.setText(location)
        self.note.setText(result["note"])
        self.canvas.set_scene_preview(background=result["image"], map_size=result["map_size"], sprites=self.canvas.sprites,
                                      location_label=location, source_note=result["note"],
                                      foreground=result.get("foreground"))

    def _map_ready(self, token, result):
        if token != self._token:
            return  # A place the player has already left.
        self.loading = False
        self._show_place(result)
        if result["map_size"] is not None:
            self.canvas.fit_map()

    def show_route(self, records, selected_row):
        """Show the selected stop's place and every stop there."""
        if selected_row is None or not 0 <= selected_row < len(records):
            if self.loading:
                self._token += 1
                self._location = None
                self.loading = False
            self._selected_row = None
            self.canvas.rows = []
            self.canvas.load([])
            self.place_label.setText("Choose a stop")
            self._set_enabled(False)
            return
        location = records[selected_row].get("location", "")
        fresh = location != self._location
        if fresh:
            self._load_place(location)
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
            # Frame the stops until their map arrives.
            self.canvas.fit()
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
