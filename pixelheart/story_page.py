"""A guided, offline workshop for authored events and relationship arcs."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QTabWidget,
    QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit, QComboBox,
    QTableWidget, QHeaderView, QCheckBox, QAbstractItemView, QScrollArea, QDialog,
)

from pixelheart_core.story import (
    new_event, new_relationship, normalize_event, normalize_relationship,
    new_actor, new_beat, event_issues, story_issues, compile_story,
    event_game_id, relationship_events,
)
from .editors import line, number, value, set_value, connect_change
from .widgets import label, button, card
from .location_picker import MapSelector
from .stage_canvas import StageCanvas
from .actor_picker import ActorSelector, vanilla_actors


STAGES = {"idea": "Idea", "outline": "Outline", "scene": "Scene", "ready": "Ready"}
KINDS = {"dialogue": "Dialogue", "emote": "Expression", "move": "Movement", "pause": "Pause", "friendship": "Friendship change", "choice": "Player choice"}


def choices(items):
    widget = QComboBox()
    for caption, data in items:
        widget.addItem(caption, data)
    return widget


def prose(placeholder, height=95):
    widget = QPlainTextEdit()
    widget.setPlaceholderText(placeholder)
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height + 35)
    return widget


def add_form(layout, fields):
    form = QFormLayout()
    form.setSpacing(12)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for caption, widget in fields:
        widget.setAccessibleName(caption)
        form.addRow(caption, widget)
    layout.addLayout(form)
    return form


def actor_caption(name, character):
    return character.get("name", "Your character") if name == "$npc" else "Farmer" if name == "farmer" else name


class CastEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.records = []
        self.loading = False
        self.project_history = None
        self.removed = None
        self.options = [("Your character", "$npc"), ("Farmer", "farmer"), *[(name, name) for name in vanilla_actors()]]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Choose everyone who appears and place them at the start of the scene. Farmer is the player. Characters from another mod can be added with their exact internal name.", "hint", True))
        self.canvas = StageCanvas()
        layout.addWidget(self.canvas)
        stage_actions = QHBoxLayout()
        stage_actions.addWidget(button("Open staging board…", self.open_staging))
        stage_actions.addWidget(button("Fit cast in view", self.canvas.fit, "quiet"))
        stage_actions.addStretch()
        layout.addLayout(stage_actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Character", "Tile X", "Tile Y", "Facing"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(145)
        self.table.setMaximumHeight(235)
        self.table.setAccessibleName("Scene cast and starting positions")
        self.table.currentCellChanged.connect(lambda row, *_: self.canvas.select_actor(row))
        self.canvas.actorSelected.connect(lambda row: self.table.setCurrentCell(row, 0))
        self.canvas.actorMoved.connect(self.move_actor)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        actions.addWidget(button("+ Add character", self.add))
        actions.addWidget(button("Remove character", self.remove, "quiet"))
        self.undo_button = button("Undo remove", self.restore_removed, "quiet")
        self.undo_button.hide()
        actions.addWidget(self.undo_button)
        actions.addStretch()
        layout.addLayout(actions)

    def load(self, records):
        self.canvas._finish_gesture()
        self.records = deepcopy(records)
        self.removed = None
        self.undo_button.hide()
        self.render()
        self.canvas.load(self.records, fit=True)

    def set_project_history(self, controller):
        self.project_history = controller
        self._connect_history_gesture(self.canvas)

    def _connect_history_gesture(self, canvas):
        if self.project_history is not None:
            canvas.gestureStarted.connect(lambda: self.project_history.begin_gesture(("cast", id(canvas))))
            canvas.gestureFinished.connect(self.project_history.end_gesture)

    def render(self):
        self.loading = True
        self.table.setRowCount(0)
        for row, record in enumerate(self.records):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 46)
            picker = ActorSelector()
            picker.set_options(self.options)
            widgets = [picker, number(0, 1000), number(0, 1000), choices([("Up", 0), ("Right", 1), ("Down", 2), ("Left", 3)])]
            for column, (key, widget) in enumerate(zip(("name", "x", "y", "facing"), widgets)):
                set_value(widget, record.get(key, "" if key == "name" else 0))
                widget.setAccessibleName(f"Character {row + 1} {key}")
                self.table.setCellWidget(row, column, widget)
                connect_change(widget, lambda r=row, k=key, w=widget: self.edit(r, k, w))
            self.table.setRowHeight(row, 88 if picker.combo.currentData() is None else 46)
        self.loading = False
        self.canvas.load(self.records)

    def set_options(self, options):
        self.options = options
        for row in range(self.table.rowCount()):
            picker = self.table.cellWidget(row, 0)
            picker.set_options(options)
            self.table.setRowHeight(row, 88 if picker.combo.currentData() is None else 46)
        self.canvas.names = {identity: caption for caption, identity in options}
        self.canvas.update()

    def move_actor(self, index, x, y):
        if not 0 <= index < len(self.records):
            return
        self.records[index].update(x=x, y=y)
        self.loading = True
        set_value(self.table.cellWidget(index, 1), x)
        set_value(self.table.cellWidget(index, 2), y)
        self.loading = False
        self.canvas.load(self.records)
        self.changed.emit()

    def open_staging(self):
        dialog = QDialog(self)
        dialog.setProperty("projectHistoryLive", True)
        dialog.setWindowTitle("Place the scene's cast")
        dialog.resize(1000, 700)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Choose a character, then click or drag them to a tile.", "sectionTitle", True))
        layout.addWidget(label("Arrow keys move the selected character one tile. Changes update the scene as you work. The supplied map preview does not check collision or pathfinding.", "muted", True))
        picker = choices([(f"{index + 1}. {self.canvas.names.get(actor['name'], actor['name'])}", index) for index, actor in enumerate(self.records)])
        picker.setAccessibleName("Character to place on the staging board")
        layout.addWidget(picker)
        canvas = StageCanvas()
        self._connect_history_gesture(canvas)
        canvas.setMaximumHeight(16777215)
        canvas.setMinimumHeight(350)
        canvas.load(self.records, self.canvas.names, fit=True)
        canvas.set_map(self.canvas.background_key)
        canvas.actorSelected.connect(picker.setCurrentIndex)
        picker.currentIndexChanged.connect(canvas.select_actor)
        canvas.actorMoved.connect(self.move_actor)
        def refresh_staging():
            canvas._finish_gesture()
            selected = picker.currentIndex()
            picker.blockSignals(True)
            picker.clear()
            for index, actor in enumerate(self.records):
                picker.addItem(f"{index + 1}. {self.canvas.names.get(actor['name'], actor['name'])}", index)
            picker.setCurrentIndex(min(selected, len(self.records) - 1))
            picker.blockSignals(False)
            canvas.load(self.records, self.canvas.names)
            canvas.set_map(self.canvas.background_key)
            canvas.select_actor(picker.currentIndex())
        dialog.refresh_project_history = refresh_staging
        layout.addWidget(canvas, 1)
        row = QHBoxLayout()
        row.addWidget(button("Fit cast in view", canvas.fit, "quiet"))
        row.addStretch()
        row.addWidget(button("Done", dialog.accept, "primary"))
        layout.addLayout(row)
        dialog.exec()
        dialog.deleteLater()

    def edit(self, row, key, widget):
        if not self.loading:
            self.records[row][key] = value(widget)
            if key == "name":
                self.table.setRowHeight(row, 88 if widget.combo.currentData() is None else 46)
            self.canvas.load(self.records)
            self.changed.emit()

    def add(self):
        if len(self.records) >= 16:
            return
        self.records.append(new_actor(""))
        self.render()
        self.table.setCurrentCell(len(self.records) - 1, 0)
        self.changed.emit()

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.removed = (row, deepcopy(self.records[row]))
            del self.records[row]
            self.render()
            self.undo_button.show()
            self.changed.emit()

    def restore_removed(self):
        if self.removed and len(self.records) < 16:
            row, record = self.removed
            self.records.insert(row, record)
            self.removed = None
            self.undo_button.hide()
            self.render()
            self.changed.emit()


class BeatsEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.records = []
        self.current = -1
        self.loading = False
        self.removed = None
        self.actor_names = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        self.kind_picker = choices([(caption, kind) for kind, caption in KINDS.items()])
        self.kind_picker.setAccessibleName("New beat type")
        self.kind_picker.setMaximumWidth(165)
        actions.addWidget(self.kind_picker)
        actions.addWidget(button("+ Add beat", lambda: self.add(self.kind_picker.currentData()), "primary"))
        actions.addStretch()
        self.up = button("↑", lambda: self.move(-1))
        self.up.setAccessibleName("Move beat earlier")
        self.down = button("↓", lambda: self.move(1))
        self.down.setAccessibleName("Move beat later")
        for control in (self.up, self.down):
            control.setFixedWidth(30)
            control.setStyleSheet("padding: 8px 4px;")
        self.delete = button("Remove", self.remove, "quiet")
        actions.addWidget(self.up)
        actions.addWidget(self.down)
        actions.addWidget(self.delete)
        root.addLayout(actions)
        split = QSplitter()
        self.split = split
        self.compact = False
        self.list = QListWidget()
        self.list.setMinimumWidth(165)
        self.list.setAccessibleName("Scene beats in play order")
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        split.addWidget(self.list)
        self.details, layout = card()
        self.fields = {
            "kind": choices([(caption, kind) for kind, caption in KINDS.items()]),
            "actor": ActorSelector(cast_only=True),
            "text": prose("What do they say? Use @ for the farmer’s name and $h for a happy portrait.", 100),
            "x": number(-100, 100), "y": number(-100, 100),
            "facing": choices([("Up", 0), ("Right", 1), ("Down", 2), ("Left", 3)]),
            "duration": number(1, 60000), "amount": number(-1000, 1000), "emote": number(0, 100),
        }
        captions = {"kind": "Beat", "actor": "Character", "text": "Their words", "x": "Move X tiles", "y": "Move Y tiles", "facing": "Then face", "duration": "Milliseconds", "amount": "Friendship points", "emote": "Emote number"}
        self.form = add_form(layout, [(captions[key], widget) for key, widget in self.fields.items()])
        self.choice_panel = QWidget()
        choice_layout = QVBoxLayout(self.choice_panel)
        choice_layout.setContentsMargins(0, 0, 0, 0)
        choice_layout.addWidget(label("End the scene with two possible answers. Each answer has its own response and friendship consequence.", "hint", True))
        self.choice_fields = []
        for index in range(2):
            fields = {"label": line("What the player can say", 200), "text": prose("How the character responds", 85), "friendship": number(-1000, 1000)}
            choice_layout.addWidget(label(f"ANSWER {index + 1}", "eyebrow"))
            add_form(choice_layout, [("Player answer", fields["label"]), ("NPC response", fields["text"]), ("Friendship points", fields["friendship"])])
            self.choice_fields.append(fields)
            for widget in fields.values():
                connect_change(widget, self.edit)
        layout.addWidget(self.choice_panel)
        self.help = label("", "hint", True)
        layout.addWidget(self.help)
        split.addWidget(self.details)
        split.setSizes([210, 470])
        split.setStretchFactor(1, 1)
        root.addWidget(split)
        self.undo_button = button("Undo removed beat", self.restore_removed, "quiet")
        self.undo_button.hide()
        root.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.list.currentRowChanged.connect(self.select)
        for widget in self.fields.values():
            connect_change(widget, self.edit)
        self.render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 600
        if compact != self.compact:
            self.compact = compact
            self.split.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
            self.list.setMinimumWidth(0 if compact else 165)
            self.list.setMinimumHeight(130 if compact else 0)
            self.list.setMaximumHeight(180 if compact else 16777215)
            self.split.setSizes([150, 320] if compact else [210, 470])

    def title(self, record, index):
        kind = record.get("kind", "dialogue")
        actor = record.get("actor", "")
        detail = record.get("text", "").replace("\n", " ")[:46] if kind in {"dialogue", "choice"} else self.actor_names.get(actor, actor)
        return f"{index + 1:02d}  {KINDS.get(kind, kind)}\n{detail or 'Write this moment…'}"

    def set_actor_options(self, options):
        self.actor_names = {identity: caption for caption, identity in options}
        self.fields["actor"].set_options(options)
        for index, record in enumerate(self.records):
            self.list.item(index).setText(self.title(record, index))

    def load(self, records):
        self.records = deepcopy(records)
        self.removed = None
        self.undo_button.hide()
        self.render(0)

    def render(self, selected=0):
        self.loading = True
        self.list.clear()
        for index, record in enumerate(self.records):
            self.list.addItem(self.title(record, index))
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
        self.details.setEnabled(enabled)
        self.up.setEnabled(enabled and index > 0)
        self.down.setEnabled(enabled and index < len(self.records) - 1)
        self.delete.setEnabled(enabled)
        record = {**new_beat(), **(self.records[index] if enabled else {})}
        for key, widget in self.fields.items():
            set_value(widget, record[key])
        options = record.get("choices", [])
        for index, fields in enumerate(self.choice_fields):
            option = options[index] if index < len(options) else {}
            for key, widget in fields.items():
                set_value(widget, option.get(key, 0 if key == "friendship" else ""))
        self.loading = False
        self.show_fields()

    def show_fields(self):
        kind = value(self.fields["kind"])
        active = {"kind"} | {"dialogue": {"actor", "text"}, "choice": {"actor", "text"}, "move": {"actor", "x", "y", "facing"}, "pause": {"duration"}, "emote": {"actor", "emote"}, "friendship": {"actor", "amount"}}.get(kind, set())
        self.choice_panel.setVisible(kind == "choice")
        for key, widget in self.fields.items():
            self.form.setRowVisible(widget, key in active)
        self.help.setText({"dialogue": "Farmer lines appear as narration. Use typographic quotes (“ ”) inside dialogue. Rehearsal checks game syntax before export.", "move": "Movement is relative to their current tile. Negative X moves left; negative Y moves up. Check the path in-game.", "pause": "Give a moment room to breathe. 1,000 milliseconds = 1 second.", "emote": "Use a game emote number, such as 20 for a heart. Verify the expression in-game.", "friendship": "Changes the farmer’s friendship with this NPC. 250 points equals one heart; this does not change NPC-to-NPC friendship."}.get(kind, ""))

    def edit(self):
        if self.loading or self.current < 0:
            return
        self.records[self.current].update({key: value(widget) for key, widget in self.fields.items()})
        if value(self.fields["kind"]) == "choice":
            record = self.records[self.current]
            existing = record.get("choices", [])
            record["choices"] = [{**(existing[index] if index < len(existing) else {"id": str(uuid.uuid4())}), **{key: value(widget) for key, widget in fields.items()}} for index, fields in enumerate(self.choice_fields)]
        self.list.item(self.current).setText(self.title(self.records[self.current], self.current))
        self.show_fields()
        self.changed.emit()

    def add(self, kind="dialogue"):
        if len(self.records) >= 200:
            return
        self.records.append(new_beat(kind))
        self.render(len(self.records) - 1)
        self.changed.emit()

    def move(self, delta):
        target = self.current + delta
        if self.current < 0 or not 0 <= target < len(self.records):
            return
        self.records[self.current], self.records[target] = self.records[target], self.records[self.current]
        self.render(target)
        self.changed.emit()

    def remove(self):
        if self.current >= 0:
            index = self.current
            self.removed = (index, deepcopy(self.records[index]))
            del self.records[index]
            self.render(index)
            self.undo_button.show()
            self.changed.emit()

    def restore_removed(self):
        if self.removed and len(self.records) < 200:
            index, record = self.removed
            self.records.insert(index, record)
            self.removed = None
            self.undo_button.hide()
            self.render(index)
            self.changed.emit()


class StoryRecords(QWidget):
    changed = Signal()

    def __init__(self, workshop, kind):
        super().__init__()
        self.workshop = workshop
        self.kind = kind
        self.records = []
        self.current = -1
        self.loading = False
        self.removed = None
        self.fields = {}
        self.story_fields = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(14)
        toolbar = QHBoxLayout()
        if kind == "events":
            self.template = choices([("Blank idea", "blank"), ("First meeting", "first_meeting"), ("A disagreement", "conflict"), ("Making amends", "reconciliation")])
            self.template.setAccessibleName("Story starter")
            toolbar.addWidget(self.template)
            toolbar.addWidget(button("+ New event", lambda: self.add(self.template.currentData()), "primary"))
        else:
            toolbar.addWidget(button("+ New relationship", self.add, "primary"))
        self.duplicate_button = button("Duplicate", self.duplicate)
        toolbar.addWidget(self.duplicate_button)
        toolbar.addStretch()
        self.undo_button = button("Undo remove", self.restore_removed, "quiet")
        self.undo_button.hide()
        toolbar.addWidget(self.undo_button)
        self.remove_button = button("Remove", self.remove, "quiet")
        toolbar.addWidget(self.remove_button)
        root.addLayout(toolbar)
        self.notice = label("", "notice", True)
        self.notice.hide()
        root.addWidget(self.notice)
        splitter = QSplitter()
        rail = QWidget()
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        self.search = line("Find a story…", 100)
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Find story notes")
        rail_layout.addWidget(self.search)
        self.filter = choices([("All stages", "all")] + [(name, stage) for stage, name in STAGES.items() if kind == "events" or stage != "scene"])
        self.filter.setAccessibleName("Filter by story stage")
        rail_layout.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(165)
        self.list.setAccessibleName("Heart events" if kind == "events" else "Relationship arcs")
        rail_layout.addWidget(self.list, 1)
        self.no_matches = label("No matching stories. Try another search or stage.", "muted", True)
        rail_layout.addWidget(self.no_matches)
        splitter.addWidget(rail)
        self.editor = QWidget()
        self.content = QVBoxLayout(self.editor)
        self.content.setContentsMargins(8, 0, 0, 0)
        self.content.setSpacing(14)
        if kind == "relationships":
            self.editor_scroll = QScrollArea()
            self.editor_scroll.setWidgetResizable(True)
            self.editor_scroll.setWidget(self.editor)
            splitter.addWidget(self.editor_scroll)
        else:
            splitter.addWidget(self.editor)
        splitter.setSizes([220, 780])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.empty, layout = card("A small moment can become a whole story.", "Start with something your character wants, something they’re afraid of, or someone who changes them. You can save at any stage.")
        layout.insertStretch(0)
        layout.addWidget(label("01  Capture an idea     02  Find the turning point     03  Build the scene     04  Rehearse & export", "muted", True))
        layout.addStretch()
        root.addWidget(self.empty)
        self.splitter = splitter
        self.list.currentRowChanged.connect(self.select)
        self.search.textChanged.connect(self.apply_filter)
        self.filter.currentIndexChanged.connect(self.apply_filter)

    def title(self, record):
        stage = STAGES.get(record.get("story", {}).get("stage", "idea"), "Idea")
        suffix = f"{record.get('hearts', 0)} hearts · {stage}" if self.kind == "events" else f"{record.get('relation') or 'Connection'} · {stage}"
        return f"{record.get('name') or 'Untitled'}\n{suffix}"

    def load(self, records):
        normalize = normalize_event if self.kind == "events" else normalize_relationship
        self.records = [normalize(record) for record in records]
        self.removed = None
        self.undo_button.hide()
        self.notice.hide()
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.refresh(0)

    def refresh(self, selected=0):
        self.loading = True
        self.list.clear()
        self.list.addItems([self.title(record) for record in self.records])
        self.current = -1
        self.loading = False
        self.empty.setVisible(not self.records)
        self.splitter.setVisible(bool(self.records))
        self.editor.setVisible(bool(self.records))
        if self.records:
            self.list.setCurrentRow(max(0, min(selected, len(self.records) - 1)))
        else:
            self.select(-1)
        self.apply_filter()

    def apply_filter(self):
        query = self.search.text().casefold().strip()
        stage = self.filter.currentData()
        visible = 0
        for index, record in enumerate(self.records):
            item = self.list.item(index)
            if item is None:
                continue
            match = query in " ".join(str(record.get(key, "")) for key in ("name", "description", "relation", "location")).casefold()
            match = match and (stage == "all" or record.get("story", {}).get("stage", "idea") == stage)
            item.setHidden(not match)
            visible += match
        self.no_matches.setVisible(bool(self.records) and not visible)
        if self.records and not self.loading:
            selected = self.list.currentItem()
            if selected is None or selected.isHidden():
                first = next((index for index in range(self.list.count()) if not self.list.item(index).isHidden()), -1)
                self.list.setCurrentRow(first)

    def bind_fields(self):
        for key, widget in {**self.fields, **self.story_fields}.items():
            if not widget.accessibleName():
                widget.setAccessibleName(key.replace("_", " ").capitalize())
            connect_change(widget, self.edit)

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
            record = self.records[index]
            self.refresh_links()
            for key, widget in self.fields.items():
                set_value(widget, record.get(key, ""))
            for key, widget in self.story_fields.items():
                set_value(widget, record["story"].get(key, ""))
            self.load_detail(record)
        self.loading = False
        self.update_preview()

    def refresh_links(self):
        pass

    def load_detail(self, record):
        pass

    def edit(self):
        if self.loading or self.current < 0:
            return
        record = self.records[self.current]
        record.update({key: value(widget) for key, widget in self.fields.items()})
        record["story"].update({key: value(widget) for key, widget in self.story_fields.items()})
        self.capture_detail(record)
        if record["story"]["stage"] == "ready":
            record["story"]["stage"] = "scene" if self.kind == "events" else "outline"
            self.show_notice("You changed a ready story. Review it again before including it in the next export.")
        self.list.item(self.current).setText(self.title(record))
        # Keep the active record pinned while typing. A title edit or automatic
        # Ready → Scene transition must not redirect the next keystroke into a
        # different record when a search/stage filter stops matching this one.
        self.changed.emit()

    def capture_detail(self, record):
        pass

    def dump(self):
        return deepcopy(self.records)

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.show()

    def add(self, template="blank"):
        if len(self.records) >= 100:
            self.show_notice("This project can hold up to 100 entries in each story collection.")
            return
        record = new_event(self.workshop.character(), template=template) if self.kind == "events" else new_relationship()
        self.records.append(record)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.refresh(len(self.records) - 1)
        if self.kind == "events":
            self.phases.setCurrentIndex(0)
        self.changed.emit()
        self.fields["name"].setFocus()
        self.fields["name"].selectAll()

    def duplicate(self):
        if self.current < 0 or len(self.records) >= 100:
            return
        record = deepcopy(self.records[self.current])
        record["id"] = str(uuid.uuid4())
        record["name"] = record["name"][:(93 if self.kind == "events" else 73)] + " (copy)"
        record["story"]["stage"] = "idea"
        if self.kind == "events":
            record["story"]["previous_event_id"] = ""
            record["story"]["relationship_id"] = ""
            for key in ("actors", "beats"):
                for entry in record["story"][key]:
                    entry["id"] = str(uuid.uuid4())
        self.records.append(record)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.refresh(len(self.records) - 1)
        self.changed.emit()

    def remove(self):
        if self.current < 0:
            return
        record = self.records[self.current]
        key = "previous_event_id" if self.kind == "events" else "relationship_id"
        references = [event.get("name") or "Untitled" for event in self.workshop.events.records if event.get("story", {}).get(key) == record["id"]]
        if references:
            self.show_notice("This story is linked to " + ", ".join(references) + ". Change those links before removing it.")
            return
        self.removed = (self.current, deepcopy(record))
        del self.records[self.current]
        self.refresh(self.current)
        self.undo_button.show()
        self.changed.emit()

    def restore_removed(self):
        if self.removed and len(self.records) < 100:
            index, record = self.removed
            self.records.insert(index, record)
            self.removed = None
            self.undo_button.hide()
            self.refresh(index)
            self.changed.emit()


class EventsPage(StoryRecords):
    def __init__(self, workshop):
        super().__init__(workshop, "events")
        heading = QHBoxLayout()
        self.stage_label = label("IDEA", "badge")
        heading.addWidget(self.stage_label)
        heading.addStretch()
        self.next_button = button("Develop the idea →", self.next_step, "primary")
        heading.addWidget(self.next_button)
        self.content.addLayout(heading)
        self.phases = QTabWidget()
        self.phases.setDocumentMode(True)
        self.phases.setAccessibleName("Story development steps")
        self.content.addWidget(self.phases)
        self.fields = {"name": line("A name for this moment", 100), "description": prose("Catch the spark. What happens, and why does it matter to this character?", 150), "hearts": number(0, 14), "location": MapSelector(compact=True)}
        self.story_fields = {"premise": prose("What does your character want when this scene begins?"), "conflict": prose("What gets in the way? Let a habit, a fear, or another person create tension."), "outcome": prose("What changes by the end? What will the player understand or feel?"), "relationship_id": QComboBox(), "previous_event_id": QComboBox(), "season": choices([("Any season", "any")] + [(s.title(), s) for s in ("spring", "summer", "fall", "winter")]), "weather": choices([("Any weather", "any"), ("Sunny", "sunny"), ("Rainy", "rainy")]), "time_start": number(600, 2600), "time_end": number(600, 2600), "music": line("none", 100)}
        self.story_fields.update(relationship=choices([("Any relationship", "any"), ("Unmarried", "unmarried"), ("Dating this character", "dating"), ("Married to this character", "married")]),
                                 min_house_upgrade=choices([("Any farmhouse", 0), ("First upgrade or higher", 1), ("Nursery upgrade or higher", 2), ("Cellar upgrade", 3)]),
                                 repeat=choices([("Once per save", "once"), ("Once each day · needs Event Repeater", "daily")]))
        idea, layout = card("Start with a spark", "A story does not have to be complete to be worth keeping.")
        add_form(layout, [("Event title", self.fields["name"]), ("The idea", self.fields["description"]), ("Part of a relationship", self.story_fields["relationship_id"])])
        layout.addWidget(label("Try this: an ordinary task reveals something unexpected. A lost object, an unfinished letter, a favorite place they no longer visit.", "notice", True))
        layout.addStretch()
        self.add_phase(idea, "1  Idea")
        outline, layout = card("Give the moment a shape", "Desire → tension → change. Keep the player close to what matters.")
        add_form(layout, [("They want…", self.story_fields["premise"]), ("But…", self.story_fields["conflict"]), ("By the end…", self.story_fields["outcome"])])
        layout.addWidget(label("These prompts guide your writing. The scene’s cast and beats determine what plays in-game.", "hint", True))
        layout.addStretch()
        self.add_phase(outline, "2  Outline")
        scene = QWidget()
        layout = QVBoxLayout(scene)
        layout.setContentsMargins(0, 10, 0, 0)
        setup, setup_layout = card("When the moment finds you")
        grid = QGridLayout()
        grid.setSpacing(10)
        controls = [("Heart level", self.fields["hearts"]), ("Map", self.fields["location"]), ("Season", self.story_fields["season"]), ("Weather", self.story_fields["weather"]), ("From (HHMM)", self.story_fields["time_start"]), ("Until (HHMM)", self.story_fields["time_end"])]
        for index, (caption, widget) in enumerate(controls):
            row, col = divmod(index, 2)
            field_layout = QVBoxLayout()
            field_layout.addWidget(label(caption, "muted"))
            field_layout.addWidget(widget)
            widget.setAccessibleName(caption)
            grid.addLayout(field_layout, row, col)
        setup_layout.addLayout(grid)
        add_form(setup_layout, [("After event", self.story_fields["previous_event_id"]), ("Relationship", self.story_fields["relationship"]), ("Farmhouse", self.story_fields["min_house_upgrade"]), ("Repeat", self.story_fields["repeat"]), ("Music track", self.story_fields["music"])])
        setup_layout.addWidget(label("Enter times in ten-minute steps (600 = 6 AM; 1800 = 6 PM). Enter the map while every condition matches. Daily scenes add Event Repeater as a required mod; install it before testing.", "hint", True))
        layout.addWidget(setup)
        cast, cast_layout = card("Set the stage")
        self.actors = CastEditor()
        cast_layout.addWidget(self.actors)
        layout.addWidget(cast)
        beats, beats_layout = card("Build the moment", "Arrange words, movement, expressions, and consequences in play order.")
        self.beats = BeatsEditor()
        beats_layout.addWidget(self.beats)
        layout.addWidget(beats)
        self.add_phase(scene, "3  Scene")
        review = QWidget()
        layout = QVBoxLayout(review)
        layout.setContentsMargins(0, 10, 0, 0)
        rehearsal, rehearsal_layout = card("A first rehearsal", "Step through the scene as a reader. This preview does not run the game engine.")
        self.trigger_summary = label("", "muted", True)
        rehearsal_layout.addWidget(self.trigger_summary)
        self.rehearsal_position = 0
        self.rehearsal_counter = label("", "eyebrow")
        self.rehearsal_text = label("Add a beat to begin.", "profileName", True)
        self.rehearsal_text.setMinimumHeight(90)
        rehearsal_layout.addWidget(self.rehearsal_counter)
        rehearsal_layout.addWidget(self.rehearsal_text)
        self.rehearsal_choice = QComboBox()
        self.rehearsal_choice.setAccessibleName("Preview a player choice outcome")
        self.rehearsal_choice.currentIndexChanged.connect(lambda _: self.rehearse() if self.current >= 0 else None)
        self.rehearsal_choice.hide()
        rehearsal_layout.addWidget(self.rehearsal_choice)
        self.choice_result = label("", "notice", True)
        self.choice_result.hide()
        rehearsal_layout.addWidget(self.choice_result)
        row = QHBoxLayout()
        self.previous_beat = button("← Previous", lambda: self.step_rehearsal(-1))
        self.next_beat = button("Next beat →", lambda: self.step_rehearsal(1))
        row.addWidget(self.previous_beat)
        row.addWidget(self.next_beat)
        row.addStretch()
        row.addWidget(button("Restart", self.restart_rehearsal, "quiet"))
        rehearsal_layout.addLayout(row)
        layout.addWidget(rehearsal)
        checks, checks_layout = card("Ready to bring into the valley?")
        self.readiness = label("", "sectionTitle", True)
        checks_layout.addWidget(self.readiness)
        self.checks = QListWidget()
        self.checks.setWordWrap(True)
        self.checks.setMinimumHeight(120)
        self.checks.setMaximumHeight(230)
        self.checks.setAccessibleName("Scene checks; activate to fix")
        self.checks.itemActivated.connect(lambda item: self.focus_field(item.data(Qt.ItemDataRole.UserRole)))
        checks_layout.addWidget(self.checks)
        row = QHBoxLayout()
        self.ready_button = button("Mark ready for export", self.mark_ready, "primary")
        self.draft_button = button("Keep as draft", self.return_to_draft)
        row.addWidget(self.ready_button)
        row.addWidget(self.draft_button)
        row.addStretch()
        checks_layout.addLayout(row)
        checks_layout.addWidget(label("Ready scenes are included in your next mod ZIP. Check walkable tiles, pacing, and every trigger in Stardew Valley before sharing.", "hint", True))
        layout.addWidget(checks)
        self.show_script = QCheckBox("Show compiled event details")
        layout.addWidget(self.show_script)
        self.script_preview = QPlainTextEdit()
        self.script_preview.setReadOnly(True)
        self.script_preview.setAccessibleName("Compiled event preview")
        self.script_preview.setMinimumHeight(170)
        self.script_preview.hide()
        self.show_script.toggled.connect(self.script_preview.setVisible)
        self.show_script.toggled.connect(self.update_preview)
        layout.addWidget(self.script_preview)
        layout.addStretch()
        self.add_phase(review, "4  Rehearse")
        self.phases.currentChanged.connect(self.phase_changed)
        self.actors.changed.connect(self.edit)
        self.beats.changed.connect(self.edit)
        self.bind_fields()

    def add_phase(self, page, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.phases.addTab(scroll, title)

    def refresh_links(self):
        if not 0 <= self.current < len(self.records):
            return
        record = self.records[self.current]
        for key, records, empty in (("relationship_id", self.workshop.relationships.records, "Standalone event"), ("previous_event_id", self.records, "No earlier event required")):
            widget = self.story_fields[key]
            selected = record["story"].get(key, "")
            blocked = widget.blockSignals(True)
            widget.clear()
            widget.addItem(empty, "")
            for other in records:
                if key != "previous_event_id" or other["id"] != record["id"]:
                    widget.addItem(other.get("name") or "Untitled", other["id"])
            if selected and widget.findData(selected) < 0:
                widget.addItem("Missing linked story — choose a replacement", selected)
            widget.setCurrentIndex(max(0, widget.findData(selected)))
            widget.blockSignals(blocked)

    def load_detail(self, record):
        self.actors.load(record["story"]["actors"])
        self.actors.canvas.load(self.actors.records, {"$npc": self.workshop.character().get("name", "Your character"), "farmer": "Farmer"})
        self.beats.load(record["story"]["beats"])
        self.refresh_actor_choices()
        self.rehearsal_position = 0
        self.phases.setCurrentIndex({"idea": 0, "outline": 1, "scene": 2, "ready": 3}.get(record["story"]["stage"], 0))

    def capture_detail(self, record):
        record["story"]["actors"] = deepcopy(self.actors.records)
        record["story"]["beats"] = deepcopy(self.beats.records)

    def refresh_actor_choices(self):
        names = {identity: caption for caption, identity in self.actors.options}
        options = [(names.get(actor["name"], actor["name"]), actor["name"]) for actor in self.actors.records if actor.get("name")]
        self.beats.set_actor_options(options)

    def phase_changed(self):
        if hasattr(self, "next_button"):
            self.next_button.setText(["Develop the idea →", "Build the scene →", "Rehearse the scene →", "Review && export →"][self.phases.currentIndex()])

    def next_step(self):
        if self.current < 0:
            return
        index = self.phases.currentIndex()
        if index == 3:
            if self.records[self.current]["story"]["stage"] != "ready" and not self.mark_ready():
                return
            self.workshop.window.open_section("export")
            return
        stage = ["outline", "scene", "scene"][index]
        if index == 1:
            story = self.records[self.current]["story"]
            if not story["actors"]:
                story["actors"] = new_event(self.workshop.character())["story"]["actors"]
                self.actors.load(story["actors"])
            if not story["beats"]:
                story["beats"] = [new_beat()]
                self.beats.load(story["beats"])
        if self.records[self.current]["story"]["stage"] != "ready":
            self.records[self.current]["story"]["stage"] = stage
            self.list.item(self.current).setText(self.title(self.records[self.current]))
            self.changed.emit()
        self.phases.setCurrentIndex(index + 1)
        self.update_preview()

    def mark_ready(self):
        if self.current < 0:
            return False
        self.phases.setCurrentIndex(3)
        issues = event_issues(self.records[self.current], self.workshop.character())
        if any(issue["level"] == "error" for issue in issues):
            self.show_notice("A few scene details still need attention. Activate a check below to go straight to it.")
            self.update_preview()
            return False
        self.records[self.current]["story"]["stage"] = "ready"
        self.notice.hide()
        self.list.item(self.current).setText(self.title(self.records[self.current]))
        self.changed.emit()
        self.update_preview()
        return True

    def return_to_draft(self):
        if self.current >= 0:
            self.records[self.current]["story"]["stage"] = "scene"
            self.list.item(self.current).setText(self.title(self.records[self.current]))
            self.changed.emit()
            self.update_preview()

    def update_preview(self):
        if self.current < 0 or not hasattr(self, "checks"):
            return
        record = self.records[self.current]
        story = record["story"]
        character = self.workshop.character()
        world = self.workshop.window.document.get("world", {})
        source = next((location.get("map") for location in world.get("locations", []) if location.get("internal_name") == record.get("location")), None)
        project_file = self.workshop.window.project_file
        if source and project_file:
            from pixelheart_core.world import asset_path
            try:
                self.actors.canvas.set_map(asset_path(source, project_file.parent))
            except ValueError:
                self.actors.canvas.set_map()
        else:
            self.actors.canvas.set_map()
        self.stage_label.setText(STAGES[story["stage" ]].upper() + (" · INCLUDED IN EXPORT" if story["stage"] == "ready" else " · SAVED AS A DRAFT"))
        self.trigger_summary.setText(f"{record.get('location') or 'Choose a map'} · {record.get('hearts', 0)} hearts · {story['time_start']:04d}–{story['time_end']:04d} · {story['season'].title()} season · {story['weather'].title()} weather · {story.get('relationship', 'any').title()} relationship · {story.get('repeat', 'once').title()}")
        self.checks.clear()
        issues = event_issues(record, character)
        for issue in issues:
            item = QListWidgetItem(("FIX  ·  " if issue["level"] == "error" else "REVIEW  ·  ") + issue["message"])
            item.setData(Qt.ItemDataRole.UserRole, issue["field"])
            self.checks.addItem(item)
        errors = sum(issue["level"] == "error" for issue in issues)
        self.readiness.setText(f"{errors} {'detail needs' if errors == 1 else 'details need'} attention" if errors else "The scene passes its structural checks.")
        self.checks.setVisible(bool(issues))
        self.ready_button.setEnabled(not errors and story["stage"] != "ready")
        self.draft_button.setEnabled(story["stage"] == "ready")
        self.rehearse()
        if not self.show_script.isChecked():
            return
        if errors:
            self.script_preview.setPlainText("Resolve the scene checks to preview the compiled event.")
        else:
            candidate = deepcopy(character)
            # Compile this scene plus its ancestors without unrelated draft blockers.
            wanted = {record["id"]}
            current = record
            while current["story"].get("previous_event_id"):
                previous = next((event for event in candidate["events"] if event["id"] == current["story"]["previous_event_id"]), None)
                if previous is None or previous["id"] in wanted:
                    break
                wanted.add(previous["id"])
                current = previous
            candidate["events"] = [event for event in candidate["events"] if event["id"] in wanted]
            for event in candidate["events"]:
                event["story"]["stage"] = "ready"
            for relationship in candidate["relationships"]:
                relationship["story"]["stage"] = "idea"
            try:
                from pixelheart_core.world import world_character
                candidate = world_character(candidate, self.workshop.window.document.get("world", {}))
                patches = compile_story(candidate)
                identity = event_game_id(record, character)
                matches = [{**patch, "Entries": {key: script for key, script in patch["Entries"].items() if key.split("/")[0] == identity or key.startswith(identity + "_choice_")}} for patch in patches if "Entries" in patch]
                self.script_preview.setPlainText(json.dumps([patch for patch in matches if patch["Entries"]], ensure_ascii=False, indent=2))
            except ValueError as exc:
                self.script_preview.setPlainText(str(exc))

    def rehearse(self):
        record = self.records[self.current]
        beats = record["story"]["beats"]
        self.rehearsal_position = max(0, min(self.rehearsal_position, len(beats) - 1))
        index = self.rehearsal_position
        self.previous_beat.setEnabled(bool(beats) and index > 0)
        self.next_beat.setEnabled(bool(beats) and index < len(beats) - 1)
        self.rehearsal_choice.hide()
        self.choice_result.hide()
        if not beats:
            self.rehearsal_counter.setText("YOUR SCENE STARTS HERE")
            self.rehearsal_text.setText("Add a dialogue or action beat in Scene to hear the story take shape.")
            return
        beat = beats[index]
        who = next((caption for caption, identity in self.actors.options if identity == beat.get("actor", "$npc")), actor_caption(beat.get("actor", "$npc"), self.workshop.character()))
        kind = beat["kind"]
        self.rehearsal_counter.setText(f"BEAT {index + 1} OF {len(beats)}  ·  {KINDS[kind].upper()}")
        if kind == "dialogue":
            text = beat.get("text", "").replace("#$b#", "\n").replace("@", "Farmer")
            text = re.sub(r"\$(?:[hslanu]|\d+)\b", "", text)
            text = f"{who}\n\n{text or 'Their words are waiting for you.'}"
        elif kind == "choice":
            options = beat.get("choices", [])
            labels = [option.get("label", "") or f"Answer {index + 1}" for index, option in enumerate(options)]
            if [self.rehearsal_choice.itemText(i) for i in range(self.rehearsal_choice.count())] != labels:
                self.rehearsal_choice.blockSignals(True)
                self.rehearsal_choice.clear()
                self.rehearsal_choice.addItems(labels)
                self.rehearsal_choice.blockSignals(False)
            text = f"{who}\n\n{beat.get('text', '')}\n\nChoose an answer to rehearse its consequence."
            self.rehearsal_choice.show()
            option_index = self.rehearsal_choice.currentIndex()
            if 0 <= option_index < len(options):
                option = options[option_index]
                self.choice_result.setText(f"{who}: {option.get('text', '')}\n\nFriendship: {option.get('friendship', 0):+d} points. This answer ends the scene.")
                self.choice_result.show()
        elif kind == "move":
            text = f"{who} moves {beat['x']} tiles horizontally and {beat['y']} vertically, then faces {['up', 'right', 'down', 'left'][beat['facing']]}."
        elif kind == "pause":
            text = f"A pause.\n\n{beat['duration'] / 1000:g} seconds to let the moment land."
        elif kind == "emote":
            text = f"{who} shows emote {beat['emote']}."
        else:
            text = f"The farmer’s friendship with {who} changes by {beat['amount']:+d} points."
        self.rehearsal_text.setText(text)

    def step_rehearsal(self, delta):
        if self.current >= 0:
            self.rehearsal_position += delta
            self.rehearse()

    def restart_rehearsal(self):
        self.rehearsal_position = 0
        if self.current >= 0:
            self.rehearse()

    def focus_field(self, field):
        parts = field.split(".")
        if parts[0] == "events" and len(parts) > 2:
            parts = parts[2:]
        key = parts[-1]
        if "actors" in parts:
            self.phases.setCurrentIndex(2)
            offset = parts.index("actors") + 1
            if len(parts) > offset and parts[offset].isdigit():
                row = int(parts[offset])
                col = {"name": 0, "x": 1, "y": 2, "facing": 3}.get(key, 0)
                self.actors.table.setCurrentCell(row, col)
                widget = self.actors.table.cellWidget(row, col)
                if widget:
                    widget.setFocus()
                    self.phases.currentWidget().ensureWidgetVisible(widget)
            else:
                self.phases.currentWidget().ensureWidgetVisible(self.actors.table)
        elif "beats" in parts:
            self.phases.setCurrentIndex(2)
            offset = parts.index("beats") + 1
            if len(parts) > offset and parts[offset].isdigit():
                self.beats.list.setCurrentRow(int(parts[offset]))
            if "choices" in parts:
                option_offset = parts.index("choices") + 1
                option = int(parts[option_offset]) if len(parts) > option_offset and parts[option_offset].isdigit() else 0
                widget = self.beats.choice_fields[min(1, option)].get(key, self.beats.choice_panel)
                widget.setFocus()
                self.phases.currentWidget().ensureWidgetVisible(widget)
            elif key in self.beats.fields:
                self.beats.fields[key].setFocus()
                self.phases.currentWidget().ensureWidgetVisible(self.beats.fields[key])
            else:
                self.phases.currentWidget().ensureWidgetVisible(self.beats)
        elif key in self.fields or key in self.story_fields:
            self.phases.setCurrentIndex(0 if key in {"name", "description", "relationship_id"} else 1 if key in {"premise", "conflict", "outcome"} else 2)
            ({**self.fields, **self.story_fields})[key].setFocus()
            self.phases.currentWidget().ensureWidgetVisible(({**self.fields, **self.story_fields})[key])
        else:
            self.phases.setCurrentIndex(2)


class RelationshipsPage(StoryRecords):
    def __init__(self, workshop):
        super().__init__(workshop, "relationships")
        self.fields = {"name": line("Who matters to them?", 80), "relation": line("Friend, neighbor, rival, love interest…", 80), "description": prose("How did they meet? What do they mean to one another?", 110)}
        self.story_fields = {"target": ActorSelector("farmer"), "desire": prose("What do they want from this connection?", 70), "tension": prose("What keeps them apart?", 70), "progression": prose("How do they begin to see each other differently?", 70), "resolution": prose("What does trust look like at the end?", 70)}
        self.story_fields["target"].combo.setAccessibleName("Other person in this character's relationship")
        self.stage_label = label("IDEA", "badge")
        self.content.addWidget(self.stage_label, 0, Qt.AlignmentFlag.AlignLeft)
        basics, layout = card("Someone who changes them", "Build a friendship, a rivalry, or a romance through moments the player can experience.")
        add_form(layout, [("Connection name", self.fields["name"]), ("Their relationship", self.fields["relation"]), ("Other character", self.story_fields["target"]), ("The backstory", self.fields["description"])])
        layout.addWidget(label("Every arc follows your character's connection with this person. Bonds with villagers are told through scenes; friendship points affect the farmer’s relationship with a character.", "hint", True))
        self.content.addWidget(basics)
        arc, layout = card("Make room for change")
        add_form(layout, [("A beginning", self.story_fields["desire"]), ("A complication", self.story_fields["tension"]), ("A turning point", self.story_fields["progression"]), ("A new connection", self.story_fields["resolution"])])
        self.content.addWidget(arc)
        milestones, layout = card("Turn the arc into playable moments", "Create four editable event drafts at 2, 4, 6, and 8 hearts. Each waits for the previous moment, so the story unfolds in order.")
        self.create_arc_button = button("Create four milestone events", self.create_arc, "primary")
        layout.addWidget(self.create_arc_button)
        self.linked_events = QListWidget()
        self.linked_events.setWordWrap(True)
        self.linked_events.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.linked_events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.linked_events.setMinimumHeight(160)
        self.linked_events.setMaximumHeight(250)
        self.linked_events.setAccessibleName("Relationship milestones; activate to edit event")
        self.linked_events.itemActivated.connect(self.open_event)
        layout.addWidget(self.linked_events)
        self.arc_status = label("", "muted", True)
        layout.addWidget(self.arc_status)
        self.ready_button = button("Mark relationship ready", self.mark_ready)
        self.draft_button = button("Keep as draft", self.return_to_draft)
        actions = QHBoxLayout()
        actions.addWidget(self.ready_button)
        actions.addWidget(self.draft_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.content.addWidget(milestones)
        self.content.addStretch()
        self.bind_fields()

    def create_arc(self):
        if self.current < 0:
            return
        relationship = self.records[self.current]
        if any(event["story"].get("relationship_id") == relationship["id"] for event in self.workshop.events.records):
            self.show_notice("This relationship already has linked events. Open a milestone below to develop it, or link another event in its Idea step.")
            return
        if len(self.workshop.events.records) > 96:
            self.show_notice("Make room for four events first. A project can hold up to 100 events.")
            return
        events = relationship_events(relationship, self.workshop.character())
        self.workshop.events.records.extend(events)
        relationship["story"]["stage"] = "outline"
        self.workshop.events.refresh(len(self.workshop.events.records) - len(events))
        self.list.item(self.current).setText(self.title(relationship))
        self.show_notice("Your four milestones are saved as drafts. Open the first one below, shape its scene, and rehearse it. Mark each event ready in story order.")
        self.changed.emit()
        self.update_preview()

    def update_preview(self):
        if self.current < 0 or not hasattr(self, "linked_events"):
            return
        relationship = self.records[self.current]
        linked = [event for event in self.workshop.events.records if event["story"].get("relationship_id") == relationship["id"]]
        self.linked_events.clear()
        for event in linked:
            item = QListWidgetItem(self.workshop.events.title(event))
            item.setData(Qt.ItemDataRole.UserRole, event["id"])
            item.setToolTip("Open this event in the story workshop")
            self.linked_events.addItem(item)
        ready = sum(event["story"]["stage"] == "ready" for event in linked)
        self.stage_label.setText(STAGES[relationship["story"]["stage"]].upper() + " · RELATIONSHIP ARC")
        self.create_arc_button.setEnabled(not linked)
        self.linked_events.setVisible(bool(linked))
        self.arc_status.setText(f"{ready} of {len(linked)} moments ready for export. Activate a milestone to continue writing." if linked else "Your arc is still a note. Give it a sequence of scenes to bring it into the game.")
        self.ready_button.setEnabled(bool(linked) and ready == len(linked) and relationship["story"]["stage"] != "ready")
        self.draft_button.setEnabled(relationship["story"]["stage"] == "ready")

    def return_to_draft(self):
        if self.current >= 0:
            self.records[self.current]["story"]["stage"] = "outline"
            self.list.item(self.current).setText(self.title(self.records[self.current]))
            self.changed.emit()

    def mark_ready(self):
        if self.current < 0:
            return False
        character = self.workshop.character()
        character["relationships"][self.current]["story"]["stage"] = "ready"
        issues = [issue for issue in story_issues(character) if issue["level"] == "error" and issue["field"].startswith(f"relationships.{self.current}")]
        if issues:
            self.show_notice(" ".join(issue["message"] for issue in issues))
            return False
        self.records[self.current]["story"]["stage"] = "ready"
        self.list.item(self.current).setText(self.title(self.records[self.current]))
        self.changed.emit()
        self.update_preview()
        return True

    def open_event(self, item):
        identity = item.data(Qt.ItemDataRole.UserRole)
        for index, event in enumerate(self.workshop.events.records):
            if event["id"] == identity:
                self.workshop.tabs.setCurrentIndex(0)
                self.workshop.events.search.clear()
                self.workshop.events.filter.setCurrentIndex(0)
                self.workshop.events.list.setCurrentRow(index)
                break


class StoryPage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.summary = label("0 ideas · 0 in development · 0 ready", "badge")
        self.summary.setAccessibleName("Event progress")
        self.tabs.setCornerWidget(self.summary, Qt.Corner.TopRightCorner)
        self.events = EventsPage(self)
        self.relationships = RelationshipsPage(self)
        self.tabs.addTab(self.events, "Heart events")
        self.tabs.addTab(self.relationships, "Relationship arcs")
        root.addWidget(self.tabs, 1)
        self.events.changed.connect(self.on_change)
        self.relationships.changed.connect(self.on_change)

    def character(self):
        character = deepcopy(self.window.document["character"])
        character.update(self.window.identity.dump())
        if hasattr(self, "events"):
            character["events"] = self.events.dump()
        if hasattr(self, "relationships"):
            character["relationships"] = self.relationships.dump()
        return character

    def load(self, character):
        self.loading = True
        self.relationships.load(character.get("relationships", []))
        self.events.load(character.get("events", []))
        self.tabs.setCurrentIndex(0)
        self.loading = False
        self.refresh_context()

    def on_change(self):
        if not self.loading:
            for index, relationship in enumerate(self.relationships.records):
                if relationship["story"]["stage"] != "ready":
                    continue
                linked = [event for event in self.events.records if event["story"].get("relationship_id") == relationship["id"]]
                if not linked or any(event["story"]["stage"] != "ready" for event in linked):
                    relationship["story"]["stage"] = "outline"
                    self.relationships.list.item(index).setText(self.relationships.title(relationship))
                    self.relationships.show_notice("A milestone is back in development. Review the linked scenes, then mark the relationship ready again.")
            self.refresh_context()
            self.changed.emit()

    def refresh_context(self):
        events = self.events.records
        counts = {stage: sum(event["story"]["stage"] == stage for event in events) for stage in STAGES}
        self.summary.setText(f"{counts['idea']} ideas · {counts['outline'] + counts['scene']} in development · {counts['ready']} ready")
        self.tabs.setTabText(0, f"Heart events  ({len(events)})")
        self.tabs.setTabText(1, f"Relationship arcs  ({len(self.relationships.records)})")
        self.events.refresh_links()
        from pixelheart_core.world import cast_actor_id
        from pixelheart_core.story import exported_npc_id
        character = self.window.document["character"]
        options = [(character.get("name") or "Your character", "$npc"), ("Farmer", "farmer")]
        for companion in self.window.document.get("world", {}).get("characters", []):
            npc = companion["character"]
            caption = (npc.get("name") or "Supporting character") + " · your cast"
            options.append((caption, cast_actor_id(companion)))
            # Preserve existing authored aliases without exposing raw IDs.
            for identity in (npc.get("internal_name"), exported_npc_id(npc)):
                if any(actor.get("name") == identity for event in events for actor in event["story"]["actors"]):
                    options.append((caption, identity))
        options.extend((name, name) for name in vanilla_actors())
        self.events.actors.set_options(options)
        self.events.refresh_actor_choices()
        self.relationships.story_fields["target"].set_options([(caption, identity) for caption, identity in options if identity != "$npc"])
        self.events.update_preview()
        self.relationships.update_preview()

    def open_issue(self, field):
        parts = field.split(".")
        relationships = parts[0] == "relationships"
        page = self.relationships if relationships else self.events
        self.tabs.setCurrentIndex(1 if relationships else 0)
        page.search.clear()
        page.filter.setCurrentIndex(0)
        if len(parts) > 1 and parts[1].isdigit():
            page.list.setCurrentRow(int(parts[1]))
        if not relationships and len(parts) > 2:
            page.focus_field(field)
        elif relationships:
            widget = {**page.fields, **page.story_fields}.get(parts[-1])
            if widget:
                widget.setFocus()
                page.editor_scroll.ensureWidgetVisible(widget)
            else:
                page.editor_scroll.ensureWidgetVisible(page.linked_events)
