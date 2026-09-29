"""Small widgets that make Pixelheart feel like the game's own menus."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import skin
from .theme import heart_icon

HEART_SIZE = QSize(14, 12)


def _pixmap(role):
    piece = skin.current_pieces().get(role)
    return QPixmap(str(piece.path)) if piece is not None else QPixmap()


class HeartMeter(QWidget):
    """A row of friendship hearts, like the game's social page."""

    def __init__(self, total=8, filled=0, parent=None):
        super().__init__(parent)
        self._total, self._filled = max(1, int(total)), 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_value(filled)

    def total(self):
        return self._total

    def filled(self):
        return self._filled

    def set_value(self, filled, total=None):
        if total is not None:
            self._total = max(1, int(total))
        self._filled = max(0, min(int(filled), self._total))
        self.setAccessibleName(f"{self._filled} of {self._total} hearts")
        self.setToolTip(self.accessibleName())
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(self._total * (HEART_SIZE.width() + 2), HEART_SIZE.height() + 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        full, empty = _pixmap("heart_full"), _pixmap("heart_empty")
        fallback = heart_icon(14).pixmap(HEART_SIZE)
        for index in range(self._total):
            image = full if index < self._filled else empty
            if image.isNull():
                image = fallback
                painter.setOpacity(1.0 if index < self._filled else 0.3)
            target = QRect(index * (HEART_SIZE.width() + 2), 1, HEART_SIZE.width(), HEART_SIZE.height())
            painter.drawPixmap(target, image)
            painter.setOpacity(1.0)
        painter.end()


class PixelTitle(QWidget):
    """Headings in the game's bold font when connected, otherwise the bundled pixel font."""
    CELL = (8, 16)
    SPACE = 4

    def __init__(self, text="", scale=2, parent=None):
        super().__init__(parent)
        self._text, self._scale = "", max(1, int(scale))
        self._atlas, self._atlas_path, self._glyphs = QImage(), None, {}
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = str(text)
        self.setAccessibleName(self._text)
        self._load_atlas()
        self.updateGeometry()
        self.update()

    def _load_atlas(self):
        piece = skin.current_pieces().get("font")
        path = piece.path if piece is not None else None
        if path != self._atlas_path:
            self._atlas_path, self._glyphs = path, {}
            self._atlas = QImage(str(path)) if path else QImage()

    def uses_game_font(self):
        self._load_atlas()
        return not self._atlas.isNull() and all(32 <= ord(char) < 127 for char in self._text)

    def _glyph(self, char):
        """Source rectangle trimmed to the glyph's inked columns, plus its advance."""
        if char == " ":
            return QRect(), self.SPACE
        if char not in self._glyphs:
            width, height = self.CELL
            index = ord(char) - 32
            x = index * width % self._atlas.width()
            y = index * width // self._atlas.width() * height
            columns = [column for column in range(width)
                       if any(self._atlas.pixelColor(x + column, y + row).alpha() for row in range(height))]
            left, right = (columns[0], columns[-1]) if columns else (0, 3)
            self._glyphs[char] = (QRect(x + left, y, right - left + 1, height), right - left + 2)
        return self._glyphs[char]

    def _fallback_font(self):
        font = QFont(skin.pixel_family())
        font.setPixelSize(self.CELL[1] * self._scale)
        return font

    def sizeHint(self):
        if self.uses_game_font():
            width = sum(self._glyph(char)[1] for char in self._text) * self._scale
            return QSize(max(1, width), self.CELL[1] * self._scale)
        metrics = QFontMetrics(self._fallback_font())
        return QSize(max(1, metrics.horizontalAdvance(self._text)), metrics.height())

    def minimumSizeHint(self):
        return QSize(1, self.sizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.uses_game_font():
            x = 0
            for char in self._text:
                source, advance = self._glyph(char)
                if not source.isNull():
                    target = QRect(x, 0, source.width() * self._scale, source.height() * self._scale)
                    painter.drawImage(target, self._atlas, source)
                x += advance * self._scale
        else:
            painter.setFont(self._fallback_font())
            painter.setPen(QColor(skin.COLORS["text"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text)
        painter.end()


class PortraitFrame(QFrame):
    """A portrait's first frame inside a game panel, or a friendly placeholder heart."""

    def __init__(self, size=128, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._size = int(size)
        self._portrait = QPixmap()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(self._size, self._size)
        layout.addWidget(self.image, 0, Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("Portrait")
        self.set_portrait()

    def has_portrait(self):
        return not self._portrait.isNull()

    def portrait_pixmap(self):
        return self._portrait

    def set_portrait(self, path=None):
        sheet = QPixmap(str(path)) if path and Path(path).is_file() else QPixmap()
        if not sheet.isNull() and sheet.width() >= 2:
            frame = sheet.width() // 2
            self._portrait = sheet.copy(0, 0, frame, frame).scaled(
                self._size, self._size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation)
            self.image.setPixmap(self._portrait)
        else:
            self._portrait = QPixmap()
            placeholder = self._size // 2
            self.image.setPixmap(heart_icon(placeholder).pixmap(placeholder, placeholder))
