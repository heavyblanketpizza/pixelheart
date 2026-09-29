"""Pixelheart's look: warm white and one ink, with a pixel font and heart."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication


def heart_icon(size=64):
    """Draw our own little pixel heart, with no external game artwork."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.scale(size / 12, size / 12)
    pattern = (
        "..oo..oo..",
        ".ohhoohho.",
        "ohrrrrrrro",
        "orrrrrrrro",
        ".orrrrrro.",
        "..orrrro..",
        "...orro...",
        "....oo....",
    )
    for y, row in enumerate(pattern):
        for x, cell in enumerate(row):
            if cell != ".":
                color = {"o": "#763c30", "h": "#f8b28d", "r": "#d76050" if y < 4 else "#bb493e"}[cell]
                painter.fillRect(x + 1, y + 2, 1, 1, QColor(color))
    painter.end()
    return QIcon(pixmap)


_UNSET = object()


def apply_theme(app: QApplication, *, content_root=_UNSET):
    """Apply the paper look, borrowing hearts and lettering from the connected game."""
    from .skin import COLORS, build_stylesheet, easy_read_enabled, pixel_family, refresh_pieces
    if content_root is _UNSET:
        from .game_connection import game_connection
        content_root = game_connection().content_root()
    pieces = refresh_pieces(content_root)
    easy = easy_read_enabled()
    stylesheet = build_stylesheet(pieces, easy_read=easy)
    if app.style().objectName().casefold() != "fusion":
        app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: COLORS["page"],
        QPalette.ColorRole.WindowText: COLORS["text"],
        QPalette.ColorRole.Base: COLORS["paper"],
        QPalette.ColorRole.AlternateBase: COLORS["highlight"],
        QPalette.ColorRole.Text: COLORS["text"],
        QPalette.ColorRole.Button: COLORS["page"],
        QPalette.ColorRole.ButtonText: COLORS["text"],
        QPalette.ColorRole.Highlight: COLORS["selection"],
        QPalette.ColorRole.HighlightedText: COLORS["text"],
        QPalette.ColorRole.PlaceholderText: COLORS["disabled"],
        QPalette.ColorRole.ToolTipBase: COLORS["paper"],
        QPalette.ColorRole.ToolTipText: COLORS["text"],
        QPalette.ColorRole.Link: COLORS["link"],
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(COLORS["disabled"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(COLORS["disabled"]))
    app.setPalette(palette)
    font = QFont("Helvetica Neue" if easy else pixel_family())
    font.setPixelSize(14 if easy else 16)
    # Pixelify's fi/fl ligatures look like an "A"; stylesheet fonts inherit this.
    for tag in ("liga", "clig", "dlig"):
        font.setFeature(QFont.Tag(tag), 0)
    app.setFont(font)
    app.setWindowIcon(heart_icon())
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)
