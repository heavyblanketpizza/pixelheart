"""The zoomable painting surface: transparency checks, grids, onion skin and input."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from pixelheart_core.pixel_document import PixelError
from .skin import COLORS


ZOOM_LEVELS = (1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 32, 48)
CHECKER = {"light": ("#fdfbf7", "#ece8e1"), "dark": ("#3b3732", "#46413b")}
ONION_TINTS = ("#d9734e", "#4e8fd9")
ONION_OPACITY = 0.35


def qimage_from_image(image):
    """A detached Qt copy of an RGBA Pillow image."""
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def image_from_qimage(qimage):
    """An RGBA Pillow image from any Qt image, honoring row padding."""
    from PIL import Image
    converted = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height, stride = converted.width(), converted.height(), converted.bytesPerLine()
    data = bytes(converted.constBits())[:stride * height]
    return Image.frombuffer("RGBA", (width, height), data, "raw", "RGBA", stride, 1).copy()


def _checker_tile(scheme):
    tile = QPixmap(16, 16)
    light, dark = (QColor(color) for color in CHECKER[scheme])
    tile.fill(light)
    painter = QPainter(tile)
    painter.fillRect(8, 0, 8, 8, dark)
    painter.fillRect(0, 8, 8, 8, dark)
    painter.end()
    return tile


def _silhouette(image, color):
    tinted = QImage(image)
    painter = QPainter(tinted)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return tinted


class PixelCanvas(QWidget):
    """Shows the document at a whole-number zoom and turns pointer input into tool calls."""

    edited = Signal()
    hovered = Signal(int, int)
    left = Signal()
    message = Signal(str)
    colors_changed = Signal()
    frame_changed = Signal(int)
    zoom_requested = Signal(int, QPointF)
    pan_requested = Signal(QPointF)

    def __init__(self, document, tools, parent=None):
        super().__init__(parent)
        self.document, self.tools = document, tools
        self.zoom = 8
        self.pixel_grid = True
        self.frame_grid = True
        self.onion = False
        self.scheme = "light"
        self.active_frame = 0
        self.hover = None
        self._pan_from = None
        self._space = False
        self._cache = QImage()
        self._tile = _checker_tile(self.scheme)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName("Pixel canvas")
        self.setAccessibleDescription("Paint with the left button, the second color with the right button. Alt-click picks a color.")
        self.refresh()

    # Display -----------------------------------------------------------------

    def refresh(self):
        """Bring the cached image up to date with the document's changed area."""
        document = self.document
        size_changed = self._cache.width() != document.width or self._cache.height() != document.height
        if size_changed:
            self._cache = QImage(document.width, document.height, QImage.Format.Format_ARGB32_Premultiplied)
            self._cache.fill(Qt.GlobalColor.transparent)
            document.take_dirty()
            box = (0, 0, document.width, document.height)
            self.setFixedSize(document.width * self.zoom, document.height * self.zoom)
            if self.active_frame >= document.frame_count:
                self.set_active_frame(max(0, document.frame_count - 1))
        else:
            box = document.take_dirty()
        if box is not None:
            painter = QPainter(self._cache)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(box[0], box[1], qimage_from_image(document.composite(box)))
            painter.end()
        self.update()

    def set_zoom(self, zoom):
        self.zoom = max(ZOOM_LEVELS[0], min(int(zoom), ZOOM_LEVELS[-1]))
        self.setFixedSize(self.document.width * self.zoom, self.document.height * self.zoom)
        self.update()

    def set_scheme(self, scheme):
        self.scheme = scheme
        self._tile = _checker_tile(scheme)
        self.update()

    def set_active_frame(self, index):
        index = max(0, min(int(index), max(0, self.document.frame_count - 1)))
        if index != self.active_frame:
            self.active_frame = index
            self.update()
            self.frame_changed.emit(index)

    def _screen(self, box):
        zoom = self.zoom
        return QRect(box[0] * zoom, box[1] * zoom, (box[2] - box[0]) * zoom, (box[3] - box[1]) * zoom)

    def onion_frames(self):
        """The previous and next frame in the active frame's row, if the row has several."""
        document = self.document
        columns = document.frame_columns
        if columns < 2 or self.active_frame >= document.frame_count:
            return None, None
        row_start = self.active_frame // columns * columns
        column = self.active_frame - row_start
        return row_start + (column - 1) % columns, row_start + (column + 1) % columns

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        exposed = event.rect()
        painter.drawTiledPixmap(exposed, self._tile, exposed.topLeft())
        zoom = self.zoom
        document = self.document
        if self.onion and document.frame_count > 1:
            target = self._screen(document.frame_box(self.active_frame))
            for index, tint in zip(self.onion_frames(), ONION_TINTS):
                if index is None or index == self.active_frame:
                    continue
                frame = qimage_from_image(document.composite(document.frame_box(index), include_reference=False))
                painter.setOpacity(ONION_OPACITY)
                painter.drawImage(target, _silhouette(frame, tint))
            painter.setOpacity(1)
        left, top = max(0, exposed.left() // zoom), max(0, exposed.top() // zoom)
        right = min(document.width, exposed.right() // zoom + 1)
        bottom = min(document.height, exposed.bottom() // zoom + 1)
        if right > left and bottom > top:
            source = QRect(left, top, right - left, bottom - top)
            painter.drawImage(self._screen((left, top, right, bottom)), self._cache, source)
            if self.pixel_grid and zoom >= 6:
                painter.setPen(QPen(QColor(38, 34, 29, 26), 1))
                for x in range(left, right + 1):
                    painter.drawLine(x * zoom, top * zoom, x * zoom, bottom * zoom)
                for y in range(top, bottom + 1):
                    painter.drawLine(left * zoom, y * zoom, right * zoom, y * zoom)
        self._paint_guides(painter)
        painter.end()

    def _paint_guides(self, painter):
        document = self.document
        zoom = self.zoom
        frame_width, frame_height = document.frame_size
        if self.frame_grid and document.frame_count > 1:
            painter.setPen(QPen(QColor(38, 34, 29, 110), 1))
            for x in range(frame_width, document.width, frame_width):
                painter.drawLine(x * zoom, 0, x * zoom, document.height * zoom)
            for y in range(frame_height, document.height, frame_height):
                painter.drawLine(0, y * zoom, document.width * zoom, y * zoom)
        if document.frame_count > 1:
            painter.setPen(QPen(QColor(COLORS["outline"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._screen(document.frame_box(self.active_frame)).adjusted(1, 1, -1, -1))
        if document.selection is not None:
            rect = self._screen(document.selection).adjusted(0, 0, -1, -1)
            painter.setPen(QPen(QColor("#fdfbf7"), 1))
            painter.drawRect(rect)
            dashed = QPen(QColor(COLORS["text"]), 1, Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.drawRect(rect)
        if self.hover is not None and not self._panning():
            x, y = self.hover
            size = self.tools.brush if self.tools.tool in ("pencil", "eraser", "line") else 1
            before = (size - 1) // 2
            outline = self._screen((x - before, y - before, x - before + size, y - before + size))
            painter.setPen(QPen(QColor(COLORS["text"]), 1))
            painter.drawRect(outline.adjusted(0, 0, -1, -1))
            painter.setPen(QPen(QColor("#fdfbf7"), 1))
            painter.drawRect(outline.adjusted(1, 1, -2, -2))

    # Input -------------------------------------------------------------------

    def pixel(self, position):
        return int(position.x() // self.zoom), int(position.y() // self.zoom)

    def _panning(self):
        return self._pan_from is not None or self._space

    def _run(self, action, *arguments, **keywords):
        try:
            action(*arguments, **keywords)
        except PixelError as exc:
            self.tools.cancel()
            self.message.emit(str(exc))
        self.refresh()

    def mousePressEvent(self, event):
        button = event.button()
        if button == Qt.MouseButton.MiddleButton or (self._space and button == Qt.MouseButton.LeftButton):
            self._pan_from = event.globalPosition()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton) or self.tools.active:
            return
        x, y = self.pixel(event.position())
        frame = self.document.frame_at(x, y)
        if frame is not None:
            self.set_active_frame(frame)
        modifiers = event.modifiers()
        before = self.tools.primary, self.tools.secondary
        self._run(self.tools.press, x, y, secondary=button == Qt.MouseButton.RightButton,
                  shift=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
                  alt=bool(modifiers & Qt.KeyboardModifier.AltModifier))
        if (self.tools.primary, self.tools.secondary) != before:
            self.colors_changed.emit()

    def mouseMoveEvent(self, event):
        if self._pan_from is not None:
            position = event.globalPosition()
            self.pan_requested.emit(position - self._pan_from)
            self._pan_from = position
            return
        x, y = self.pixel(event.position())
        if (x, y) != self.hover:
            self.hover = (x, y)
            self.hovered.emit(x, y)
        if self.tools.active:
            before = self.tools.primary, self.tools.secondary
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._run(self.tools.move, x, y, shift=shift)
            if (self.tools.primary, self.tools.secondary) != before:
                self.colors_changed.emit()
        else:
            self.update()

    def mouseReleaseEvent(self, event):
        if self._pan_from is not None:
            self._pan_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.CrossCursor)
            return
        if not self.tools.active:
            return
        x, y = self.pixel(event.position())
        self._run(self.tools.release, x, y)
        self.edited.emit()

    def leaveEvent(self, event):
        self.hover = None
        self.left.emit()
        self.update()

    def wheelEvent(self, event):
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            steps = 1 if event.angleDelta().y() > 0 else -1 if event.angleDelta().y() < 0 else 0
            if steps:
                self.zoom_requested.emit(steps, event.position())
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space = False
        super().focusOutEvent(event)
