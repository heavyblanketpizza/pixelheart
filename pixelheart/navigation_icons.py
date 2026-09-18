"""Small, original pixel pictograms for the editor navigation."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


def navigation_icon(section: int) -> QIcon:
    """Return a crisp 20px icon with matching light and selected-row inks."""
    icon = QIcon()
    for mode, ink, accent in (
        (QIcon.Mode.Normal, "#c9bba4", "#c6a569"),
        (QIcon.Mode.Active, "#f1e6cf", "#d8b477"),
        (QIcon.Mode.Selected, "#66553e", "#aa8447"),
    ):
        pixmap = QPixmap(40, 40)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        def rect(x, y, width, height, color=ink):
            painter.fillRect(x, y, width, height, QColor(color))

        def outline(x, y, width, height, color=ink):
            rect(x, y, width, 1, color)
            rect(x, y + height - 1, width, 1, color)
            rect(x, y, 1, height, color)
            rect(x + width - 1, y, 1, height, color)

        if section == 0:  # A little portrait.
            outline(3, 2, 14, 16)
            rect(8, 5, 4, 4)
            rect(6, 11, 8, 4, accent)
        elif section == 1:  # Conversation.
            outline(2, 3, 16, 11)
            rect(5, 13, 1, 4)
            rect(6, 14, 1, 2)
            for x in (6, 9, 12):
                rect(x, 8, 2, 2, accent)
        elif section == 2:  # Calendar.
            outline(3, 4, 14, 14)
            rect(3, 7, 14, 1)
            rect(6, 2, 2, 4)
            rect(12, 2, 2, 4)
            for x, y in ((6, 10), (11, 10), (6, 14), (11, 14)):
                rect(x, y, 2, 2, accent)
        elif section == 3:  # Wrapped present.
            outline(3, 9, 14, 9)
            outline(2, 6, 16, 4)
            rect(9, 6, 2, 12, accent)
            outline(5, 2, 4, 4, accent)
            outline(11, 2, 4, 4, accent)
        elif section == 4:  # An open notebook.
            outline(2, 4, 8, 13)
            outline(10, 4, 8, 13)
            rect(9, 3, 2, 15, accent)
            rect(4, 7, 3, 1)
            rect(4, 10, 3, 1)
            rect(13, 7, 3, 1)
            rect(13, 10, 3, 1)
        elif section == 5:  # Landscape in a frame.
            outline(2, 3, 16, 14)
            rect(12, 6, 2, 2, accent)
            for x, y, height in ((4, 12, 3), (6, 10, 5), (8, 8, 7), (10, 10, 5), (12, 12, 3), (14, 11, 4)):
                rect(x, y, 2, height)
        elif section == 6:  # Export arrow and tray.
            rect(9, 2, 2, 11, accent)
            rect(7, 3, 2, 2, accent)
            rect(5, 5, 2, 2, accent)
            rect(11, 3, 2, 2, accent)
            rect(13, 5, 2, 2, accent)
            rect(3, 12, 2, 6)
            rect(15, 12, 2, 6)
            rect(3, 16, 14, 2)
        elif section == 7:  # Growing a new idea.
            rect(9, 8, 2, 10)
            rect(4, 5, 4, 2, accent)
            rect(6, 7, 4, 2, accent)
            rect(3, 3, 2, 3, accent)
            rect(11, 5, 4, 2, accent)
            rect(10, 7, 3, 2, accent)
            rect(14, 3, 3, 3, accent)
            rect(6, 17, 8, 1)
        elif section == 8:  # A pixel heart.
            for y, row in enumerate((".xx..xx.", "xxxxxxxx", "xxxxxxxx", ".xxxxxx.", "..xxxx..", "...xx...")):
                for x, cell in enumerate(row):
                    if cell == "x":
                        rect(2 + x * 2, 4 + y * 2, 2, 2, accent if y < 2 else ink)
        else:  # Places and paths.
            outline(2, 3, 6, 14)
            outline(8, 5, 5, 14)
            outline(13, 3, 5, 14)
            rect(5, 8, 2, 2, accent)
            rect(7, 9, 2, 2, accent)
            rect(9, 10, 2, 2, accent)
            rect(11, 9, 2, 2, accent)
            rect(13, 7, 2, 3, accent)

        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon
