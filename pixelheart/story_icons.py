"""Original line pictograms for the two levels of story navigation."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .skin import COLORS


def story_icon(name: str) -> QIcon:
    """Return an 18px tab icon with native selected, hover and disabled states."""
    if name not in ("storyline", "events", "relationships", "purpose", "trigger", "scene", "aftermath", "rehearse"):
        raise ValueError(f"Unknown story icon: {name}")
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected, QIcon.Mode.Disabled):
        for state in (QIcon.State.Off, QIcon.State.On):
            selected = state == QIcon.State.On or mode == QIcon.Mode.Selected
            ink = COLORS["disabled"] if mode == QIcon.Mode.Disabled else COLORS["text"] if selected or mode == QIcon.Mode.Active else COLORS["muted"]
            accent = COLORS["rule"] if mode == QIcon.Mode.Disabled else COLORS["outline"] if selected else COLORS["rule_strong"]
            for ratio in (1, 2):
                pixmap = QPixmap(18 * ratio, 18 * ratio)
                pixmap.setDevicePixelRatio(ratio)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.scale(.9, .9)
                _draw(painter, name, ink, accent)
                painter.end()
                icon.addPixmap(pixmap, mode, state)
    return icon


def _draw(painter, name, ink, accent):
    pen = QPen(QColor(ink), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def path(points, *, color=ink, close=False):
        pen.setColor(QColor(color))
        painter.setPen(pen)
        shape = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            shape.lineTo(QPointF(*point))
        if close:
            shape.closeSubpath()
        painter.drawPath(shape)

    def circle(x, y, radius, *, color=ink):
        pen.setColor(QColor(color))
        painter.setPen(pen)
        painter.drawEllipse(QPointF(x, y), radius, radius)

    def box(x, y, width, height):
        pen.setColor(QColor(ink))
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(x, y, width, height), 1.2, 1.2)

    if name == "storyline":
        path([(6, 5), (10, 5), (10, 8)], color=accent)
        path([(10, 12), (10, 15), (14, 15)], color=accent)
        for x, y in ((2, 3), (8, 8), (14, 13)):
            box(x, y, 4, 4)
    elif name == "events":
        path([(6, 3), (17, 3), (17, 14)], color=accent)
        box(3, 6, 11, 11)
        path([(6, 10), (11, 10)])
        path([(6, 13), (9, 13)])
    elif name == "relationships":
        circle(6, 6, 2.5)
        circle(14, 6, 2.5)
        path([(2, 15), (2, 13), (4, 11), (8, 11), (10, 13), (12, 11), (16, 11), (18, 13), (18, 15)])
        path([(7, 16), (10, 18), (13, 16)], color=accent)
    elif name == "purpose":
        circle(10, 10, 7)
        circle(10, 10, 3.5)
        path([(10, 10), (16.5, 3.5)], color=accent)
        path([(13.5, 3.5), (16.5, 3.5), (16.5, 6.5)], color=accent)
    elif name == "trigger":
        path([(11, 3), (17, 3), (17, 17), (11, 17)])
        path([(2, 10), (13, 10)], color=accent)
        path([(9, 6), (13, 10), (9, 14)], color=accent)
    elif name == "scene":
        box(2, 3, 16, 14)
        path([(2, 7), (18, 7)], color=accent)
        circle(10, 10.5, 1.5)
        path([(7, 15), (8, 13), (12, 13), (13, 15)])
    elif name == "aftermath":
        circle(4, 10, 2)
        path([(6, 10), (10, 10), (10, 5), (14, 5)], color=accent)
        path([(10, 10), (10, 15), (14, 15)], color=accent)
        circle(16, 5, 2)
        circle(16, 15, 2)
    else:  # Rehearse: a play control that does not imply export readiness.
        circle(10, 10, 7)
        path([(8, 6.5), (13, 10), (8, 13.5)], color=accent, close=True)
