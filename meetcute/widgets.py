"""Small shared widgets; artwork previews never synthesize character content."""

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget, QPushButton


def label(text, style=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if style:
        widget.setObjectName(style)
    return widget


def button(text, callback, style=None):
    widget = QPushButton(text)
    if style:
        widget.setObjectName(style)
    widget.clicked.connect(callback)
    return widget


def card(title=None, subtitle=None):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 22, 24, 24)
    layout.setSpacing(16)
    if title:
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(label(title, "sectionTitle"))
        if subtitle:
            heading.addWidget(label(subtitle, "muted", True))
        layout.addLayout(heading)
    elif subtitle:
        layout.addWidget(label(subtitle, "muted", True))
    return frame, layout


def paint_artwork_placeholder(painter, rect, text, *, portrait=True):
    """A quiet empty frame, distinct from the transparency grid of a loaded PNG."""
    from .skin import COLORS  # skin imports game_import, which imports this module.
    painter.fillRect(rect, QColor(COLORS["highlight"]))
    painter.setPen(QPen(QColor(COLORS["rule"]), 1))
    painter.drawRect(rect.adjusted(0, 0, -1, -1))
    width, height = (38, 42) if portrait else (26, 44)
    frame = QRect(0, 0, width, height)
    frame.moveCenter(rect.center())
    frame.translate(0, -15)
    painter.fillRect(frame, QColor(COLORS["paper"]))
    painter.setPen(QPen(QColor(COLORS["rule_strong"]), 1))
    painter.drawRect(frame)
    painter.fillRect(frame.left() + 4, frame.top() + 4, 1, 6, QColor(COLORS["rule"]))
    painter.fillRect(frame.left() + 4, frame.top() + 4, 6, 1, QColor(COLORS["rule"]))
    center = frame.center()
    painter.fillRect(center.x() - 5, center.y(), 11, 1, QColor(COLORS["rule_strong"]))
    painter.fillRect(center.x(), center.y() - 5, 1, 11, QColor(COLORS["rule_strong"]))
    font = QFont(painter.font())
    font.setPixelSize(12)
    painter.setFont(font)
    painter.setPen(QColor(COLORS["muted"]))
    caption = QRect(rect.left() + 12, frame.bottom() + 12, max(1, rect.width() - 24), 38)
    painter.drawText(caption, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, text)


class ArtworkPreview(QWidget):
    def __init__(self, text="Your character belongs here", parent=None):
        super().__init__(parent)
        self.pixmap = QPixmap()
        self.empty_text = text
        self.setMinimumSize(180, 180)
        self.setAccessibleName("Artwork preview")

    def set_image(self, path=None, portrait=False):
        self.pixmap = QPixmap(str(path)) if path else QPixmap()
        if portrait and not self.pixmap.isNull():
            # Preview a single supplied frame, preserving its original pixels.
            frame = self.pixmap.width() // 2
            self.pixmap = self.pixmap.copy(0, 0, frame, frame)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        if not self.pixmap.isNull():
            for y in range(0, self.height(), 12):
                for x in range(0, self.width(), 12):
                    painter.fillRect(x, y, 12, 12, QColor("#f3eee3" if (x // 12 + y // 12) % 2 else "#eae4d5"))
            # Keep a little breathing room around supplied sheets.
            size = self.pixmap.size().scaled(max(1, self.width() - 32), max(1, self.height() - 32), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRect((self.width() - size.width()) // 2, (self.height() - size.height()) // 2, size.width(), size.height())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(rect, self.pixmap)
        else:
            paint_artwork_placeholder(painter, self.rect(), self.empty_text)
        painter.end()
