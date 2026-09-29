"""Dialogue in plain words: when a line is said, how they feel, and how it looks in the game's box."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QMenu, QToolButton, QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from pixelheart_core.dialogue_keys import DAY_NAMES, DAYS, HEARTS, OCCASIONS, SEASONS, build_trigger, parse_trigger
from pixelheart_core.dialogue_pages import BOX_LINES, EMOTIONS, dialogue_pages, set_emotion
from .location_picker import MapSelector
from .skin import COLORS, pixel_family
from .widgets import button, label


def portrait_frames(path):
    """The expressions of a portrait sheet (two columns of square frames), in game order."""
    sheet = QPixmap(str(path)) if path and Path(path).is_file() else QPixmap()
    if sheet.isNull() or sheet.width() < 2:
        return []
    size = sheet.width() // 2
    return [sheet.copy(column * size, row * size, size, size)
            for row in range(sheet.height() // size) for column in range(2)]


def _combo(choices, name):
    widget = QComboBox()
    for caption, data in choices:
        widget.addItem(caption, data)
    widget.setAccessibleName(name)
    # Short menus share one row; let them shrink instead of widening the page.
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.setMinimumContentsLength(6)
    return widget


class TriggerPicker(QWidget):
    """Choose when a line is said in plain words; the game key is shown underneath.

    ``items`` returns ``[(qualified item ID, name)]`` for the gift list.
    """

    changed = Signal()

    def __init__(self, parent=None, *, items=None):
        super().__init__(parent)
        self._items = items or (lambda: [])
        self._loading = False
        self._key = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.occasion = _combo([(caption, key) for key, caption in OCCASIONS.items()], "When they say it")
        root.addWidget(self.occasion)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root.addWidget(self.stack)
        self.pages = {}

        self._page("introduction", [])
        self.day = _combo([(DAY_NAMES[day] + "s", day) for day in DAYS], "Day of the week")
        self.season = _combo([("Any season", "")] + [(season.title(), season) for season in SEASONS], "Season")
        self.hearts = _combo([("Any friendship", 0)] + [("10 hearts" if h == 10 else f"{h}+ hearts", h) for h in HEARTS[1:]],
                             "Hearts")
        self._page("weekday", [self.day, self.season, self.hearts])
        self.date_season = _combo([(season.title(), season) for season in SEASONS], "Season")
        self.date_day = QSpinBox()
        self.date_day.setRange(1, 28)
        self.date_day.setAccessibleName("Day of the month")
        self._page("date", [self.date_season, self.date_day])
        self.place = MapSelector("Saloon", compact=True)
        self.place_day = _combo([("Any day", "")] + [(DAY_NAMES[day] + "s", day) for day in DAYS], "Day at this place")
        self._page("place", [self.place, self.place_day])
        self.gift = QComboBox()
        self.gift.setEditable(True)
        self.gift.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.gift.setAccessibleName("Gift")
        self.gift.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.gift.setMinimumContentsLength(12)
        self.gift.lineEdit().setPlaceholderText("Search by item name, or type an item ID like (O)109")
        self._page("gift", [self.gift])
        self.birthday = _combo([("They like it", True), ("They don't like it", False)], "Birthday gift")
        self._page("birthday_gift", [self.birthday])
        self.other = QLineEdit()
        self.other.setMaxLength(120)
        self.other.setPlaceholderText("A game dialogue key, e.g. Resort_Bar")
        self.other.setAccessibleName("Game dialogue key")
        self._page("other", [self.other])

        self.key_label = label("", "hint")
        self.key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.key_label)
        self.occasion.currentIndexChanged.connect(self._occasion_changed)
        for widget in (self.day, self.season, self.hearts, self.date_season, self.place_day, self.birthday):
            widget.currentIndexChanged.connect(self._edited)
        self.date_day.valueChanged.connect(self._edited)
        self.place.changed.connect(self._edited)
        self.gift.currentIndexChanged.connect(self._edited)
        self.gift.lineEdit().editingFinished.connect(self._edited)
        self.other.textChanged.connect(self._edited)
        self._fill_items()
        self.set_value("Introduction")

    def _page(self, key, widgets):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget, 2 if widget in (getattr(self, "place", None), getattr(self, "gift", None),
                                                    getattr(self, "other", None)) else 1)
        if not widgets:
            layout.addWidget(label("Said once, when you first meet.", "hint"))
        self.pages[key] = self.stack.addWidget(page)

    def _fill_items(self):
        current = self.gift.currentData()
        self.gift.blockSignals(True)
        self.gift.clear()
        for identity, name in self._items():
            self.gift.addItem(f"{name} · {identity}", identity)
        self.gift.blockSignals(False)
        if current is not None:
            self.gift.setCurrentIndex(self.gift.findData(current))

    def _gift_value(self):
        text = self.gift.currentText().strip()
        data = self.gift.currentData()
        if data is not None and self.gift.itemText(self.gift.currentIndex()) == text:
            return data
        # A typed ID, or a name matching a listed item.
        index = self.gift.findText(text, Qt.MatchFlag.MatchStartsWith)
        return self.gift.itemData(index) if index >= 0 and "·" not in text else text

    def _spec(self):
        occasion = self.occasion.currentData()
        if occasion == "weekday":
            return {"occasion": occasion, "day": self.day.currentData(), "season": self.season.currentData(),
                    "hearts": self.hearts.currentData()}
        if occasion == "date":
            return {"occasion": occasion, "season": self.date_season.currentData(), "day": self.date_day.value()}
        if occasion == "place":
            return {"occasion": occasion, "location": self.place.value(), "day": self.place_day.currentData()}
        if occasion == "gift":
            return {"occasion": occasion, "item": self._gift_value()}
        if occasion == "birthday_gift":
            return {"occasion": occasion, "liked": self.birthday.currentData()}
        if occasion == "other":
            return {"occasion": occasion, "key": self.other.text()}
        return {"occasion": "introduction"}

    def value(self):
        return self._key

    def text(self):
        return self._key

    def _show_key(self):
        self.key_label.setText(f"Game key: {self._key}" if self._key else "Game key: not chosen yet")

    def _occasion_changed(self, *_):
        self.stack.setCurrentIndex(self.pages[self.occasion.currentData()])
        if self.occasion.currentData() == "other" and not self._loading:
            self.other.setText(self._key)
        self._edited()

    def _edited(self, *_):
        if self._loading:
            return
        try:
            key = build_trigger(self._spec())
        except ValueError:
            key = self._key if self.occasion.currentData() != "other" else self.other.text()
        if key != self._key:
            self._key = key
            self._show_key()
            self.changed.emit()

    def set_value(self, key):
        """Show a saved key without reporting a change."""
        key = key if isinstance(key, str) else ""
        places = getattr(self.place, "_extra_locations", {})
        spec = parse_trigger(key, places=places)
        self._loading = True
        try:
            occasion = spec["occasion"]
            if occasion == "gift" and self.gift.findData(spec["item"]) < 0:
                self._fill_items()
            self.occasion.setCurrentIndex(self.occasion.findData(occasion))
            self.stack.setCurrentIndex(self.pages[occasion])
            if occasion == "weekday":
                self.day.setCurrentIndex(self.day.findData(spec["day"]))
                self.season.setCurrentIndex(self.season.findData(spec["season"]))
                self.hearts.setCurrentIndex(self.hearts.findData(spec["hearts"]))
            elif occasion == "date":
                self.date_season.setCurrentIndex(self.date_season.findData(spec["season"]))
                self.date_day.setValue(spec["day"])
            elif occasion == "place":
                self.place.set_value(spec["location"])
                self.place_day.setCurrentIndex(self.place_day.findData(spec["day"]))
            elif occasion == "gift":
                index = self.gift.findData(spec["item"])
                if index >= 0:
                    self.gift.setCurrentIndex(index)
                else:
                    self.gift.setCurrentIndex(-1)
                    self.gift.setEditText(spec["item"])
            elif occasion == "birthday_gift":
                self.birthday.setCurrentIndex(self.birthday.findData(spec["liked"]))
            elif occasion == "other":
                self.other.setText(key)
        finally:
            self._loading = False
        self._key = key
        self._show_key()

    def setText(self, key):
        self.set_value(key)


class EmotionBar(QWidget):
    """Feeling buttons, plus New box and Farmer's name, acting on a dialogue text box.

    ``compact`` gives one **Feeling ▾** menu for narrow panels; ``buttons`` then
    holds its actions instead of push buttons.
    """

    def __init__(self, edit, parent=None, *, compact=False):
        super().__init__(parent)
        self.edit = edit
        self.compact = compact
        if compact:
            self._build_menu()
            return
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        # Three feelings to a row keeps the bar inside the narrowest editor.
        feelings = QGridLayout()
        feelings.setContentsMargins(0, 0, 0, 0)
        feelings.setHorizontalSpacing(4)
        feelings.setVerticalSpacing(4)
        layout.addLayout(feelings)
        self.buttons = {}
        for position, emotion in enumerate(EMOTIONS):
            widget = QPushButton(emotion.label)
            widget.setObjectName("quiet")
            widget.setIconSize(QSize(24, 24))
            widget.setToolTip(f"Show their {emotion.label.lower()} portrait in this box ({emotion.command})")
            widget.setAccessibleName(f"{emotion.label} expression")
            widget.clicked.connect(lambda _=False, command=emotion.command: self.set_emotion(command))
            feelings.addWidget(widget, position // 3, position % 3)
            self.buttons[emotion.key] = widget
        layout.addStretch()
        inserts = QVBoxLayout()
        inserts.setSpacing(4)
        self.new_box = button("New box", lambda: self.insert("#$b#"), "quiet")
        self.new_box.setToolTip("Continue in a new dialogue box (#$b#)")
        self.farmer = button("Farmer's name", lambda: self.insert("@"), "quiet")
        self.farmer.setToolTip("The player's name (@)")
        inserts.addWidget(self.new_box)
        inserts.addWidget(self.farmer)
        layout.addLayout(inserts)

    def _build_menu(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.menu_button = QToolButton()
        self.menu_button.setText("Feeling ▾")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_button.setAccessibleName("Feeling and dialogue helpers")
        menu = QMenu(self.menu_button)
        self.buttons = {}
        for emotion in EMOTIONS:
            action = menu.addAction(emotion.label, lambda command=emotion.command: self.set_emotion(command))
            action.setToolTip(f"Show their {emotion.label.lower()} portrait in this box ({emotion.command})")
            self.buttons[emotion.key] = action
        menu.addSeparator()
        self.new_box = menu.addAction("New box", lambda: self.insert("#$b#"))
        self.farmer = menu.addAction("Farmer's name", lambda: self.insert("@"))
        self.menu_button.setMenu(menu)
        layout.addWidget(self.menu_button)
        layout.addStretch()

    def set_portraits(self, frames):
        for emotion in EMOTIONS:
            frame = frames[emotion.index] if emotion.index < len(frames) else None
            self.buttons[emotion.key].setIcon(QIcon(frame) if frame is not None else QIcon())

    def _replace_all(self, text, position):
        cursor = self.edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        cursor.setPosition(min(position, len(text)))
        self.edit.setTextCursor(cursor)
        self.edit.setFocus()

    def set_emotion(self, command):
        text, position = set_emotion(self.edit.toPlainText(), self.edit.textCursor().position(), command)
        if text != self.edit.toPlainText():
            self._replace_all(text, position)

    def insert(self, snippet):
        self.edit.insertPlainText(snippet)
        self.edit.setFocus()


class _BoxCanvas(QWidget):
    def __init__(self, preview):
        super().__init__(preview)
        self.preview = preview
        self.setMinimumHeight(176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(560, 176)

    def paintEvent(self, event):
        preview = self.preview
        painter = QPainter(self)
        ink, paper = QColor(COLORS["outline"]), QColor(COLORS["paper"])
        rect = self.rect().adjusted(2, 2, -3, -3)
        painter.fillRect(rect, paper)
        painter.setPen(QPen(ink, 2))
        painter.drawRect(rect)
        portrait_size = min(128, rect.height() - 40)
        side = QRect(rect.right() - portrait_size - 24, rect.top() + 8, portrait_size + 16, rect.height() - 16)
        painter.setPen(QPen(QColor(COLORS["rule_strong"]), 2))
        painter.drawLine(side.left() - 8, rect.top() + 10, side.left() - 8, rect.bottom() - 10)
        page = preview.current()
        frame = QRect(side.left() + 8, side.top(), portrait_size, portrait_size)
        painter.setPen(QPen(ink, 2))
        painter.drawRect(frame.adjusted(-2, -2, 1, 1))
        frames = preview.frames
        if page is not None and page.portrait < len(frames):
            painter.drawPixmap(frame, frames[page.portrait].scaled(
                frame.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        else:
            painter.setPen(QColor(COLORS["muted"]))
            caption = next((e.label for e in EMOTIONS if page and e.index == page.portrait), "Portrait") if page else "Portrait"
            painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, caption)
        font = QFont(pixel_family())
        font.setPixelSize(15)
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(QRect(side.left(), frame.bottom() + 6, side.width(), 22), Qt.AlignmentFlag.AlignCenter,
                         preview.name or "Their name")
        text_area = QRect(rect.left() + 18, rect.top() + 16, side.left() - rect.left() - 40, rect.height() - 32)
        font.setPixelSize(17)
        painter.setFont(font)
        painter.setPen(ink if page is None or not page.raw else QColor(COLORS["muted"]))
        lines = page.lines if page is not None else ("Your dialogue will appear here.",)
        height = max(1, text_area.height() // BOX_LINES)
        for index, line in enumerate(lines[:BOX_LINES]):
            painter.drawText(QRect(text_area.left(), text_area.top() + index * height, text_area.width(), height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.end()


class DialogueBoxPreview(QWidget):
    """The line as the game's dialogue box shows it, one box at a time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages, self.page, self.name, self.frames = [], 0, "", []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.canvas = _BoxCanvas(self)
        self.canvas.setAccessibleName("Dialogue box preview")
        layout.addWidget(self.canvas)
        row = QHBoxLayout()
        self.previous = button("◀", lambda: self.show_page(self.page - 1), "quiet")
        self.previous.setAccessibleName("Previous box")
        self.next = button("▶", lambda: self.show_page(self.page + 1), "quiet")
        self.next.setAccessibleName("Next box")
        self.position = label("", "hint")
        row.addWidget(self.previous)
        row.addWidget(self.position)
        row.addWidget(self.next)
        row.addStretch()
        layout.addLayout(row)
        self.note = label("", "hint", True)
        layout.addWidget(self.note)
        self.set_dialogue("")

    def page_count(self):
        return len(self.pages)

    def current(self):
        return self.pages[self.page] if self.pages else None

    def set_portraits(self, frames):
        self.frames = list(frames)
        self.canvas.update()

    def set_dialogue(self, text, *, name=None):
        if name is not None:
            self.name = name
        self.pages = dialogue_pages(text)
        self.show_page(min(self.page, max(0, len(self.pages) - 1)))

    def show_page(self, index):
        self.page = max(0, min(index, len(self.pages) - 1)) if self.pages else 0
        page = self.current()
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < len(self.pages))
        if page is None:
            self.position.setText("No boxes yet")
        else:
            where = f"Box {self.page + 1} of {len(self.pages)}"
            if page.new_conversation:
                where += " · next time you talk"
            elif page.continued:
                where += " · continues automatically"
            self.position.setText(where)
        notes = ["Line breaks are approximate; the game measures its own font."]
        if page is not None and page.raw:
            notes.insert(0, "Game commands in this box are shown as written.")
        self.note.setText(" ".join(notes))
        self.canvas.update()
