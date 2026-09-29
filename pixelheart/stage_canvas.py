"""A native scene preview of local artwork with precise game-tile placement."""
from __future__ import annotations

from copy import deepcopy
import math

from PySide6.QtCore import QEvent, Qt, Signal, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QImage, QFontMetrics
from PySide6.QtWidgets import QWidget, QSizePolicy


def image_from_pixels(pixels):
    """Detach Pillow preview pixels from their source image buffer."""
    if pixels is None:
        return None
    pixels = pixels.convert("RGBA")
    return QImage(pixels.tobytes(), pixels.width, pixels.height, QImage.Format.Format_RGBA8888).copy()


class StageCanvas(QWidget):
    empty_caption = "Add an actor to stage this scene."
    actorSelected = Signal(int)
    actorMoved = Signal(int, int, int)
    gestureStarted = Signal()
    gestureFinished = Signal()
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.actors = []
        self.selected = 0
        self.dragging = False
        self._drag_offset = QPointF()
        self.panning = False
        self._pan_position = QPointF()
        self._space_pressed = False
        self.bounds = (0, 0, 16, 10)
        self.names = {}
        self.background = QImage()
        self.foreground = QImage()
        self.map_size = None
        self.background_key = None
        self.sprites = {}
        self.location_label = "Scene location"
        self.source_note = "No local map artwork is available for this location."
        self.preview_note = "Scene location · map unavailable"
        self.farmer_gender = "female"
        self.grid_visible = False
        self.header_visible = True
        self.facing_markers = True
        self.zoom_factor = 1.0
        self.setMinimumHeight(280)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Scene preview. Select an actor and drag or use arrow keys to move them.")
        self._update_accessibility()

    def sizeHint(self):
        return QSize(900, 540)

    def load(self, actors, names=None, *, fit=False):
        self.actors = deepcopy(actors)
        if names is not None:
            self.names = deepcopy(names)
        self.selected = min(self.selected, max(0, len(actors) - 1))
        if fit:
            self.fit()
        self.update()

    def fit(self):
        """Frame the cast at a readable scale without changing placements."""
        xs = [actor.get("x", 0) for actor in self.actors] or [8]
        ys = [actor.get("y", 0) for actor in self.actors] or [6]
        columns, rows = max(18, max(xs) - min(xs) + 6), max(14, max(ys) - min(ys) + 6)
        center_x, center_y = (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys)) / 2
        self.bounds = (center_x - columns / 2, center_y - rows / 2, columns, rows)
        self.zoom_factor = 1.0
        self._view_changed()

    def fit_map(self):
        """Frame known map bounds; without a map, return to the cast."""
        if self.map_size is None:
            self.fit()
            return
        self.bounds = (0, 0, self.map_size[0], self.map_size[1])
        self.zoom_factor = 1.0
        self._view_changed()

    def set_scene_preview(self, background=None, map_size=None, sprites=None, location_label="", source_note="", farmer_gender="female", key=None, foreground=None):
        self.background = self._image_copy(background)
        self.foreground = self._image_copy(foreground)
        self.map_size = tuple(map_size) if map_size and len(map_size) == 2 and min(map_size) > 0 else None
        if self.map_size is None:
            self.background = QImage()
            self.foreground = QImage()
        self.sprites = {name: image.copy() for name, image in (sprites or {}).items()
                        if isinstance(image, QImage) and not image.isNull()}
        self.location_label = location_label or "Scene location"
        self.source_note = source_note or ("Local map artwork; verify walkability in-game." if not self.background.isNull()
                                          else "No local map artwork is available for this location.")
        self.farmer_gender = "male" if farmer_gender == "male" else "female"
        self.background_key = key
        self.preview_note = self.location_label + (" · local map preview" if not self.background.isNull() else " · map unavailable")
        self._update_accessibility()
        self.update()

    @staticmethod
    def _image_copy(image):
        return image.copy() if isinstance(image, QImage) and not image.isNull() else QImage()

    def _update_accessibility(self):
        description = self.preview_note + ". " + self.source_note
        self.setAccessibleDescription(description + " Actors without artwork use named position markers.")
        self.setToolTip(description + "\nSelect an actor, then drag, click a tile, or use arrow keys to move them."
                        "\nScroll to zoom. Middle-drag or hold Space and drag to pan."
                        "\nNamed markers indicate unavailable actor artwork.")

    def copy_preview_from(self, other):
        self.set_scene_preview(other.background, other.map_size, other.sprites, other.location_label,
                               other.source_note, other.farmer_gender, other.background_key, other.foreground)
        self.bounds = tuple(other.bounds)
        self.grid_visible = other.grid_visible
        self.facing_markers = other.facing_markers
        self.zoom_factor = other.zoom_factor
        self.update()

    def set_map(self, path=None):
        """Compatibility for callers with a supplied Tiled map path."""
        key = str(path) if path else None
        if key is not None and key == self.background_key:
            return
        background, dimensions = None, None
        note = "No local map artwork is available for this location."
        if path:
            try:
                from pixelheart_core.world import map_bundle, render_map_preview
                bundle = map_bundle(path)
                pixels = render_map_preview(path).convert("RGBA")
                background = QImage(pixels.tobytes(), pixels.width, pixels.height, QImage.Format.Format_RGBA8888).copy()
                dimensions = (bundle["width"], bundle["height"])
                note = "Your supplied map; verify walkability in-game."
            except (ValueError, OSError) as exc:
                note = f"The supplied map cannot be previewed: {exc}"
        self.set_scene_preview(background, dimensions, self.sprites, "Your supplied map" if path else "Scene location",
                               note, self.farmer_gender, key)

    def set_grid_visible(self, visible):
        self.grid_visible = bool(visible)
        self.update()

    def set_header_visible(self, visible):
        self.header_visible = bool(visible)
        self.update()

    def set_facing_markers(self, visible):
        self.facing_markers = bool(visible)
        self.update()

    def _view_changed(self):
        self.update()
        self.viewChanged.emit()

    def set_zoom(self, factor, anchor=None):
        """Zoom around the pointer or viewport center; leave placements intact."""
        factor = float(factor)
        if not math.isfinite(factor):
            return
        factor = min(8.0, max(0.15, factor))
        if factor == self.zoom_factor:
            return
        anchor = anchor if anchor is not None else self.scene_rect().center()
        before = self.world_at(anchor)
        self.zoom_factor = factor
        after = self.world_at(anchor)
        x, y, columns, rows = self.bounds
        self.bounds = (x + before.x() - after.x(), y + before.y() - after.y(), columns, rows)
        self._view_changed()

    def zoom_by(self, factor, anchor=None):
        self.set_zoom(self.zoom_factor * factor, anchor)

    def pan_by(self, delta):
        """Translate the camera by screen pixels, independent of actor edits."""
        scale = self.geometry_grid()[2]
        x, y, columns, rows = self.bounds
        self.bounds = (x - delta.x() / scale, y - delta.y() / scale, columns, rows)
        self._view_changed()

    def scene_rect(self):
        top = 44 if self.header_visible else 0
        return QRectF(0, top, self.width(), max(1, self.height() - top - 30))

    def geometry_grid(self):
        x, y, columns, rows = self.bounds
        viewport = self.scene_rect()
        scale = min(64, viewport.width() / columns, viewport.height() / rows)
        # Fit views use whole source pixels where possible. A broad map may
        # need fractional scale; zoom still retains exact world coordinates.
        if scale >= 16:
            scale = max(16, math.floor(scale / 16) * 16)
        scale = max(0.2, scale * self.zoom_factor)
        left = x + columns / 2 - viewport.width() / scale / 2
        top = y + rows / 2 - viewport.height() / scale / 2
        return left, top, scale, viewport.left(), viewport.top()

    def world_at(self, position):
        left, top, scale, ox, oy = self.geometry_grid()
        return QPointF(left + (position.x() - ox) / scale, top + (position.y() - oy) / scale)

    def tile_point(self, x, y):
        left, top, scale, ox, oy = self.geometry_grid()
        return QPointF(ox + (x - left + .5) * scale, oy + (y - top + .5) * scale)

    def tile_at(self, position):
        if not self.scene_rect().contains(position):
            return None
        point = self.world_at(position)
        x, y = math.floor(point.x()), math.floor(point.y())
        if not 0 <= x <= 1000 or not 0 <= y <= 1000:
            return None
        return x, y

    def actor_rect(self, index):
        actor = self.actors[index]
        point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
        scale = self.geometry_grid()[2]
        # A standard 16×32 sprite stands at the bottom of its actual game tile.
        return QRectF(point.x() - scale / 2, point.y() - scale * 1.5, scale, scale * 2)

    def actor_name(self, index):
        key = self.actors[index].get("name")
        return self.names.get(key, key or "Unnamed actor")

    def marker_rect(self, index):
        frames = [self.sprite_frame(actor) for actor in self.actors]
        return self._marker_rects(self.paint_order(), frames).get(index, QRectF())

    def _marker_rects(self, order, frames):
        # Compute the complete marker layout once so paint and hit testing share
        # the same names, sprite exclusions and exact-tile leader positions.
        occupied = [self.actor_rect(index).adjusted(-2, -2, 2, 2) for index in order
                    if not frames[index].isNull()]
        markers = {}
        for index in order:
            if not frames[index].isNull():
                continue
            actor = self.actors[index]
            point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
            font = self.font()
            font.setPixelSize(11)
            font.setBold(index == self.selected)
            width = min(130, max(64, QFontMetrics(font).horizontalAdvance(self.actor_name(index)) + 22))
            rect = QRectF(point.x() - width / 2, point.y() - 12, width, 24)
            while any(rect.intersects(previous) for previous in occupied):
                rect.translate(0, 28)
            markers[index] = rect
            occupied.append(rect.adjusted(-2, -2, 2, 2))
        return markers

    def paint_order(self):
        return sorted(range(len(self.actors)), key=lambda index: (self.actors[index].get("y", 0), index))

    def actor_at(self, position):
        if not self.scene_rect().contains(position):
            return None
        # Missing-art markers are UI affordances painted above map layers and
        # sprites, so hit testing follows that same order.
        order = self.paint_order()
        frames = [self.sprite_frame(actor) for actor in self.actors]
        markers = self._marker_rects(order, frames)
        for missing in (True, False):
            for index in reversed(order):
                if frames[index].isNull() != missing:
                    continue
                rect = markers[index] if missing else self.actor_rect(index)
                anchor = self.tile_point(self.actors[index].get("x", 0), self.actors[index].get("y", 0))
                if rect.contains(position) or (missing and QRectF(anchor.x() - 5, anchor.y() - 5, 10, 10).contains(position)):
                    return index
        return None

    def sprite_frame(self, actor):
        sheet = self.sprites.get(actor.get("name"))
        if (sheet is None or sheet.isNull() or sheet.width() < 4 or sheet.width() % 4
                or sheet.height() < (sheet.width() // 4) * 8):
            return QImage()
        # Four columns define the frame size, including proportional artwork
        # such as a 96px-wide sheet with 24×48 frames.
        width = sheet.width() // 4
        height = width * 2
        row = (2, 1, 0, 3)[self._facing(actor)]
        return sheet.copy(0, row * height, width, height)

    @staticmethod
    def _facing(actor):
        facing = actor.get("facing", 2)
        if isinstance(facing, str):
            return {"up": 0, "right": 1, "down": 2, "left": 3}.get(facing, 2)
        return facing % 4

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
        if not self.scene_rect().contains(event.position()):
            return
        self._finish_gesture()
        self.setFocus()
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and self._space_pressed):
            self.panning = True
            self._pan_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or not self.actors:
            return
        match = self.actor_at(event.position())
        tile = self.tile_at(event.position())
        if tile is None and match is None:
            return
        if match is not None:
            self.selected = match
            self.actorSelected.emit(self.selected)
            actor = self.actors[self.selected]
            self._drag_offset = event.position() - self.tile_point(actor.get("x", 0), actor.get("y", 0))
        else:
            self._drag_offset = QPointF()
        self.dragging = True
        self.gestureStarted.emit()
        if match is None:
            self.move_selected(*tile)
        self.update()

    def mouseMoveEvent(self, event):
        if self.panning:
            self.pan_by(event.position() - self._pan_position)
            self._pan_position = event.position()
        elif self.dragging and (tile := self.tile_at(event.position() - self._drag_offset)) is not None:
            self.move_selected(*tile)

    def mouseReleaseEvent(self, event):
        self._finish_gesture()
        self._finish_pan()

    def _finish_gesture(self):
        if self.dragging:
            self.dragging = False
            self._drag_offset = QPointF()
            self.gestureFinished.emit()

    def _finish_pan(self, *, clear_space=False):
        self.panning = False
        if clear_space:
            self._space_pressed = False
        if self._space_pressed:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def focusOutEvent(self, event):
        self._finish_gesture()
        self._finish_pan(clear_space=True)
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._finish_gesture()
        self._finish_pan(clear_space=True)
        super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.UngrabMouse and hasattr(self, "dragging"):
            self._finish_gesture()
            self._finish_pan(clear_space=True)
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self._finish_gesture()
                self._space_pressed = True
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        was_dragging = self.dragging or self.panning
        self._finish_gesture()
        if event.key() == Qt.Key.Key_Escape and (was_dragging or self._space_pressed):
            self._finish_pan(clear_space=True)
            event.accept()
            return
        delta = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}.get(event.key())
        if delta and self.actors and not self.panning and not self._space_pressed:
            actor = self.actors[self.selected]
            self.move_selected(actor["x"] + delta[0], actor["y"] + delta[1])
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self._finish_pan(clear_space=True)
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def wheelEvent(self, event):
        if not self.scene_rect().contains(event.position()):
            event.ignore()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self._finish_gesture()
            self.zoom_by(1.2 ** (delta / 120), event.position())
        event.accept()

    def map_rect(self):
        if self.map_size is None:
            return QRectF()
        left, top, scale, ox, oy = self.geometry_grid()
        return QRectF(ox - left * scale, oy - top * scale, self.map_size[0] * scale, self.map_size[1] * scale)

    def _paint_marker(self, painter, index, rect):
        selected = index == self.selected
        actor = self.actors[index]
        point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
        painter.setPen(QPen(QColor("#ead891" if selected else "#839087"), 1))
        if not rect.contains(point):
            painter.drawLine(point, QPointF(rect.center().x(), rect.top()))
            painter.setBrush(QColor("#ead891" if selected else "#839087"))
            painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        painter.setBrush(QColor("#3b483e" if selected else "#333a37"))
        painter.drawRoundedRect(rect, 4, 4)
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QColor("#fff2c5" if selected else "#e0e6e0"))
        text = painter.fontMetrics().elidedText(self.actor_name(index), Qt.TextElideMode.ElideRight, int(rect.width() - 14))
        painter.drawText(rect.adjusted(7, 0, -7, 0), Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), QColor("#faf6eb"))
        left, top, scale, ox, oy = self.geometry_grid()
        viewport = self.scene_rect()
        map_rect = self.map_rect()
        order = self.paint_order()
        frames = [self.sprite_frame(actor) for actor in self.actors]
        markers = self._marker_rects(order, frames)
        painter.save()
        painter.setClipRect(viewport)
        painter.fillRect(viewport, QColor("#262d2a"))
        if not self.background.isNull():
            painter.drawImage(map_rect, self.background)
        if self.grid_visible:
            painter.setPen(QPen(QColor(210, 220, 210, 55), 1))
            stride = max(1, math.ceil(10 / scale))
            for column in range(math.floor(left), math.ceil(left + viewport.width() / scale) + 1, stride):
                x = ox + (column - left) * scale
                painter.drawLine(QPointF(x, viewport.top()), QPointF(x, viewport.bottom()))
            for row in range(math.floor(top), math.ceil(top + viewport.height() / scale) + 1, stride):
                y = oy + (row - top) * scale
                painter.drawLine(QPointF(viewport.left(), y), QPointF(viewport.right(), y))
        # Paint real sprite frames in standing-tile order. Missing artwork has
        # a named marker only; the canvas never invents a character's appearance.
        for index in order:
            actor = self.actors[index]
            frame = frames[index]
            if frame.isNull():
                continue
            target = self.actor_rect(index)
            point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
            if index == self.selected:
                painter.setBrush(QColor(252, 239, 165, 35))
                painter.setPen(QPen(QColor("#fff0af"), 1))
                painter.drawRect(QRectF(point.x() - scale * .5, point.y() - scale * .5, scale, scale))
            painter.drawImage(target, frame)
        if not self.foreground.isNull():
            painter.drawImage(map_rect, self.foreground)
        # Placement affordances remain visible above map occluders.
        for index in order:
            actor = self.actors[index]
            if index in markers:
                self._paint_marker(painter, index, markers[index])
            if index == self.selected and self.facing_markers:
                point = self.tile_point(actor.get("x", 0), actor.get("y", 0))
                dx, dy = ((0, -1), (1, 0), (0, 1), (-1, 0))[self._facing(actor)]
                base = point + QPointF(0, scale * .4)
                tip = base + QPointF(dx * min(20, scale * .65), dy * min(20, scale * .65))
                cross = QPointF(-dy * 3, dx * 3)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#fff0af"))
                painter.drawPolygon(QPolygonF([tip, base + cross, base - cross]))
        notice = ""
        if self.background.isNull():
            notice = "No map picture here yet · you can still place everyone"
        elif not map_rect.intersects(viewport):
            notice = "Outside the map · use Fit map to return"
        if notice:
            font = painter.font()
            font.setBold(False)
            font.setPixelSize(11)
            painter.setFont(font)
            rect = QRectF(12, viewport.top() + 12, max(1, viewport.width() - 24), 26)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(27, 33, 29, 220))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#dbe2d9"))
            painter.drawText(rect.adjusted(9, 0, -9, 0), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(notice, Qt.TextElideMode.ElideRight, max(1, int(rect.width() - 18))))
        painter.restore()
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#45553c"))
        if self.header_visible:
            painter.drawText(QRectF(12, 5, max(1, self.width() - 24), 18), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(self.preview_note, Qt.TextElideMode.ElideRight, max(1, self.width() - 24)))
            font.setBold(False)
            font.setPixelSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#786f5b"))
            painter.drawText(QRectF(12, 23, max(1, self.width() - 24), 17), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(self.source_note, Qt.TextElideMode.ElideRight, max(1, self.width() - 24)))
        if self.actors:
            actor = self.actors[self.selected]
            missing = " · artwork unavailable" if frames[self.selected].isNull() else ""
            caption = f"{self.actor_name(self.selected)} · tile {actor.get('x', 0)}, {actor.get('y', 0)}{missing}"
        else:
            caption = self.empty_caption
        font.setBold(False)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#45553c"))
        help_text = "Scroll to zoom · Space + drag to pan"
        available = max(1, self.width() - 24)
        help_width = painter.fontMetrics().horizontalAdvance(help_text) + 24
        if available > help_width + 280:
            painter.drawText(QRectF(self.width() - help_width - 12, self.height() - 28, help_width, 24),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, help_text)
            available -= help_width
        painter.drawText(QRectF(12, self.height() - 28, available, 24), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, available))
        painter.end()
