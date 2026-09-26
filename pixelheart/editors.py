"""Authoring pages backed by ordinary character dictionaries."""

from copy import deepcopy
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLineEdit,
    QPlainTextEdit, QComboBox, QSpinBox, QCheckBox, QListWidget, QSplitter,
    QTableWidget, QHeaderView, QAbstractItemView, QDialog,
)

from .widgets import label, button, card, ArtworkPreview
from .birthday_calendar import BirthdayCalendar
from .location_picker import MapSelector
from .schedule_time import ScheduleTime, game_minutes, game_time
from .dialogue_templates import DialogueTemplateDialog
from pixelheart_core.dialogue_templates import MAX_DIALOGUES, dialogue_preview
from pixelheart_core.local_templates import LocalTemplateError, project_dialogue_folder
from pixelheart_core.validation import infer_legacy_gender


def line(placeholder="", maximum=8000):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setMaxLength(maximum)
    return widget


def combo(values):
    widget = QComboBox()
    for value in values:
        widget.addItem(value.replace("_", " ").capitalize(), value)
    return widget


def number(low=0, high=1000):
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
    return widget


def value(widget):
    if isinstance(widget, MapSelector):
        return widget.value()
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QPlainTextEdit):
        return widget.toPlainText()
    if isinstance(widget, QComboBox):
        return widget.currentData()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QCheckBox):
        return widget.isChecked()


def set_value(widget, content):
    if isinstance(widget, MapSelector):
        widget.set_value(str(content))
    elif isinstance(widget, ScheduleTime):
        widget.setText(str(content))
    elif isinstance(widget, QLineEdit):
        widget.setText(str(content))
    elif isinstance(widget, QPlainTextEdit):
        widget.setPlainText(str(content))
    elif isinstance(widget, QComboBox):
        index = widget.findData(content)
        if index >= 0:
            widget.setCurrentIndex(index)
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(content))
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(content))


def connect_change(widget, callback):
    if isinstance(widget, MapSelector):
        widget.changed.connect(callback)
    elif isinstance(widget, (QLineEdit, QPlainTextEdit)):
        widget.textChanged.connect(callback)
    elif isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(callback)
    elif isinstance(widget, QSpinBox):
        widget.valueChanged.connect(callback)
    elif isinstance(widget, QCheckBox):
        widget.toggled.connect(callback)


class IdentityPage(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.loading = False
        self.fields = {
            "name": line("What should the valley call them?", 64),
            "internal_name": line("NewCharacter", 64),
            "tagline": line("A little line that captures who they are", 160),
            "gender": QComboBox(),
            "occupation": line("e.g. botanist, baker, wandering musician", 80),
            "romanceable": QCheckBox("Open to romance"),
            "manners": combo(["polite", "neutral", "rude"]),
            "social_anxiety": combo(["shy", "neutral", "outgoing"]),
            "optimism": combo(["positive", "neutral", "negative"]),
            "bio": QPlainTextEdit(),
            "home_map": MapSelector(),
            "home_x": number(), "home_y": number(),
        }
        for caption, gender in (("Woman", "Female"), ("Man", "Male"), ("Unspecified", "Undefined")):
            self.fields["gender"].addItem(caption, gender)
        self.fields["gender"].setCurrentIndex(2)
        self.fields["gender"].setToolTip("Used by Stardew Valley's gendered dialogue. Unspecified exports as Undefined.")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)
        introduction = QHBoxLayout()
        introduction.setSpacing(20)
        root.addLayout(introduction)
        left = QVBoxLayout()
        left.setSpacing(20)
        introduction.addLayout(left, 3)
        basics, content = card("The basics", "Every memorable character starts with a few small details.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        placements = [("Display name", "name", 0, 0), ("Gender in game", "gender", 0, 1),
                      ("Occupation", "occupation", 2, 0), ("Internal name", "internal_name", 2, 1)]
        for caption, key, row, col in placements:
            caption_label = label(caption)
            caption_label.setBuddy(self.fields[key])
            grid.addWidget(caption_label, row, col)
            grid.addWidget(self.fields[key], row + 1, col)
            self.fields[key].setAccessibleName(caption)
        grid.addWidget(label("Their one-line story"), 4, 0, 1, 2)
        grid.addWidget(self.fields["tagline"], 5, 0, 1, 2)
        content.addLayout(grid)
        content.addWidget(label("Keep the internal name stable once a character is used in a save.", "hint", True))
        content.addWidget(self.fields["romanceable"])
        content.addWidget(label("All Pixelheart characters are adults.", "hint"))
        left.addWidget(basics)
        birthday_card, content = card("A day of their own", "Choose an open day on the valley's calendar. Existing NPC birthdays are reserved and cannot be selected.")
        self.birthday = BirthdayCalendar()
        content.addWidget(self.birthday)
        root.addWidget(birthday_card)
        personality, content = card("A little more human", "What makes them feel like someone you could meet?")
        traits = QHBoxLayout()
        for caption, key in [("Manners", "manners"), ("Social style", "social_anxiety"), ("Outlook", "optimism")]:
            column = QVBoxLayout()
            column.addWidget(label(caption))
            column.addWidget(self.fields[key])
            traits.addLayout(column)
        content.addLayout(traits)
        self.fields["bio"].setPlaceholderText("Their history, their habits, the thing they never talk about…")
        self.fields["bio"].setMinimumHeight(145)
        self.fields["bio"].setAccessibleName("Biography")
        content.addWidget(label("Character notes"))
        content.addWidget(self.fields["bio"])
        root.addWidget(personality)
        home, content = card("A place in the valley", "The map and tile where your character first appears.")
        row = QHBoxLayout()
        for caption, key in [("Home map", "home_map"), ("Tile X", "home_x"), ("Tile Y", "home_y")]:
            column = QVBoxLayout()
            column.setSpacing(8)
            column.addWidget(label(caption))
            column.addWidget(self.fields[key])
            row.addLayout(column, 2 if key == "home_map" else 1)
            row.setAlignment(column, Qt.AlignmentFlag.AlignTop)
        content.addLayout(row)
        content.addWidget(label("Choose an existing location, or select Custom / mod location for one added by a mod. The tile still needs to be walkable in-game.", "hint", True))
        root.addWidget(home)
        right = QVBoxLayout()
        right.setSpacing(16)
        introduction.addLayout(right, 1)
        profile, details = card()
        profile.setObjectName("profile")
        profile.setMinimumWidth(220)
        profile.setMaximumWidth(330)
        details.addWidget(label("CHARACTER CARD", "eyebrow"))
        self.portrait = ArtworkPreview("Add a portrait in Artwork")
        self.portrait.setFixedHeight(150)
        details.addWidget(self.portrait)
        self.profile_name = label("New character", "profileName", True)
        self.profile_tagline = label("A story waiting to be told.", "muted", True)
        self.profile_birthday = label("Spring 1", "muted")
        self.profile_romance = label("Adult · Romanceable", "badge")
        details.addWidget(self.profile_name)
        details.addWidget(self.profile_tagline)
        details.addSpacing(8)
        details.addWidget(self.profile_birthday)
        details.addWidget(self.profile_romance)
        right.addWidget(profile)
        right.addStretch()
        for key, widget in self.fields.items():
            widget.setAccessibleName({"day": "Birthday day", "season": "Birthday season", "social_anxiety": "Social style", "bio": "Biography"}.get(key, key.replace("_", " ").capitalize()))
            connect_change(widget, self._change)
        self.birthday.date_changed.connect(self._change)

    def _change(self):
        self.update_preview()
        if not self.loading:
            self.changed.emit()

    def update_preview(self):
        data = self.dump()
        self.profile_name.setText(data["name"] or "Your character")
        self.profile_tagline.setText(data["tagline"] or data["occupation"] or "A story waiting to be told.")
        self.profile_birthday.setText(f"Birthday  ·  {data['season'].title()} {data['day']}")
        self.profile_romance.setText("Adult  ·  " + ("Open to romance" if data["romanceable"] else "Romance disabled"))

    def load(self, data):
        self.loading = True
        # Older projects keep their authored pronouns as metadata. New changes
        # use an explicit game gender and never rewrite those original notes.
        legacy_gender = infer_legacy_gender(data.get("pronouns"))
        set_value(self.fields["gender"], data.get("gender", legacy_gender))
        self.birthday.set_date(data.get("season", "spring"), data.get("day", 1))
        for key, widget in self.fields.items():
            if key in data:
                set_value(widget, data[key])
        self.loading = False
        self.update_preview()

    def dump(self):
        return {**{key: value(widget) for key, widget in self.fields.items()},
                "season": self.birthday.selected_season, "day": self.birthday.selected_day}


class DialoguePage(QWidget):
    """List/detail editor keeps entry identities stable while reordering or editing."""
    changed = Signal()

    def __init__(self, project_window=None):
        super().__init__()
        self.project_window = project_window
        self.project_file = None
        self.records = []
        self.loading = False
        self.current = -1
        self.fields = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        self.example_prompt = label("Load Abigail’s or Elliott’s full dialogue from your game, then edit the conversations in your character’s voice.", "notice", True)
        root.addWidget(self.example_prompt)
        self.project_location = label("Save your NPC project to create its dialogue template folder.", "hint", True)
        self.project_location.setTextFormat(Qt.TextFormat.PlainText)
        self.project_location.setAccessibleName("Dialogue project location")
        root.addWidget(self.project_location)
        row = QHBoxLayout()
        row.addWidget(button("+ Add dialogue", self.add, "primary"))
        self.examples_button = button("Load dialogue template…", self.open_examples)
        row.addWidget(self.examples_button)
        self.duplicate_button = button("Duplicate", self.duplicate)
        row.addWidget(self.duplicate_button)
        row.addStretch()
        self.remove_button = button("Remove", self.remove, "danger")
        row.addWidget(self.remove_button)
        root.addLayout(row)
        splitter = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(190)
        self.list.setAccessibleName("Everyday dialogue")
        splitter.addWidget(self.list)
        self.editor, content = card("Write their voice")
        form = QFormLayout()
        form.setSpacing(12)
        self.fields = {"trigger": line("Introduction, Mon, spring_Mon2…", 120), "text": QPlainTextEdit()}
        form.addRow("When they say it", self.fields["trigger"])
        content.addLayout(form)
        content.addWidget(label("Dialogue", "muted"))
        self.fields["text"].setPlaceholderText("Hey, @. I was hoping I'd run into you today.$h")
        self.fields["text"].setMinimumHeight(190)
        content.addWidget(self.fields["text"])
        content.addWidget(label("@ = farmer's name    $h = happy    $s = sad    $l = love    #$b# = next dialogue box", "hint", True))
        preview, preview_layout = card("A first listen")
        self.preview = label("Your dialogue preview will appear here.", "profileName", True)
        self.preview.setStyleSheet("font-size: 20px;")
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(label("Text preview · Game commands and portrait changes need in-game review.", "hint", True))
        content.addWidget(preview)
        content.addStretch()
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 650])
        root.addWidget(splitter, 1)
        self.list.currentRowChanged.connect(self.select)
        for key, widget in self.fields.items():
            widget.setAccessibleName(key.replace("_", " ").capitalize())
            connect_change(widget, self.edit)

    def title(self, record):
        return record.get("trigger") or "Untitled"

    def load(self, records):
        self.records = deepcopy(records)
        self.refresh(0)

    def refresh(self, selected):
        self.loading = True
        self.example_prompt.setVisible(len(self.records) <= 1)
        self.list.clear()
        self.list.addItems([self.title(record) for record in self.records])
        self.current = -1
        self.loading = False
        if self.records:
            self.list.setCurrentRow(max(0, min(selected, len(self.records) - 1)))
        else:
            self.select(-1)

    def select(self, index):
        if self.loading:
            return
        self.current = index
        self.loading = True
        enabled = 0 <= index < len(self.records)
        self.editor.setEnabled(enabled)
        self.duplicate_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        if enabled:
            for key, widget in self.fields.items():
                set_value(widget, self.records[index].get(key, ""))
        else:
            for widget in self.fields.values():
                if isinstance(widget, (QLineEdit, QPlainTextEdit)):
                    widget.clear()
        self.loading = False
        self.update_preview()

    def edit(self):
        if self.loading or self.current < 0:
            return
        self.records[self.current].update({key: value(widget) for key, widget in self.fields.items()})
        self.list.item(self.current).setText(self.title(self.records[self.current]))
        self.update_preview()
        self.changed.emit()

    def update_preview(self):
        text = dialogue_preview(value(self.fields["text"]))
        self.preview.setText(text or "Your dialogue preview will appear here.")

    def set_project_file(self, project_file):
        self.project_file = project_file
        if project_file is None:
            self.project_location.setText("Save your NPC project to create its dialogue template folder.")
            return
        try:
            folder = project_dialogue_folder(project_file, create=True)
            self.project_location.setText(f"Dialogue templates: {folder}")
        except (LocalTemplateError, OSError) as exc:
            self.project_location.setText(str(exc))

    def open_examples(self):
        if self.project_window is not None:
            if not self.project_window.ensure_saved():
                return
            self.set_project_file(self.project_window.project_file)
        dialog = DialogueTemplateDialog(self.records, self, project_file=self.project_file)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.imported_records is None:
                return
            records = dialog.imported_records
            if records == self.records:
                return
            original = {row["id"]: row for row in self.records}
            selected = next((i for i, row in enumerate(records) if original.get(row["id"]) != row), 0)
            self.records = deepcopy(records)
            self.refresh(selected)
            self.changed.emit()
        finally:
            dialog.deleteLater()

    def add(self):
        if len(self.records) >= MAX_DIALOGUES:
            return
        record = {"id": str(uuid.uuid4())}
        used = {record.get("trigger") for record in self.records}
        trigger = next((key for key in ("Introduction", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if key not in used), "")
        record.update(trigger=trigger, text="")
        self.records.append(record)
        self.refresh(len(self.records) - 1)
        self.changed.emit()

    def duplicate(self):
        if self.current < 0 or len(self.records) >= MAX_DIALOGUES:
            return
        record = deepcopy(self.records[self.current])
        record["id"] = str(uuid.uuid4())
        record["trigger"] = ""
        self.records.append(record)
        self.refresh(len(self.records) - 1)
        self.changed.emit()

    def remove(self):
        if self.current >= 0:
            selected = self.current
            del self.records[selected]
            self.refresh(selected)
            self.changed.emit()

    def dump(self):
        return deepcopy(self.records)


class SchedulePage(QWidget):
    changed = Signal()

    def __init__(self, *, compact=False):
        super().__init__()
        self.records = []
        self.loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(label("One daily routine is the default before marriage. Add alternatives in Conditional routines. Times must increase in ten-minute steps from 06:00 to 26:00.", "notice", True))
        row = QHBoxLayout()
        if compact:
            row.setSpacing(6)
        self.add_button = button("+ Add stop" if compact else "+ Add a stop", self.add, "primary")
        row.addWidget(self.add_button)
        row.addWidget(button("Move up", lambda: self.move(-1)))
        row.addWidget(button("Move down", lambda: self.move(1)))
        row.addStretch()
        row.addWidget(button("Remove" if compact else "Remove stop", self.remove, "danger"))
        root.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Time", "Map", "Tile X", "Tile Y", "Facing", "Activity note"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 130), (2, 80), (3, 80), (4, 100)):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        if compact:
            for column, width in ((1, 200), (5, 180)):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                self.table.setColumnWidth(column, width)
            self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMinimumHeight(180 if compact else 310)
        root.addWidget(self.table)
        root.addWidget(label("Choose existing locations from the map selector. Custom locations must be added by a mod. Activity notes describe intent; they do not create animations.", "muted", True))
        root.addStretch()

    def load(self, records):
        self.records = deepcopy(records)
        self.render()

    def render(self):
        self.loading = True
        self.table.setRowCount(0)
        for row, record in enumerate(self.records):
            self.table.insertRow(row)
            for column, key in enumerate(("time", "location", "x", "y", "facing", "activity")):
                widget = number() if key in ("x", "y") else combo(["up", "right", "down", "left"]) if key == "facing" else MapSelector(compact=True) if key == "location" else ScheduleTime() if key == "time" else line(maximum=300)
                content = record.get(key, "")
                if key == "facing" and str(content) in ("0", "1", "2", "3"):
                    content = ("up", "right", "down", "left")[int(content)]
                set_value(widget, content)
                widget.setAccessibleName(f"Stop {row + 1} {key}")
                widget.setProperty("row", row)
                widget.setProperty("field", key)
                connect_change(widget, lambda *args, w=widget: self.edit(w))
                self.table.setCellWidget(row, column, widget)
            self.table.setRowHeight(row, max(56, self.table.cellWidget(row, 1).sizeHint().height() + 6))
        last_time = game_minutes(self.records[-1].get("time", "")) if self.records else None
        can_add = len(self.records) < 100 and last_time != 1560
        self.add_button.setEnabled(can_add)
        self.add_button.setToolTip("The last stop is at 26:00. Move it earlier to add another stop." if last_time == 1560 else "Add the next stop to the daily routine.")
        self.loading = False

    def edit(self, widget):
        if not self.loading:
            self.records[widget.property("row")][widget.property("field")] = value(widget)
            if isinstance(widget, MapSelector):
                self.table.setRowHeight(widget.property("row"), max(56, widget.sizeHint().height() + 6))
            last_time = game_minutes(self.records[-1].get("time", "")) if self.records else None
            self.add_button.setEnabled(len(self.records) < 100 and last_time != 1560)
            self.add_button.setToolTip("The last stop is at 26:00. Move it earlier to add another stop." if last_time == 1560 else "Add the next stop to the daily routine.")
            self.changed.emit()

    def add(self):
        if len(self.records) >= 100:
            return
        previous = self.records[-1] if self.records else {}
        last_time = game_minutes(previous.get("time", "600"))
        if self.records and last_time == 1560:
            return
        next_time = game_time(min(1560, (last_time if last_time is not None else 360) + 60)) if self.records else "600"
        self.records.append({"id": str(uuid.uuid4()), "time": next_time, "location": previous.get("location", "Town"), "x": previous.get("x", 32), "y": previous.get("y", 62), "facing": previous.get("facing", "down"), "activity": ""})
        self.render()
        self.table.selectRow(len(self.records) - 1)
        self.changed.emit()

    def move(self, step):
        row = self.table.currentRow()
        if row >= 0 and 0 <= row + step < len(self.records):
            self.records[row], self.records[row + step] = self.records[row + step], self.records[row]
            self.render()
            self.table.selectRow(row + step)
            self.changed.emit()

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.records[row]
            self.render()
            if self.records:
                self.table.selectRow(min(row, len(self.records) - 1))
            self.changed.emit()

    def dump(self):
        return deepcopy(self.records)
