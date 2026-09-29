"""Game-clock choices, including Stardew's hours after midnight."""

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QSpinBox
from PySide6.QtGui import QValidator


def game_minutes(value):
    raw = str(value).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hours, minutes = map(int, raw.split(":"))
    elif re.fullmatch(r"\d{3,4}", raw):
        hours, minutes = divmod(int(raw), 100)
    else:
        return None
    total = hours * 60 + minutes
    return total if 0 <= minutes < 60 and minutes % 10 == 0 and 360 <= total <= 1560 else None


def game_time(minutes):
    hours, remainder = divmod(minutes, 60)
    return str(hours * 100 + remainder)


class ScheduleTime(QComboBox):
    """Only valid ten-minute choices; retain invalid legacy text for review."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(False)
        self.setMaxVisibleItems(14)
        for minutes in range(360, 1561, 10):
            hours, remainder = divmod(minutes, 60)
            self.addItem(f"{hours:02}:{remainder:02}" + (" (+1 day)" if hours >= 24 else ""), game_time(minutes))
        self.setToolTip("Stardew game time, in ten-minute steps. 24:00–26:00 is after midnight.")

    def setText(self, text):
        # Compatibility with saved HH:MM values and older editor integrations.
        while self.count() > 121:
            self.removeItem(self.count() - 1)
        minutes = game_minutes(text)
        if minutes is None:
            self.addItem(f"{text} · review", str(text))
            self.setItemData(self.count() - 1, "Choose a valid game time to replace this saved value.", Qt.ItemDataRole.ToolTipRole)
            self.setCurrentIndex(self.count() - 1)
        else:
            self.setCurrentIndex(self.findData(game_time(minutes)))

    def text(self):
        return self.currentData()


class EventTime(QSpinBox):
    """Display a clock while retaining the event format's integer game time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(600, 2600)
        self.setSingleStep(10)
        self.setToolTip("Game time in ten-minute steps. 24:00–26:00 is after midnight.")

    def textFromValue(self, value):
        return f"{value // 100:02d}:{value % 100:02d}"

    def valueFromText(self, text):
        minutes = game_minutes(text)
        return int(game_time(minutes)) if minutes is not None else self.value()

    def validate(self, text, position):
        if game_minutes(text) is not None:
            state = QValidator.State.Acceptable
        elif re.fullmatch(r"\d{0,2}:?\d{0,2}", text):
            state = QValidator.State.Intermediate
        else:
            state = QValidator.State.Invalid
        return state, text, position

    def stepBy(self, steps):
        minutes = self.value() // 100 * 60 + self.value() % 100
        minutes = max(360, min(1560, minutes + steps * 10))
        self.setValue(int(game_time(minutes)))
