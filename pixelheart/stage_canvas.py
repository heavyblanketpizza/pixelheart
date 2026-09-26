"""An accessible tile blocking board; no game geometry is inferred."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import QEvent, Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QImage
from PySide6.QtWidgets import QWidget


class StageCanvas(QWidget):
    actorSelected = Signal(int)
    actorMoved = Signal(int, int, int)
    gestureStarted = Signal()
    gestureFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.actors = []
        self.selected = 0
        self.dragging = False
        self.bounds = (0, 0, 24, 16)
        self.names = {}
        self.background = QImage()
        self.map_size = None
        self.background_key = None
        self.preview_note = "Staging grid · verify walkable tiles in-game"
        self.setMinimumHeight(260)
        self.setMaximumHeight(360)
        self.setMinimumWidth(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Scene staging grid. Select a character, click a tile to move them, or use arrow keys.")
        self.setToolTip("Click a character to select them. Click a tile or use arrow keys to move them. Coordinates refer to the game's tiles.")

    def load(self, actors, names=None, *, fit=False):
        self.actors = deepcopy(actors)
        if names is not None:
            self.names = names
        self.selected = min(self.selected, max(0, len(actors) - 1))
        if fit:
            self.fit()
        self.update()

    def fit(self):
        xs = [actor.get("x", 0) for actor in self.actors] or [8, 16]
        ys = [actor.get("y", 0) for actor in self.actors] or [5, 10]
        left, top = max(0, min(xs) - 5), max(0, min(ys) - 5)
        self.bounds = (left, top, max(16, max(xs) - left + 6), max(12, max(ys) - top + 6))
        self.update()

    def set_map(self, path=None):
        key = str(path) if path else None
        if key == self.background_key:
            return
        self.background_key = key
        self.background = QImage()
        self.map_size = None
        self.preview_note = "Staging grid · verify walkable tiles in-game"
        if path:
            try:
                from pixelheart_core.world import map_bundle, render_map_preview
                bundle = map_bundle(path)
                pixels = render_map_preview(path).convert("RGBA")
                self.background = QImage(pixels.tobytes(), pixels.width, pixels.height, QImage.Format.Format_RGBA8888).copy()
                self.map_size = (bundle["width"], bundle["height"])
                self.preview_note = "Your supplied map · verify walkability in-game"
                self.bounds = (0, 0, max(bundle["width"], max((actor.get("x", 0) + 2 for actor in self.actors), default=0)),
                               max(bundle["height"], max((actor.get("y", 0) + 2 for actor in self.actors), default=0)))
            except (ValueError, OSError) as exc:
                self.preview_note = "Grid only · this map cannot be previewed"
                self.setToolTip(str(exc))
        self.update()

    def geometry_grid(self):
        x, y, columns, rows = self.bounds
        scale = min((self.width() - 48) / columns, (self.height() - 56) / rows)
        return x, y, max(0.2, scale), 32, 26

    def tile_point(self, x, y):
        left, top, scale, ox, oy = self.geometry_grid()
        return QPointF(ox + (x - left + .5) * scale, oy + (y - top + .5) * scale)

    def tile_at(self, position):
        left, top, scale, ox, oy = self.geometry_grid()
        x, y = int((position.x() - ox) // scale) + left, int((position.y() - oy) // scale) + top
        if position.x() < ox or position.y() < oy or not (left <= x < left + self.bounds[2] and top <= y < top + self.bounds[3]):
            return None
        return min(1000, max(0, x)), min(1000, max(0, y))

    def select_actor(self, index):
        if 0 <= index < len(self.actors):
            self.selected = index
            self.update()

    def move_selected(self, x, y):
        if not self.actors:
            return
        actor = self.actors[self.selected]
        x, y = min(1000, max(0, x)), min(1000, max(0, y))
        if (actor.get("x"), actor.get("y")) != (x, y):
            actor.update(x=x, y=y)
            self.actorMoved.emit(self.selected, x, y)
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._finish_gesture()
        self.setFocus()
        tile = self.tile_at(event.position())
        if tile is None or not self.actors:
            return
        matches = [index for index, actor in enumerate(self.actors) if (actor.get("x"), actor.get("y")) == tile]
        if matches:
            self.selected = matches[0]
            self.actorSelected.emit(self.selected)
        self.dragging = True
        self.gestureStarted.emit()
        if not matches:
            self.move_selected(*tile)
        self.update()

    def mouseMoveEvent(self, event):
        if self.dragging and (tile := self.tile_at(event.position())) is not None:
            self.move_selected(*tile)

    def mouseReleaseEvent(self, event):
        self._finish_gesture()

    def _finish_gesture(self):
        if self.dragging:
            self.dragging = False
            self.gestureFinished.emit()

    def focusOutEvent(self, event):
        self._finish_gesture()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._finish_gesture()
        super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.UngrabMouse and getattr(self, "dragging", False):
            self._finish_gesture()
        return super().event(event)

    def keyPressEvent(self, event):
        was_dragging = self.dragging
        self._finish_gesture()
        if was_dragging and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        delta = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}.get(event.key())
        if delta and self.actors:
            actor = self.actors[self.selected]
            self.move_selected(actor["x"] + delta[0], actor["y"] + delta[1])
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f6f2e8"))
        left, top, scale, ox, oy = self.geometry_grid()
        columns, rows = self.bounds[2:]
        if not self.background.isNull():
            painter.drawImage(QRectF(ox - left * scale, oy - top * scale, self.map_size[0] * scale, self.map_size[1] * scale), self.background)
        painter.setPen(QPen(QColor("#ded6c4"), 1))
        stride = max(1, int(12 / scale))
        for column in range(0, columns + 1, stride):
            x = ox + column * scale
            painter.drawLine(QPointF(x, oy), QPointF(x, oy + rows * scale))
        for row in range(0, rows + 1, stride):
            y = oy + row * scale
            painter.drawLine(QPointF(ox, y), QPointF(ox + columns * scale, y))
        painter.setPen(QColor("#6a7064"))
        painter.drawText(QPointF(ox, 17), f"Tile {left}, {top} · {self.preview_note}")
        for index, actor in enumerate(self.actors):
            point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
            radius = max(5, min(scale * .42, 15))
            selected = index == self.selected
            painter.setBrush(QColor("#496b51" if selected else "#ba8b51"))
            painter.setPen(QPen(QColor("#293e30" if selected else "#8a6436"), 2))
            painter.drawEllipse(point, radius, radius)
            dx, dy = [(0, -1), (1, 0), (0, 1), (-1, 0)][actor.get("facing", 2) % 4]
            tip = point + QPointF(dx * (radius + 5), dy * (radius + 5))
            painter.setBrush(QColor("#293e30"))
            painter.drawPolygon(QPolygonF([tip, point + QPointF(-dy * 4, dx * 4), point + QPointF(dy * 4, -dx * 4)]))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2), Qt.AlignmentFlag.AlignCenter, str(index + 1))
        if self.actors:
            actor = self.actors[self.selected]
            name = self.names.get(actor.get("name"), actor.get("name") or "Unnamed character")
            painter.setPen(QColor("#364b3b"))
            painter.drawText(QPointF(12, self.height() - 8), f"Selected: {name} · tile {actor.get('x', 0)}, {actor.get('y', 0)} · click a tile or use arrow keys")
