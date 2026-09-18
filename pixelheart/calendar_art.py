"""Original, code-drawn calendar decorations; no game textures or fonts.

The small glyphs and motifs here are purpose-built for Pixelheart's calendar.
Keep coordinates integral so the artwork stays crisp at desktop display scales.
"""

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor


_GLYPHS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "A": ("01110", "11011", "10001", "11111", "10001", "10001", "10001"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "I": ("111", "010", "010", "010", "010", "010", "111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
}


def lettering_width(text, scale):
    return sum((len(_GLYPHS[letter][0]) + 1) * scale for letter in text.upper()) - scale


def draw_lettering(painter, text, x, y, scale=2, color="#654023"):
    for letter in text.upper():
        glyph = _GLYPHS[letter]
        for row, pixels in enumerate(glyph):
            for column, pixel in enumerate(pixels):
                if pixel == "1":
                    painter.fillRect(x + column * scale, y + row * scale, scale, scale, QColor(color))
        x += (len(glyph[0]) + 1) * scale


def draw_motif(painter, kind, x, y, scale=2):
    """Paint a 12 × 12 original motif with a transparent background."""
    def block(left, top, width, height, color):
        painter.fillRect(x + left * scale, y + top * scale, width * scale, height * scale, QColor(color))

    if kind == "gift":
        block(2, 4, 8, 7, "#814e36")
        block(1, 3, 10, 3, "#814e36")
        block(2, 4, 8, 1, "#d77957")
        block(3, 6, 6, 4, "#e49864")
        block(5, 3, 2, 8, "#ffe1a0")
        block(3, 1, 2, 2, "#814e36")
        block(7, 1, 2, 2, "#814e36")
        block(4, 2, 4, 1, "#f4c16e")
    elif kind == "heart":
        rows = ("01100110", "11111111", "11111111", "11111111", "01111110", "00111100", "00011000")
        for row, pixels in enumerate(rows):
            for col, pixel in enumerate(pixels):
                if pixel == "1":
                    block(col + 2, row + 2, 1, 1, "#a6443e" if row > 3 else "#d26b56")
        block(3, 3, 1, 2, "#f5ad7d")
        block(4, 3, 1, 1, "#f5ad7d")
    elif kind == "flag":
        block(2, 1, 1, 10, "#755039")
        block(3, 2, 7, 5, "#9b4339")
        block(3, 2, 6, 2, "#d27651")
        block(3, 4, 5, 1, "#edb76b")
        block(9, 6, 1, 1, "#fbe4ae")
        block(1, 10, 3, 1, "#755039")
    elif kind == "spring":
        block(5, 6, 1, 6, "#63804a")
        block(6, 8, 3, 2, "#78994f")
        block(3, 9, 2, 1, "#78994f")
        for left, top in ((4, 0), (1, 3), (7, 3), (4, 6)):
            block(left, top, 3, 3, "#b86157")
            block(left, top, 2, 2, "#e3987d")
        block(4, 3, 3, 3, "#efbd53")
        block(5, 4, 1, 1, "#fff1b5")
    elif kind == "summer":
        for left, top, width, height in ((5, 0, 2, 2), (5, 10, 2, 2), (0, 5, 2, 2), (10, 5, 2, 2), (1, 1, 2, 2), (9, 1, 2, 2), (1, 9, 2, 2), (9, 9, 2, 2)):
            block(left, top, width, height, "#c08031")
        block(3, 2, 6, 8, "#d59738")
        block(2, 3, 8, 6, "#d59738")
        block(3, 3, 6, 6, "#f1c757")
        block(4, 3, 3, 2, "#ffe3a1")
    elif kind == "fall":
        block(3, 2, 7, 7, "#a85931")
        block(5, 0, 4, 2, "#a85931")
        block(1, 4, 2, 4, "#a85931")
        block(3, 3, 6, 4, "#d58a42")
        block(5, 1, 3, 4, "#e8ab59")
        for i in range(7):
            block(2 + i, 10 - i, 1, 1, "#785032")
    elif kind == "winter":
        block(5, 0, 2, 12, "#6a98a0")
        block(0, 5, 12, 2, "#6a98a0")
        for i in (2, 3, 8, 9):
            block(i, i, 1, 1, "#6a98a0")
            block(11 - i, i, 1, 1, "#6a98a0")
        block(4, 4, 4, 4, "#b6d8ce")
        block(5, 5, 2, 2, "#f6fcdf")


def draw_wood_frame(painter, rect):
    """A narrow oak frame; the paper and its contents do most of the work."""
    painter.fillRect(rect.adjusted(3, 0, -3, 0), QColor("#806044"))
    painter.fillRect(rect.adjusted(0, 3, 0, -3), QColor("#806044"))
    painter.fillRect(rect.adjusted(2, 2, -2, -3), QColor("#b18c5d"))
    painter.fillRect(rect.adjusted(5, 5, -5, -6), QColor("#ddc193"))
    painter.fillRect(rect.adjusted(8, 8, -8, -9), QColor("#b89a6d"))
    painter.fillRect(rect.adjusted(9, 9, -9, -10), QColor("#f3e5c7"))
    painter.fillRect(rect.left() + 5, rect.top() + 3, rect.width() - 10, 2, QColor("#ebd4aa"))
    for left in range(rect.left() + 30, rect.right() - 35, 89):
        painter.fillRect(left, rect.top() + 6, 20, 1, QColor("#c7a777"))
        painter.fillRect(left + 13, rect.bottom() - 5, 16, 1, QColor("#9e7c52"))
    for left in (rect.left() + 3, rect.right() - 5):
        for top in (rect.top() + 3, rect.bottom() - 6):
            painter.fillRect(QRect(left, top, 3, 3), QColor("#806044"))
            painter.fillRect(QRect(left, top, 1, 1), QColor("#f2dfbc"))
