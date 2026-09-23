"""Plain-language authoring for daily life, changing routines, and marriage."""
from copy import deepcopy
import uuid

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QListWidget, QSplitter, QScrollArea, QComboBox, QCheckBox, QPlainTextEdit, QLabel,
    QStackedWidget,
)

from pixelheart_core.life import (
    COLLECTIONS, MOMENTS, SEASONS, WEEKDAYS, DAY_NAMES, normalize_life,
    new_life_record, life_issues,
)
from .editors import SchedulePage, line, number, set_value, value, connect_change
from .widgets import button, card, label


def _choices(options):
    widget = QComboBox()
    for caption, data in options:
        widget.addItem(caption, data)
    return widget


class LifeRules(QWidget):
    changed = Signal()

    def __init__(self, page, kind):
        super().__init__()
        self.page, self.kind = page, kind
        self.records, self.current, self.loading, self.removed = [], -1, False, None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        captions = {"dialogues": "Conversations that remember", "routines": "A routine for each part of life", "spouse_dialogue": "Their voice after the wedding"}
        descriptions = {
            "dialogues": "Replace everyday weekday greetings when a season, relationship, or completed chapter matches. Special festival and location dialogue still follows the game's own priority.",
            "routines": "Choose the days and circumstances, then edit destinations below. Include a married routine to author life after moving in. The normal Schedule remains the fallback.",
            "spouse_dialogue": "Write mornings and evenings in their own voice. Each conversation fills every random slot for the selected moment; the game's other spouse interactions retain their defaults.",
        }
        root.addWidget(label(captions[kind], "sectionTitle", True))
        root.addWidget(label(descriptions[kind], "muted", True))
        root.addWidget(label("Rules are checked each morning. Lower matching rules take priority. Leave unfinished rules as drafts; check Include in mod after reviewing them.", "notice", True))
        actions = QHBoxLayout()
        self.add_button = button("+ Add " + ("routine" if kind == "routines" else "conversation"), self.add, "primary")
        actions.addWidget(self.add_button)
        actions.addWidget(button("Move up", lambda: self.move(-1)))
        actions.addWidget(button("Move down", lambda: self.move(1)))
        actions.addStretch()
        actions.addWidget(button("Remove", self.remove, "danger"))
        self.undo = button("Undo remove", self.restore, "quiet")
        self.undo.setEnabled(False)
        actions.addWidget(self.undo)
        root.addLayout(actions)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(190)
        self.list.setAccessibleName(captions[kind] + " rules")
        self.list.currentRowChanged.connect(self.select)
        splitter.addWidget(self.list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(14, 0, 0, 0)
        self.fields = {"name": line("What changes for this conversation?", 100), "enabled": QCheckBox("Include this rule in the next mod export")}
        self.fields["enabled"].setAccessibleName("Include rule in mod")
        form = QFormLayout()
        form.addRow("Name", self.fields["name"])
        form.addRow(self.fields["enabled"])
        if kind == "spouse_dialogue":
            self.fields["moment"] = _choices([(caption, key) for key, caption in MOMENTS.items()])
            form.addRow("Moment", self.fields["moment"])
        detail.addLayout(form)
        condition_card, condition_layout = card("When this belongs in their life")
        self.conditions = {
            "season": _choices([("Any season", "any")] + [(season.title(), season) for season in SEASONS]),
            "weather": _choices([("Any weather", "any"), ("Sunshine or wind", "sunny"), ("Rain or storms", "rainy"), ("Snow", "snowy")]),
            "weekday": _choices([("Every day", "any")] + [(DAY_NAMES[day], day) for day in WEEKDAYS]),
            "relationship": _choices([("All unmarried stages" if kind == "routines" else "Any relationship", "any"), ("Not married to this NPC", "unmarried"), ("Dating this NPC", "dating"), ("Married to this NPC", "married")]),
            "min_hearts": number(0, 14), "after_event_id": QComboBox(),
            "min_house_upgrade": _choices([("Any home", 0), ("Kitchen or larger", 1), ("Family home or larger", 2), ("Cellar upgrade", 3)]),
        }
        grid = QGridLayout()
        for index, (caption, key) in enumerate((("Season", "season"), ("Weather", "weather"), ("Day", "weekday"), ("Relationship", "relationship"), ("At least this many hearts", "min_hearts"), ("After this story event", "after_event_id"), ("Farmer's home", "min_house_upgrade"))):
            box = QVBoxLayout()
            box.addWidget(label(caption, "muted"))
            self.conditions[key].setAccessibleName(caption)
            box.addWidget(self.conditions[key])
            grid.addLayout(box, index // 2, index % 2)
        condition_layout.addLayout(grid)
        detail.addWidget(condition_card)
        if kind == "routines":
            self.schedule = SchedulePage(compact=True)
            # The standalone schedule's explanation describes the base route.
            for widget in self.schedule.findChildren(QLabel):
                if widget.text().startswith("One daily routine"):
                    widget.setText("This route applies when the conditions above match. Times must increase in ten-minute steps. Married routes need a tested path back toward the farm; end at the bus stop's farm exit or a verified destination.")
            self.schedule.changed.connect(self.edit)
            detail.addWidget(self.schedule)
            detail.addWidget(button("+ Return to farmhouse", self.add_home, "quiet"))
        else:
            self.fields["text"] = QPlainTextEdit()
            self.fields["text"].setPlaceholderText("Write their words. Use @ for the farmer's name.")
            self.fields["text"].setMinimumHeight(170)
            self.fields["text"].setAccessibleName("Their dialogue")
            detail.addWidget(self.fields["text"])
        self.checks = label("", "notice", True)
        detail.addWidget(self.checks)
        detail.addStretch()
        scroll.setWidget(self.detail)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(scroll)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(30, 24, 30, 24)
        empty_layout.addStretch()
        noun = "routine" if kind == "routines" else "conversation"
        empty_title = label(f"No {noun}s yet", "sectionTitle", True)
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = label(f"Add a {noun} to start shaping this part of their life. You can keep it as a draft while you work.", "muted", True)
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_hint)
        empty_layout.addStretch()
        self.detail_stack.addWidget(empty)
        splitter.addWidget(self.detail_stack)
        splitter.setSizes([220, 620])
        root.addWidget(splitter, 1)
        for widget in (*self.fields.values(), *self.conditions.values()):
            connect_change(widget, self.edit)

    def load(self, records):
        self.records = deepcopy(records)
        self.removed = None
        self.undo.setEnabled(False)
        self.refresh()

    def caption(self, row):
        return ("IN MOD  ·  " if row.get("enabled") else "DRAFT  ·  ") + (row.get("name") or "Untitled")

    def refresh(self, selected=0):
        self.loading = True
        self.list.clear()
        self.list.addItems([self.caption(row) for row in self.records])
        self.loading = False
        self.add_button.setEnabled(len(self.records) < 100)
        if self.records:
            self.list.setCurrentRow(max(0, min(selected, len(self.records) - 1)))
        else:
            self.select(-1)

    def select(self, index):
        if self.loading:
            return
        self.current = index
        self.detail_stack.setCurrentIndex(0 if index >= 0 else 1)
        if index < 0:
            return
        self.loading = True
        row = self.records[index]
        for key, widget in self.fields.items():
            set_value(widget, row[key])
        self.refresh_events(row["conditions"].get("after_event_id", ""))
        for key, widget in self.conditions.items():
            set_value(widget, row["conditions"][key])
        if self.kind == "routines":
            self.schedule.load(row["stops"])
        self.loading = False
        self.update_checks()

    def refresh_events(self, selected=None):
        widget = self.conditions["after_event_id"]
        if selected is None:
            selected = widget.currentData() or ""
        blocked = widget.blockSignals(True)
        widget.clear()
        widget.addItem("No earlier story required", "")
        for event in self.page.character().get("events", []):
            if isinstance(event, dict):
                widget.addItem(event.get("name") or "Untitled event", event.get("id"))
        if selected and widget.findData(selected) < 0:
            widget.addItem("Missing event — choose a replacement", selected)
        widget.setCurrentIndex(max(0, widget.findData(selected)))
        widget.blockSignals(blocked)

    def edit(self, *_):
        if self.loading or not 0 <= self.current < len(self.records):
            return
        row = self.records[self.current]
        for key, widget in self.fields.items():
            row[key] = value(widget)
        row["conditions"].update({key: value(widget) for key, widget in self.conditions.items()})
        if self.kind == "routines":
            row["stops"] = self.schedule.dump()
        self.list.item(self.current).setText(self.caption(row))
        self.changed.emit()
        self.update_checks()

    def update_checks(self):
        if self.current < 0:
            return
        row = self.records[self.current]
        if not row["enabled"]:
            self.checks.setText("Saved as a draft. Review the rule, then check Include in mod when you want it to play.")
            return
        character = self.page.character()
        character["life"] = self.page.dump()
        prefix = f"life.{self.kind}.{self.current}"
        issues = [issue["message"] for issue in life_issues(character) if issue["field"] == prefix or issue["field"].startswith(prefix + ".")]
        self.checks.setText("\n".join(issues) if issues else "Included in the next export. Test this condition on a new in-game day.")

    def add(self):
        if len(self.records) >= 100:
            return
        self.records.append(new_life_record(self.kind, self.page.character()))
        self.refresh(len(self.records) - 1)
        self.changed.emit()

    def add_home(self):
        if self.kind != "routines" or self.current < 0 or len(self.schedule.records) >= 100:
            return
        stops = self.schedule.dump()
        # Use the game's bed destination so no farmhouse layout coordinate is
        # guessed. The game routes married NPCs home through the bus stop.
        from .schedule_time import game_minutes, game_time
        last = game_minutes(stops[-1].get("time", "")) if stops else 360
        if last is None or last >= 1560:
            self.checks.setText("Move the last stop earlier than 26:00 before adding a return home.")
            return
        time = game_time(max(1320, min(1560, last + 60)))
        stops.append({"id": str(uuid.uuid4()), "time": time, "location": "bed", "x": 0, "y": 0,
                      "facing": "down", "activity": "Return to the spouse's home"})
        self.schedule.load(stops)
        self.edit()

    def move(self, delta):
        target = self.current + delta
        if 0 <= self.current < len(self.records) and 0 <= target < len(self.records):
            self.records[self.current], self.records[target] = self.records[target], self.records[self.current]
            self.refresh(target)
            self.changed.emit()

    def remove(self):
        if 0 <= self.current < len(self.records):
            self.removed = (self.current, self.records.pop(self.current))
            self.undo.setEnabled(True)
            self.refresh(self.current)
            self.changed.emit()

    def restore(self):
        if self.removed is not None and len(self.records) < 100:
            index, row = self.removed
            self.records.insert(min(index, len(self.records)), row)
            self.removed = None
            self.undo.setEnabled(False)
            self.refresh(index)
            self.changed.emit()


class LifePage(QObject):
    """Shared life data for the Dialogue and Schedule rule editors."""
    changed = Signal()

    def __init__(self, window=None):
        super().__init__(window)
        self.window = window
        self._character = {}
        self._life = normalize_life()
        self.editors = {}
        for kind in COLLECTIONS:
            editor = LifeRules(self, kind)
            editor.changed.connect(self.changed)
            self.editors[kind] = editor

    def character(self):
        if self.window is not None and hasattr(self.window, "document"):
            return deepcopy(self.window.document["character"])
        return deepcopy(self._character)

    def load(self, character):
        self._character = deepcopy(character)
        self._life = normalize_life(character.get("life", {}))
        for kind, editor in self.editors.items():
            editor.load(self._life[kind])

    def dump(self):
        result = deepcopy(self._life)
        result.update({kind: deepcopy(editor.records) for kind, editor in self.editors.items()})
        return result

    def refresh_context(self):
        for editor in self.editors.values():
            editor.refresh_events()
            editor.update_checks()

    def open_issue(self, field):
        parts = field.split(".")
        if len(parts) >= 2 and parts[1] in self.editors:
            if self.window is not None:
                self.window.open_life_editor(parts[1])
            editor = self.editors[parts[1]]
            if len(parts) >= 3 and parts[2].isdigit():
                editor.list.setCurrentRow(int(parts[2]))
            key = parts[-1]
            widget = editor.conditions.get(key) or editor.fields.get(key)
            if widget:
                widget.setFocus()
