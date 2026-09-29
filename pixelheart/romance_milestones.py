"""A compact, keyboard-accessible picker for the six heart milestones."""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QStyle,
    QStyleOptionButton, QStylePainter, QWidget,
)

from .theme import heart_icon


_MILESTONES = ((2, "Friends", "Friendship"), (4, "Friends", "Friendship"),
               (6, "Friends", "Friendship"), (8, "Close", "Close friendship"),
               (10, "Dating", "Dating"), (14, "Married", "Married"))


class _MilestoneButton(QPushButton):
    def __init__(self, hearts, phase, accessible_phase, icon, parent):
        super().__init__(parent)
        self.hearts, self.phase, self.accessible_phase = hearts, phase, accessible_phase
        self.heart = icon
        self.setObjectName("romanceMilestone")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(80)
        self.setMaximumWidth(110)
        self.setFixedHeight(86)
        self.set_count(0)

    def sizeHint(self):
        return QSize(110, 86)

    def set_count(self, count):
        self.status = "Not started" if count == 0 else f"{count} {'scene' if count == 1 else 'scenes'}"
        self.setAccessibleName(f"{self.hearts} hearts · {self.accessible_phase} · {self.status}")
        self.setToolTip(self.accessibleName())
        self.update()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        enabled, selected = self.isEnabled(), self.isChecked()
        ink = "#a19888" if not enabled else "#536543" if selected else "#554833"
        muted = "#a19888" if not enabled else "#687751" if selected else "#796b56"
        font = self.font()
        font.setPixelSize(19)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(ink))
        number = str(self.hearts)
        width = QFontMetrics(font).horizontalAdvance(number)
        x = (self.width() - width - 23) // 2
        painter.drawText(QRect(x, 10, width, 26), Qt.AlignmentFlag.AlignCenter, number)
        self.heart.paint(painter, QRect(x + width + 5, 14, 18, 18),
                         Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled)
        font.setPixelSize(12)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(QRect(4, 38, self.width() - 8, 18), Qt.AlignmentFlag.AlignCenter, self.phase)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(muted))
        painter.drawText(QRect(4, 58, self.width() - 8, 17), Qt.AlignmentFlag.AlignCenter, self.status)


class HeartMilestones(QWidget):
    """Choose a heart milestone; programmatic updates never emit selected."""

    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("romanceMilestones")
        self.setAccessibleName("Romance heart milestones")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("""
            QPushButton#romanceMilestone { background: #fffbf2; border: 1px solid #d8ccb5; border-radius: 5px; padding: 0; }
            QPushButton#romanceMilestone:hover { background: #f4ecdc; border-color: #b6a27e; }
            QPushButton#romanceMilestone:checked { background: #e9eddf; border: 2px solid #87986e; }
            QPushButton#romanceMilestone:checked:hover { background: #e2e9d5; }
            QPushButton#romanceMilestone:pressed { background: #e1e6d2; }
            QPushButton#romanceMilestone:focus { border: 2px solid #b28e4e; }
            QPushButton#romanceMilestone:disabled { background: #f0ebdf; border-color: #ddd3bf; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        icon = heart_icon(36)
        for hearts, phase, accessible_phase in _MILESTONES:
            button = _MilestoneButton(hearts, phase, accessible_phase, icon, self)
            self.buttons[hearts] = button
            self.group.addButton(button, hearts)
            layout.addWidget(button, 1)
        layout.addStretch()
        self.group.idClicked.connect(self.selected.emit)

    def set_current(self, hearts):
        self.group.setExclusive(False)
        for value, button in self.buttons.items():
            button.setChecked(value == hearts)
        self.group.setExclusive(True)

    def set_counts(self, counts):
        for hearts, button in self.buttons.items():
            count = counts.get(hearts, 0)
            button.set_count(count if isinstance(count, int) and count >= 0 else 0)
