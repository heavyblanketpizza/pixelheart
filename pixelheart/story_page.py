"""A guided, offline workshop for authored events and relationship arcs."""
from __future__ import annotations

from copy import deepcopy
import json
import uuid

from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QPixmap
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QTabWidget,
    QListWidget, QListWidgetItem, QSplitter, QPlainTextEdit, QComboBox,
    QTableWidget, QHeaderView, QCheckBox, QAbstractItemView, QScrollArea, QDialog, QFileDialog, QMenu, QSizePolicy,
)

from pixelheart_core.story import (
    new_event, new_relationship, normalize_event, normalize_relationship,
    new_actor, new_beat, event_issues, story_issues, compile_story,
    event_game_id,
)
from pixelheart_core.story_planning import (relationship_references, related_events,
                                          event_removal_issues, remove_event)
from .editors import line, number, value, set_value, connect_change
from .widgets import label, button, card
from .location_picker import MapSelector
from .stage_canvas import StageCanvas, image_from_pixels as _preview_image
from .actor_picker import ActorSelector, vanilla_actors
from .storyline_page import StorylinePage
from .story_aftermath import EventAftermath
from .schedule_time import EventTime
from .story_icons import story_icon
from .dialogue_tools import EmotionBar


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
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    for caption, widget in fields:
        widget.setAccessibleName(caption)
        form.addRow(caption, widget)
    layout.addLayout(form)
    return form


class TriggerFields(QWidget):
    """Keep condition controls readable in the split editor and wide windows."""

    def __init__(self, controls):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.cells = []
        self.columns = 0
        for caption, control in controls:
            cell = QWidget()
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(label(caption, "muted"))
            layout.addWidget(control)
            control.setAccessibleName(caption)
            self.cells.append(cell)
        self.arrange(1)

    def arrange(self, columns):
        if self.columns == columns:
            return
        self.columns = columns
        for index, cell in enumerate(self.cells):
            self.grid.removeWidget(cell)
            self.grid.addWidget(cell, index // columns, index % columns)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange(2 if self.width() >= 580 else 1)


def actor_caption(name, character):
    return character.get("name", "Your character") if name == "$npc" else "Farmer" if name == "farmer" else name


class CastEditor(QWidget):
    changed = Signal()
    previewChanged = Signal()
    previewVisibilityChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.records = []
        self.loading = False
        self.project_history = None
        self.removed = None
        self.options = [("Your character", "$npc"), ("Farmer", "farmer"), *[(name, name) for name in vanilla_actors()]]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        preview_heading = QHBoxLayout()
        self.location_label = label("Pelican Town", "sceneLocation")
        self.location_label.setMinimumWidth(0)
        self.location_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        preview_heading.addWidget(self.location_label, 1)
        self.preview_toggle = button("Hide scene", self.toggle_preview, "quiet")
        preview_heading.addWidget(self.preview_toggle)
        layout.addLayout(preview_heading)
        self.preview_panel = QWidget()
        preview_layout = QVBoxLayout(self.preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        self.farmer = choices([("Woman farmer", "female"), ("Man farmer", "male")])
        self.farmer.setAccessibleName("Farmer gender for scene and dialogue preview")
        self.farmer.setToolTip("Preview the player's appearance and gendered dialogue. This does not change your NPC or restrict who can see the event.")
        preview_heading.insertWidget(1, self.farmer)
        self.grid_toggle = QCheckBox("Tile grid", self)
        self.grid_toggle.hide()
        self.preview_location_panel = QWidget()
        preview_location_layout = QFormLayout(self.preview_location_panel)
        preview_location_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_location = MapSelector(compact=True)
        self.preview_location.setAccessibleName("Preview location when no event location is chosen")
        preview_location_layout.addRow("Preview location", self.preview_location)
        self.preview_location_panel.hide()
        preview_layout.addWidget(self.preview_location_panel)
        self.canvas = StageCanvas()
        self.canvas.set_header_visible(False)
        self.canvas.setMinimumHeight(180)
        self.expand_button = button("Expand", self.open_staging, "quiet")
        preview_heading.insertWidget(2, self.expand_button)
        view = button("View", lambda: None, "quiet")
        menu = QMenu(view)
        menu.addAction("Expand scene", self.open_staging)
        menu.addSeparator()
        menu.addAction("Fit cast", self.canvas.fit)
        menu.addAction("Fit map", self.canvas.fit_map)
        menu.addSeparator()
        menu.addAction("Zoom in", lambda: self.canvas.zoom_by(1.25))
        menu.addAction("Zoom out", lambda: self.canvas.zoom_by(0.8))
        menu.addSeparator()
        grid_action = menu.addAction("Show tile grid")
        grid_action.setCheckable(True)
        grid_action.toggled.connect(self.grid_toggle.setChecked)
        self.grid_toggle.toggled.connect(grid_action.setChecked)
        menu.addSeparator()
        menu.addAction("Find Stardew Valley…", self.choose_game_artwork)
        view.setMenu(menu)
        preview_heading.insertWidget(3, view)
        preview_layout.addWidget(self.canvas, 1)
        self.source_note = label("", "hint", True)
        self.source_note.setMaximumHeight(50)
        preview_layout.addWidget(self.source_note)
        self.game_artwork = button("Find Stardew Valley…", self.choose_game_artwork, "quiet")
        preview_layout.addWidget(self.game_artwork, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.preview_panel, 1)
        self.grid_toggle.toggled.connect(self.canvas.set_grid_visible)
        self.farmer.currentIndexChanged.connect(self.previewChanged)
        self.preview_location.changed.connect(self.previewChanged)
        self.details_toggle = QCheckBox("Cast & coordinates", self)
        self.details_toggle.setToolTip("Add or remove people, edit exact tile coordinates, and change the direction they face.")
        self.details_toggle.hide()
        self.details_panel = QWidget(self)
        details = QVBoxLayout(self.details_panel)
        details.setContentsMargins(0, 0, 0, 0)
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
        details.addWidget(self.table)
        actions = QHBoxLayout()
        actions.addWidget(button("+ Add character", self.add))
        actions.addWidget(button("Remove character", self.remove, "quiet"))
        self.undo_button = button("Undo remove", self.restore_removed, "quiet")
        self.undo_button.hide()
        actions.addWidget(self.undo_button)
        actions.addStretch()
        details.addLayout(actions)
        self.details_panel.hide()
        self.details_toggle.toggled.connect(self.details_panel.setVisible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "expand_button"):
            self.expand_button.setVisible(self.width() >= 520)

    def toggle_preview(self):
        visible = self.preview_panel.isHidden()
        self.preview_panel.setVisible(visible)
        self.preview_toggle.setText("Hide scene" if visible else "Show scene")
        self.previewVisibilityChanged.emit(visible)

    def choose_game_artwork(self):
        from . import game_connection as connection
        parent = self.window()
        if connection.open_find_game(parent if parent is not self else None):
            self.previewChanged.emit()

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
        canvas.setMinimumHeight(350)
        canvas.load(self.records, self.canvas.names, fit=True)
        canvas.copy_preview_from(self.canvas)
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
            canvas.copy_preview_from(self.canvas)
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
        self.details_toggle.setChecked(True)
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

    def __init__(self, *, inspector=False):
        super().__init__()
        self.inspector_mode = inspector
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
        if inspector:
            self.kind_picker.setParent(self)
            self.kind_picker.hide()
            actions.addWidget(label("SCENE SEQUENCE", "eyebrow"), 1)
            add_beat = button("+ Add", lambda: None, "primary")
            add_menu = QMenu(add_beat)
            for kind, caption in KINDS.items():
                add_menu.addAction(caption, lambda _checked=False, kind=kind: self.add(kind))
            add_beat.setMenu(add_menu)
            actions.addWidget(add_beat)
        else:
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
        if not inspector:
            actions.addWidget(self.up)
            actions.addWidget(self.down)
            actions.addWidget(self.delete)
        root.addLayout(actions)
        self.empty_note = label("Add the first line, action, or choice to begin this scene.", "muted", True)
        root.addWidget(self.empty_note)
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
        if inspector:
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)
        self.fields = {
            "kind": choices([(caption, kind) for kind, caption in KINDS.items()]),
            "actor": ActorSelector(cast_only=True),
            "text": prose("What do they say?", 100),
            "x": number(-100, 100), "y": number(-100, 100),
            "facing": choices([("Up", 0), ("Right", 1), ("Down", 2), ("Left", 3)]),
            "duration": number(1, 60000), "amount": number(-1000, 1000), "emote": number(0, 100),
        }
        captions = {"kind": "Beat", "actor": "Character", "text": "Their words", "x": "Move X tiles", "y": "Move Y tiles", "facing": "Then face", "duration": "Milliseconds", "amount": "Friendship points", "emote": "Emote number"}
        self.form = add_form(layout, [(captions[key], widget) for key, widget in self.fields.items()])
        # Feelings sit right above the words they change; the narrow inspector
        # keeps its compact menu in the footer so the words stay in view.
        self.emotion_bar = EmotionBar(self.fields["text"], compact=inspector)
        if not inspector:
            text_row, _ = self.form.getWidgetPosition(self.fields["text"])
            self.form.insertRow(text_row, "Feeling", self.emotion_bar)
        if inspector:
            self.fields["text"].setMinimumHeight(85)
            self.form.setSpacing(8)
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
        self.help.setVisible(not inspector)
        layout.addWidget(self.help)
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setWidget(self.details)
        split.addWidget(self.details_scroll)
        split.setSizes([210, 470])
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        if inspector:
            footer = QHBoxLayout()
            footer.addWidget(self.up)
            footer.addWidget(self.down)
            footer.addStretch()
            change_type = button("Type", lambda: None, "quiet")
            change_type.setAccessibleName("Change selected beat type")
            type_menu = QMenu(change_type)
            for kind, caption in KINDS.items():
                type_menu.addAction(caption, lambda _checked=False, kind=kind: self.fields["kind"].setCurrentIndex(self.fields["kind"].findData(kind)))
            change_type.setMenu(type_menu)
            footer.addWidget(self.emotion_bar)
            footer.addWidget(change_type)
            footer.addWidget(self.delete)
            root.addLayout(footer)
        self.undo_button = button("Undo removed beat", self.restore_removed, "quiet")
        self.undo_button.hide()
        root.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.list.currentRowChanged.connect(self.select)
        for widget in self.fields.values():
            connect_change(widget, self.edit)
        self.render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < (470 if self.inspector_mode else 600)
        if self.inspector_mode:
            self.fields["text"].setMaximumHeight(90 if self.height() < 350 else 135)
        if self.inspector_mode and compact:
            self.list.setMaximumHeight(max(60, min(150, self.height() // 5)))
        if compact != self.compact:
            self.compact = compact
            self.split.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
            self.list.setMinimumWidth(0 if compact else 140 if self.inspector_mode else 165)
            self.list.setMinimumHeight(60 if self.inspector_mode else 130 if compact else 0)
            self.list.setMaximumHeight(max(60, min(150, self.height() // 5)) if self.inspector_mode and compact else 180 if compact else 16777215)
            self.split.setSizes([150, 320] if compact else [210, 470])
        if self.inspector_mode and compact:
            list_height = max(60, min(150, self.height() // 5))
            self.split.setSizes([list_height, max(180, self.height() - list_height)])

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
        self.empty_note.setVisible(not enabled)
        self.split.setVisible(enabled)
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
            self.form.setRowVisible(widget, key in active and not (self.inspector_mode and key == "kind"))
        if self.inspector_mode:
            self.emotion_bar.setVisible("text" in active)
        else:
            self.form.setRowVisible(self.emotion_bar, "text" in active)
        self.help.setText({"dialogue": "Farmer lines appear as narration. Use typographic quotes (“ ”) inside dialogue. Rehearsal checks game syntax before export.", "move": "Movement is relative to their current tile. Negative X moves left; negative Y moves up. Check the path in-game.", "pause": "Give a moment room to breathe. 1,000 milliseconds = 1 second.", "emote": "Use a game emote number, such as 20 for a heart. Verify the expression in-game.", "friendship": "Changes the farmer’s friendship with this NPC. 250 points equals one heart; this does not change NPC-to-NPC friendship."}.get(kind, ""))

        self.details.setToolTip(self.help.text())

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

    def __init__(self, workshop, kind, *, compact=False):
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
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(8)
        if compact:
            self.build_compact_collection(root)
            return
        self.template = choices([
            ("Blank idea", "blank"), ("First meeting", "first_meeting"),
            ("Shared activity", "shared_activity"), ("A private side", "private_side"),
            ("A boundary", "boundary"), ("Help with a cost", "help_with_cost"),
            ("A disagreement", "conflict"), ("Making amends", "reconciliation"),
            ("A public step", "public_step"), ("A remembered preference", "remembered_preference"),
            ("Mutual invitation", "mutual_invitation"), ("A life together", "life_together"),
        ])
        self.template.setParent(self)
        self.template.setAccessibleName("Story starter")
        self.template.hide()
        self.duplicate_button = button("Duplicate", self.duplicate)
        self.remove_button = button("Remove", self.remove, "quiet")
        self.undo_button = button("Undo remove", self.restore_removed, "quiet")
        for control in (self.duplicate_button, self.remove_button, self.undo_button):
            control.setParent(self)
            control.hide()
        self.notice = label("", "notice", True)
        self.notice.hide()
        root.addWidget(self.notice)
        self.reference_issues = QListWidget()
        self.reference_issues.setAccessibleName("Links to resolve before removing this story")
        self.reference_issues.setMaximumHeight(120)
        self.reference_issues.setWordWrap(True)
        self.reference_issues.hide()
        self.reference_issues.itemActivated.connect(self.open_reference_issue)
        root.addWidget(self.reference_issues)
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        rail = QWidget()
        self.rail = rail
        rail.setMinimumWidth(170)
        rail.setMaximumWidth(270)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(7)
        rail_heading = QHBoxLayout()
        rail_heading.addWidget(label("EVENTS" if kind == "events" else "RELATIONSHIPS", "eyebrow"), 1)
        new_button = button("+ New", lambda: None, "primary")
        new_button.setFixedWidth(86)
        new_button.setAccessibleName("New event" if kind == "events" else "New relationship arc")
        if kind == "events":
            new_menu = QMenu(new_button)
            self.populate_new_menu(new_menu)
            new_button.setMenu(new_menu)
        else:
            new_button.clicked.connect(self.add)
        rail_heading.addWidget(new_button)
        rail_layout.addLayout(rail_heading)
        self.search = line("Find a story…", 100)
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Find story notes")
        rail_layout.addWidget(self.search)
        self.filter = choices([("All stages", "all")] + [(name, stage) for stage, name in STAGES.items() if kind == "events" or stage != "scene"])
        self.filter.setAccessibleName("Filter by story stage")
        self.filter.setParent(self)
        self.filter.hide()
        self.filter_button = button("All stages", lambda: None, "quiet")
        filter_menu = QMenu(self.filter_button)
        for index in range(self.filter.count()):
            filter_menu.addAction(self.filter.itemText(index), lambda _checked=False, index=index: self.filter.setCurrentIndex(index))
        self.filter_button.setMenu(filter_menu)
        rail_layout.addWidget(self.filter_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(165)
        self.list.setAccessibleName("Story events" if kind == "events" else "Relationship arcs")
        rail_layout.addWidget(self.list, 1)
        self.no_matches = label("No matching stories. Try another search or stage.", "muted", True)
        rail_layout.addWidget(self.no_matches)
        rail_layout.addWidget(self.undo_button)
        splitter.addWidget(rail)
        self.editor = QWidget()
        self.content = QVBoxLayout(self.editor)
        self.content.setContentsMargins(4, 0, 0, 0)
        self.content.setSpacing(8)
        self.record_menu_button = button("•••", lambda: None, "quiet")
        self.record_menu_button.setAccessibleName("Event actions" if kind == "events" else "Relationship actions")
        self.record_menu_button.setFixedWidth(38)
        self.record_menu = QMenu(self.record_menu_button)
        self.duplicate_action = self.record_menu.addAction("Duplicate", self.duplicate)
        self.remove_action = self.record_menu.addAction("Remove event" if kind == "events" else "Remove relationship", self.remove)
        self.record_menu_button.setMenu(self.record_menu)
        if kind == "relationships":
            self.content.addWidget(self.record_menu_button, 0, Qt.AlignmentFlag.AlignRight)
            self.editor_scroll = QScrollArea()
            self.editor_scroll.setWidgetResizable(True)
            self.editor_scroll.setWidget(self.editor)
            splitter.addWidget(self.editor_scroll)
        else:
            splitter.addWidget(self.editor)
        splitter.setSizes([195, 1000])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.empty, layout = card("A small moment can become a whole story.", "Start with something your character wants, something they’re afraid of, or someone who changes them. You can save at any stage.")
        layout.insertStretch(0)
        layout.addWidget(label("Find its purpose · Choose when it happens · Build the scene · Follow through · Rehearse", "muted", True))
        empty_create = button("Create an event" if kind == "events" else "Create a relationship", self.add, "primary")
        if kind == "events":
            empty_menu = QMenu(empty_create)
            self.populate_new_menu(empty_menu)
            empty_create.setMenu(empty_menu)
        layout.addWidget(empty_create, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        root.addWidget(self.empty)
        self.splitter = splitter
        self.list.currentRowChanged.connect(self.select)
        self.search.textChanged.connect(self.apply_filter)
        self.filter.currentIndexChanged.connect(self.apply_filter)

    def build_compact_collection(self, root):
        """Romance uses heart tiles for navigation, not a second event sidebar."""
        self.notice = label("", "notice", True)
        self.notice.hide()
        root.addWidget(self.notice)
        self.reference_issues = QListWidget()
        self.reference_issues.setMaximumHeight(100)
        self.reference_issues.hide()
        self.reference_issues.itemActivated.connect(self.open_reference_issue)
        root.addWidget(self.reference_issues)
        # Keep one stable row model for project history and review links.
        self.list = QListWidget(self)
        self.search = line("", 100)
        self.filter = choices([("All scenes", "all")])
        for widget in (self.list, self.search, self.filter):
            widget.setParent(self)
            widget.hide()
        self.list.currentRowChanged.connect(self.select)
        self.editor = QWidget()
        self.content = QVBoxLayout(self.editor)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(6)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.editor)
        root.addWidget(self.splitter, 1)
        self.empty = QWidget(self)
        self.empty.hide()
        self.record_menu_button = button("•••", lambda: None, "quiet")
        self.record_menu_button.setAccessibleName("Scene actions")
        self.record_menu_button.setFixedWidth(38)
        self.record_menu = QMenu(self.record_menu_button)
        self.duplicate_action = self.record_menu.addAction("Duplicate scene", self.duplicate)
        self.remove_action = self.record_menu.addAction("Remove scene", self.remove)
        self.record_menu_button.setMenu(self.record_menu)
        self.duplicate_button = button("Duplicate", self.duplicate)
        self.remove_button = button("Remove", self.remove)
        self.undo_button = button("Undo remove", self.restore_removed)
        for widget in (self.duplicate_button, self.remove_button, self.undo_button):
            widget.setParent(self)
            widget.hide()

    def populate_new_menu(self, menu):
        menu.addAction("Blank event", lambda: self.add("blank"))
        outlines = menu.addMenu("From a scene outline")
        for index in range(1, self.template.count()):
            template = self.template.itemData(index)
            outlines.addAction(self.template.itemText(index), lambda _checked=False, template=template: self.add(template))
        menu.addSeparator()
        menu.addAction("Add scenes from a vanilla NPC…", lambda: self.workshop.open_vanilla_template(mode="append"))

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
        self.reference_issues.hide()
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
        if getattr(self, "romance", False):
            return
        self.filter_button.setText(self.filter.currentText())
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
        self.notice.hide()
        self.reference_issues.hide()
        self.current = index
        self.loading = True
        enabled = 0 <= index < len(self.records)
        self.editor.setEnabled(enabled)
        self.duplicate_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.remove_action.setEnabled(enabled)
        self.duplicate_action.setEnabled(enabled)
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

    def _set_stage(self, stage):
        record = self.records[self.current]
        record["story"]["stage"] = stage
        self.list.item(self.current).setText(self.title(record))
        self.changed.emit()

    def capture_detail(self, record):
        pass

    def dump(self):
        return deepcopy(self.records)

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.show()

    def open_reference_issue(self, item):
        field = item.data(Qt.ItemDataRole.UserRole)
        if field.startswith("life."):
            window = self.workshop.window
            window.open_section("dialogue")
            window.life.open_issue(field)
        else:
            self.workshop.open_issue(field)

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
            self.phases.setCurrentIndex(self.SCENE)
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
            record["story"]["arc_ids"] = []
            for effect in record["story"].get("planned_effects", []):
                effect["id"] = str(uuid.uuid4())
            for key in ("actors", "beats"):
                for entry in record["story"][key]:
                    entry["id"] = str(uuid.uuid4())
                    for option in entry.get("choices", []):
                        option["id"] = str(uuid.uuid4())
        self.records.append(record)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.refresh(len(self.records) - 1)
        self.changed.emit()

    def remove(self):
        if self.current < 0:
            return
        record = self.records[self.current]
        character = self.workshop.character()
        if self.kind == "events":
            issues = event_removal_issues(character, record["id"])
            if issues:
                self.show_notice("This event is used by another condition. Open a link below to change it, then remove the event.")
                self.reference_issues.clear()
                for issue in issues:
                    item = QListWidgetItem(issue["message"])
                    item.setData(Qt.ItemDataRole.UserRole, issue["field"])
                    self.reference_issues.addItem(item)
                self.reference_issues.show()
                return
            updated = remove_event(character, record["id"])
            self.removed_memberships = {chapter["id"]: chapter["event_ids"].index(record["id"])
                                       for chapter in character.get("storyline", {}).get("chapters", [])
                                       if record["id"] in chapter.get("event_ids", [])}
            if getattr(self, "romance", False):
                self.workshop.legacy_storyline = deepcopy(updated["storyline"])
            else:
                self.workshop.storyline.load(updated)
            references = []
        else:
            references = relationship_references(character, record["id"])
        if references:
            self.show_notice("Change these links before removing this story:\n" + "\n".join(references))
            return
        self.notice.hide()
        self.reference_issues.hide()
        self.removed = (self.current, deepcopy(record))
        del self.records[self.current]
        if getattr(self, "romance", False):
            # A milestone is the user's navigation context, independent of the
            # flat serialization order (multipart scenes may be appended).
            from pixelheart_core.romance import ROMANCE_HEARTS
            heart = record.get("hearts")
            self.workshop.loading = True
            try:
                self.refresh(self.current)
            finally:
                self.workshop.loading = False
            if heart in ROMANCE_HEARTS:
                self.workshop.select_heart(heart)
            else:
                self.workshop._selection_changed(self.current)
        else:
            self.refresh(self.current)
        self.undo_button.show()
        self.changed.emit()

    def restore_removed(self):
        if self.removed and len(self.records) < 100:
            index, record = self.removed
            self.records.insert(index, record)
            if self.kind == "events":
                chapters = (self.workshop.legacy_storyline.get("chapters", []) if getattr(self, "romance", False)
                            else self.workshop.storyline.records)
                for chapter in chapters:
                    position = getattr(self, "removed_memberships", {}).get(chapter["id"])
                    if position is not None and record["id"] not in chapter["event_ids"]:
                        chapter["event_ids"].insert(min(position, len(chapter["event_ids"])), record["id"])
                if not getattr(self, "romance", False):
                    self.workshop.storyline.refresh_context()
            self.removed = None
            self.undo_button.hide()
            self.refresh(index)
            self.changed.emit()


class EventsPage(StoryRecords):
    PURPOSE, TRIGGER, SCENE, AFTERMATH, REHEARSE = range(5)

    def __init__(self, workshop, *, romance=False):
        self.romance = romance
        if romance:
            self.TRIGGER, self.SCENE, self.REHEARSE = range(3)
        super().__init__(workshop, "events", compact=romance)
        self.event_views = {}
        self.source_action = self.record_menu.addAction("View vanilla source…", self.open_vanilla_source)
        self.source_action.setVisible(False)
        heading = QHBoxLayout()
        self.heading = heading
        self.compact_browser = button("Events", lambda: None, "quiet")
        self.compact_browser.setAccessibleName("Choose or create an event")
        self.compact_browser.hide()
        self.compact_event_menu = QMenu(self.compact_browser)
        self.compact_event_menu.aboutToShow.connect(self.populate_event_menu)
        self.compact_browser.setMenu(self.compact_event_menu)
        heading.addWidget(self.compact_browser)
        self.stage_label = label("IDEA", "badge")
        heading.addWidget(self.stage_label)
        self.source_button = button("Original scene", self.open_vanilla_source, "quiet")
        self.source_button.setIcon(story_icon("storyline"))
        self.source_button.hide()
        heading.addWidget(self.source_button)
        self.next_button = button("Choose the trigger →", self.next_step, "primary")
        self.next_button.setParent(self)
        self.next_button.hide()
        heading.addWidget(self.record_menu_button)
        self.content.addLayout(heading)
        self.phases = QTabWidget()
        self.phases.setObjectName("storyEventSteps")
        self.phases.setDocumentMode(True)
        self.phases.setAccessibleName("Romance scene editor" if romance else "Story development steps")
        self.phases.setIconSize(QSize(18, 18))
        self.content.addWidget(self.phases)
        self.fields = {"name": line("A name for this moment", 100),
                       "hearts": number(0, 14), "location": MapSelector(compact=True)}
        if not romance:
            self.fields["description"] = prose("Keep the original idea or any context worth remembering.", 85)
        self.fields["name"].setObjectName("storyEventTitle")
        self.fields["name"].setAccessibleName("Event title")
        heading.insertWidget(1, self.fields["name"], 1)
        self.story_fields = {
            "previous_event_id": QComboBox(),
            "season": choices([("Any season", "any")] + [(x.title(), x) for x in ("spring", "summer", "fall", "winter")]),
            "weather": choices([("Any weather", "any"), ("Sunny", "sunny"), ("Rainy", "rainy")]),
            "time_start": EventTime(), "time_end": EventTime(), "music": line("none", 100),
            "relationship": choices([("Any relationship", "any"), ("Not married to this character", "unmarried"),
                                     ("Dating this character", "dating"), ("Married to this character", "married")]),
            "min_house_upgrade": choices([("Any farmhouse", 0), ("First upgrade or higher", 1), ("Nursery upgrade or higher", 2), ("Cellar upgrade", 3)]),
            "repeat": choices([("Once per save", "once"), ("Daily / reload · needs Event Repeater", "daily")]),
        }
        if not romance:
            self.story_fields.update({
            "premise": prose("What does your character want in this moment?", 80),
            "conflict": prose("What gives the moment weight? Difficulty is optional.", 75),
            "outcome": prose("What changes in their understanding, behavior, or relationship?", 80),
            "player_role": prose("What does the farmer get to do, witness, or decide?", 70),
            "before": prose("How do they enter this moment?", 65),
            "after": prose("How do they leave it?", 65),
            "motif": prose("An activity, place, object, gesture, or earlier detail to return to.", 65),
            "relationship_id": QComboBox(),
            })
        for key in ("previous_event_id", "relationship", "min_house_upgrade", "repeat"):
            self.story_fields[key].setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.story_fields[key].setMinimumContentsLength(16)
        if not romance:
            purpose, layout = card("Give the moment a purpose", "One change the player can notice. An ordinary shared experience can be enough.")
            add_form(layout, [("They want…", self.story_fields["premise"]),
                              ("By the end…", self.story_fields["outcome"]), ("The player's part", self.story_fields["player_role"])])
            self.arc_links = QListWidget()
            self.arc_links.setAccessibleName("Story arcs this event develops")
            self.arc_links.setMaximumHeight(115)
            self.arc_links.setWordWrap(True)
            layout.addWidget(label("Contributes to these arcs", "sectionTitle"))
            layout.addWidget(self.arc_links)
            layout.addWidget(label("These links organize the story. The people involved do not have to appear in this scene.", "hint", True))
            self.more_prompts = QCheckBox("More writing prompts")
            layout.addWidget(self.more_prompts)
            self.prompt_panel = QWidget()
            prompt_layout = QVBoxLayout(self.prompt_panel)
            prompt_layout.setContentsMargins(0, 0, 0, 0)
            add_form(prompt_layout, [("The original idea", self.fields["description"]), ("What gives it weight", self.story_fields["conflict"]),
                                     ("Before", self.story_fields["before"]), ("After", self.story_fields["after"]),
                                     ("Motif or callback", self.story_fields["motif"])])
            self.prompt_panel.hide()
            self.more_prompts.toggled.connect(self.prompt_panel.setVisible)
            layout.addWidget(self.prompt_panel)
            layout.addStretch()
            self.add_phase(purpose, "Purpose")

        if romance:
            trigger, setup_layout = card("When it happens", "The event begins when the farmer enters this location and meets these conditions.")
            self.access_summary = label("", "notice", True)
            setup_layout.addWidget(self.access_summary)
            self.fields["hearts"].setParent(self)
            self.fields["hearts"].setToolTip("Changing this value moves the scene to the matching heart milestone.")
            setup_layout.addWidget(TriggerFields([
                ("Enter location", self.fields["location"]), ("Relationship", self.story_fields["relationship"]),
                ("From", self.story_fields["time_start"]), ("Until", self.story_fields["time_end"]),
            ]))
            more = QCheckBox("Additional conditions")
            setup_layout.addWidget(more)
            conditions = QWidget()
            conditions_layout = QVBoxLayout(conditions)
            conditions_layout.setContentsMargins(0, 4, 0, 0)
            conditions_layout.addWidget(TriggerFields([
                ("Season", self.story_fields["season"]), ("Weather", self.story_fields["weather"]),
                ("Farmhouse", self.story_fields["min_house_upgrade"]), ("Repeat", self.story_fields["repeat"]),
                ("Minimum hearts", self.fields["hearts"]),
            ]))
            add_form(conditions_layout, [("Requires completed scene", self.story_fields["previous_event_id"])])
            conditions.hide()
            more.toggled.connect(conditions.setVisible)
            self.conditions_toggle = more
            self.conditions_panel = conditions
            setup_layout.addWidget(conditions)
            setup_layout.addStretch()
            self.add_phase(trigger, "Trigger")
        else:
            trigger, setup_layout = card("When the moment finds you", "Minimum friendship and relationship status are separate requirements. Outline order does not set either one.")
            self.access_summary = label("", "notice", True)
            setup_layout.addWidget(self.access_summary)
            controls = [("Minimum hearts", self.fields["hearts"]), ("Relationship", self.story_fields["relationship"]),
                        ("Enter location", self.fields["location"]), ("Season", self.story_fields["season"]),
                        ("Weather", self.story_fields["weather"]), ("Farmhouse", self.story_fields["min_house_upgrade"]),
                        ("From", self.story_fields["time_start"]), ("Until", self.story_fields["time_end"])]
            setup_layout.addWidget(TriggerFields(controls))
            form = add_form(setup_layout, [("Requires completed event", self.story_fields["previous_event_id"]), ("Repeat", self.story_fields["repeat"])])
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            setup_layout.addWidget(label("Use a prerequisite only when the scene needs that earlier experience. Dating-only scenes need a plan for players who become engaged or married first.", "hint", True))
            setup_layout.addWidget(label("Enter the location while every condition matches. 24:00–26:00 is after midnight. Repeating scenes need Event Repeater and cannot anchor lasting story progress.", "hint", True))
            setup_layout.addStretch()
            self.add_phase(trigger, "Trigger")

        scene = QWidget()
        self.scene_workspace = scene
        scene_layout = QVBoxLayout(scene)
        scene_layout.setContentsMargins(0, 6, 0, 0)
        self.scene_split = QSplitter()
        self.scene_split.setChildrenCollapsible(False)
        self.actors = CastEditor()
        self.scene_split.addWidget(self.actors)
        self.scene_inspector = QTabWidget()
        self.scene_inspector.setObjectName("sceneInspector")
        self.scene_inspector.setDocumentMode(True)
        self.scene_inspector.setAccessibleName("Scene editing tools")
        self.beats = BeatsEditor(inspector=True)
        self.scene_inspector.addTab(self.beats, "Sequence")
        cast_scroll = QScrollArea()
        cast_scroll.setWidgetResizable(True)
        cast_scroll.setWidget(self.actors.details_panel)
        self.actors.details_panel.show()
        self.scene_inspector.addTab(cast_scroll, "Cast")
        self.actors.details_toggle.toggled.connect(lambda checked: self.scene_inspector.setCurrentIndex(1) if checked else None)
        self.stage_options_toggle = QCheckBox("Scene settings", self)
        self.stage_options_toggle.hide()
        self.stage_options = QWidget()
        stage_options_layout = QVBoxLayout(self.stage_options)
        stage_options_layout.setContentsMargins(12, 14, 12, 14)
        settings = [("Music track", self.story_fields["music"])]
        if not romance:
            settings.append(("Onstage relationship", self.story_fields["relationship_id"]))
        add_form(stage_options_layout, settings)
        stage_options_layout.addStretch()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(self.stage_options)
        self.scene_inspector.addTab(settings_scroll, "Settings")
        self.stage_options_toggle.toggled.connect(lambda checked: self.scene_inspector.setCurrentIndex(2) if checked else None)
        self.scene_split.addWidget(self.scene_inspector)
        self.scene_split.setStretchFactor(0, 1)
        self.scene_split.setSizes([620, 310])
        scene_layout.addWidget(self.scene_split, 1)
        self.add_phase(scene, "Scene")

        if not romance:
            self.aftermath = EventAftermath(workshop)
            self.add_phase(self.aftermath, "Aftermath")
        review = QWidget()
        layout = QVBoxLayout(review)
        layout.setContentsMargins(0, 10, 0, 0)
        rehearsal, rehearsal_layout = card("Preview dialogue", "Read each line and try the player choices.")
        self.trigger_summary = label("", "muted", True)
        rehearsal_layout.addWidget(self.trigger_summary)
        profile = QHBoxLayout()
        self.farmer_name = line("Farmer's name", 80)
        self.farmer_name.setText("Farmer")
        self.farmer_name.setAccessibleName("Farmer name for dialogue preview")
        self.rehearsal_gender = choices([("Woman", "female"), ("Man", "male")])
        self.rehearsal_gender.setAccessibleName("Farmer gender for dialogue preview")
        profile.addWidget(self.farmer_name, 1)
        profile.addWidget(self.rehearsal_gender)
        rehearsal_layout.addLayout(profile)
        self.rehearsal_position = 0
        self.rehearsal_counter = label("", "eyebrow")
        self.rehearsal_text = label("Add a beat to begin.", "profileName", True)
        self.rehearsal_text.setMinimumHeight(90)
        rehearsal_layout.addWidget(self.rehearsal_counter)
        dialogue_row = QHBoxLayout()
        self.rehearsal_portrait = label("")
        self.rehearsal_portrait.setFixedSize(96, 96)
        self.rehearsal_portrait.setAccessibleName("Speaking character's portrait")
        self.rehearsal_portrait.hide()
        dialogue_row.addWidget(self.rehearsal_portrait, 0, Qt.AlignmentFlag.AlignTop)
        dialogue_row.addWidget(self.rehearsal_text, 1)
        rehearsal_layout.addLayout(dialogue_row)
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
        self.playtest_status = label("Not tested in-game", "muted", True)
        checks_layout.addWidget(self.playtest_status)
        checks_layout.addWidget(button("Open in-game playtest", self.open_playtest, "quiet"))
        checks_layout.addWidget(label("Test movement and timing in Stardew Valley before publishing.", "hint", True))
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
        self.add_phase(review, "Rehearse")
        self.phases.currentChanged.connect(self.phase_changed)
        self.actors.changed.connect(self.edit)
        self.beats.changed.connect(self.edit)
        if not romance:
            self.arc_links.itemChanged.connect(self.edit)
            self.aftermath.changed.connect(self.edit)
        self.actors.previewChanged.connect(self.preview_profile_changed)
        self.actors.previewVisibilityChanged.connect(lambda _visible: self.fit_scene_canvas())
        self.rehearsal_gender.currentIndexChanged.connect(self.rehearsal_profile_changed)
        self.farmer_name.textChanged.connect(lambda: self.rehearse() if self.current >= 0 else None)
        self.bind_fields()
        if romance:
            self.phases.setTabText(self.TRIGGER, "When")
            self.phases.setTabText(self.REHEARSE, "Preview")
        self.phases.setCurrentIndex(self.SCENE)

    def preview_profile_changed(self):
        self.rehearsal_gender.blockSignals(True)
        self.rehearsal_gender.setCurrentIndex(self.rehearsal_gender.findData(self.actors.farmer.currentData()))
        self.rehearsal_gender.blockSignals(False)
        self.update_preview()

    def rehearsal_profile_changed(self):
        self.actors.farmer.setCurrentIndex(self.actors.farmer.findData(self.rehearsal_gender.currentData()))

    def update_scene_preview(self, record, character):
        from pixelheart_core.scene_preview import resolve_scene_preview
        from .game_import import game_import_settings, LocalGameSourceWidget
        from .game_connection import game_connection
        document = {**self.workshop.window.document, "character": character}
        source = game_import_settings().value(LocalGameSourceWidget.SETTINGS_KEY, "")
        connection = game_connection()
        game_root = str(connection.install().root) if connection.state() == "connected" else ""
        result = resolve_scene_preview(document, self.workshop.window.project_file, record,
                                       export_root=(source.strip() or None) if isinstance(source, str) else None,
                                       game_root=(game_root.strip() or None) if isinstance(game_root, str) else None,
                                       preview_location=self.actors.preview_location.text() or "Town",
                                       farmer_gender=self.actors.farmer.currentData())
        self.actors.preview_location_panel.setVisible(not str(record.get("location", "")).strip())
        self._scene_assets = result
        key = (result.get("key"), result.get("location_label"), self.actors.farmer.currentData())
        if key != getattr(self, "_scene_preview_key", None):
            self._scene_preview_key = key
            sprites = {actor: _preview_image(pixels) for actor, pixels in result.get("sprites", {}).items() if pixels is not None}
            self.actors.canvas.set_scene_preview(background=_preview_image(result.get("background")), map_size=result.get("map_size"),
                                                 sprites=sprites, location_label=result.get("location_label", "Town square"),
                                                 source_note=result.get("source_note", ""),
                                                 farmer_gender=self.actors.farmer.currentData(), key=key,
                                                 foreground=_preview_image(result.get("foreground")))
        location_label = result.get("location_label") or "Town square"
        self.actors.location_label.setText(location_label)
        self.actors.location_label.setToolTip(result.get("source_note", ""))
        unavailable = result.get("background") is None
        self.actors.source_note.setText(result.get("source_note", ""))
        self.actors.source_note.hide()
        self.actors.game_artwork.setVisible(unavailable)
        self.actors.game_artwork.setToolTip("\n".join(result.get("warnings", [])) or "Use local game exports for scene maps and supporting cast artwork.")

    def add_phase(self, page, title):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        index = self.phases.addTab(scroll, story_icon(title.lower()), title)
        self.phases.setTabToolTip(index, {
            "Purpose": "What changes between these characters?",
            "Trigger": "Where and when the player can enter this event",
            "Scene": "Stage the cast and write the sequence",
            "Aftermath": "Connect choices to lasting changes",
            "Rehearse": "Play through the scene and check readiness",
        }[title])

    def fit_scene_canvas(self):
        if not hasattr(self, "scene_split"):
            return
        compact_navigation = self.width() < 920
        if self.romance:
            self.compact_browser.hide()
            available = self.width()
        else:
            self.rail.setVisible(not compact_navigation)
            self.compact_browser.setVisible(compact_navigation)
            available = self.width() - (8 if compact_navigation else self.rail.width() + 16)
        compact = available < 700
        preview_closed = self.actors.preview_panel.isHidden()
        self.actors.setMaximumHeight(44 if preview_closed else 16777215)
        orientation = Qt.Orientation.Vertical if compact or preview_closed else Qt.Orientation.Horizontal
        if self.scene_split.orientation() != orientation:
            self.scene_split.setOrientation(orientation)
            self.scene_split.setSizes([44, 600] if preview_closed else [280, 240] if compact else [640, 310])
        self.scene_inspector.setMinimumWidth(0 if compact or preview_closed else 275)
        self.scene_inspector.setMaximumWidth(16777215 if compact or preview_closed else 370)
        self.actors.canvas.setMinimumHeight(150 if compact else 220)

    def populate_event_menu(self):
        if self.romance:
            return
        self.compact_event_menu.clear()
        for index, record in enumerate(self.records):
            action = self.compact_event_menu.addAction(record.get("name") or "Untitled event",
                                                      lambda _checked=False, index=index: self.choose_event(index))
            action.setCheckable(True)
            action.setChecked(index == self.current)
        self.compact_event_menu.addSeparator()
        create = self.compact_event_menu.addMenu("New event")
        self.populate_new_menu(create)

    def open_vanilla_source(self):
        if self.current < 0:
            return
        reference = self.records[self.current]["story"].get("vanilla_source")
        if isinstance(reference, dict):
            from .vanilla_story import VanillaSourceDialog
            dialog = VanillaSourceDialog(reference, self)
            dialog.exec()
            dialog.deleteLater()

    def choose_event(self, index):
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.list.setCurrentRow(index)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self.fit_scene_canvas)

    def refresh_links(self):
        if not 0 <= self.current < len(self.records):
            return
        record = self.records[self.current]
        links = [("previous_event_id", self.records, "No earlier event required")]
        if not self.romance:
            links.insert(0, ("relationship_id", self.workshop.relationships.records, "Standalone event"))
        for key, records, empty in links:
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
        if self.romance:
            return
        blocked = self.arc_links.blockSignals(True)
        selected_arcs = record["story"].get("arc_ids", [])
        self.arc_links.clear()
        available = {arc["id"]: arc.get("name") or "Untitled arc" for arc in self.workshop.relationships.records}
        for identity in selected_arcs:
            available.setdefault(identity, "Missing arc — uncheck to remove link")
        for identity, name in available.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if identity in selected_arcs else Qt.CheckState.Unchecked)
            self.arc_links.addItem(item)
        self.arc_links.blockSignals(blocked)

    def load_detail(self, record):
        self.actors.load(record["story"]["actors"])
        self.actors.canvas.load(self.actors.records, {"$npc": self.workshop.character().get("name", "Your character"), "farmer": "Farmer"})
        self.beats.load(record["story"]["beats"])
        self.refresh_actor_choices()
        if not self.romance:
            self.aftermath.load(record, self.workshop.character())
        self.rehearsal_position = 0
        if self.romance:
            story = record["story"]
            self.conditions_toggle.setChecked(bool(story["previous_event_id"] or story["min_house_upgrade"]
                                                   or story["season"] != "any" or story["weather"] != "any"
                                                   or story["repeat"] != "once"))
        default_view = self.SCENE if self.romance or record["story"].get("vanilla_source") else {
            "idea": self.PURPOSE, "outline": self.PURPOSE,
            "scene": self.SCENE, "ready": self.REHEARSE,
        }.get(record["story"]["stage"], self.PURPOSE)
        self.phases.setCurrentIndex(self.event_views.get(record["id"], default_view))

    def capture_detail(self, record):
        record["story"]["actors"] = deepcopy(self.actors.records)
        record["story"]["beats"] = deepcopy(self.beats.records)
        if self.romance:
            return
        record["story"]["arc_ids"] = [self.arc_links.item(i).data(Qt.ItemDataRole.UserRole)
                                       for i in range(self.arc_links.count())
                                       if self.arc_links.item(i).checkState() == Qt.CheckState.Checked]
        self.aftermath.capture(record)

    def refresh_actor_choices(self):
        names = {identity: caption for caption, identity in self.actors.options}
        options = [(names.get(actor["name"], actor["name"]), actor["name"]) for actor in self.actors.records if actor.get("name")]
        self.beats.set_actor_options(options)

    def phase_changed(self):
        if not self.loading and 0 <= self.current < len(self.records):
            self.event_views[self.records[self.current]["id"]] = self.phases.currentIndex()
        if hasattr(self, "next_button"):
            self.next_button.setText(["Choose the trigger →", "Build the scene →", "Plan the aftermath →", "Rehearse the scene →", "Review && export →"][self.phases.currentIndex()])
        if hasattr(self.workshop, "sync_workspace_presentation"):
            self.workshop.sync_workspace_presentation()
        QTimer.singleShot(0, self, self.fit_scene_canvas)

    def next_step(self):
        if self.current < 0:
            return
        index = self.phases.currentIndex()
        if index == self.REHEARSE:
            if self.records[self.current]["story"]["stage"] != "ready" and not self.mark_ready():
                return
            self.workshop.window.open_section("export")
            return
        stage = "outline" if index == self.PURPOSE else "scene"
        if index == self.TRIGGER:
            story = self.records[self.current]["story"]
            if not story["actors"]:
                story["actors"] = new_event(self.workshop.character())["story"]["actors"]
                self.actors.load(story["actors"])
            if not story["beats"]:
                story["beats"] = [new_beat()]
                self.beats.load(story["beats"])
        if self.records[self.current]["story"]["stage"] != "ready":
            self._set_stage(stage)
        self.phases.setCurrentIndex(index + 1)
        self.update_preview()

    def mark_ready(self):
        if self.current < 0:
            return False
        self.phases.setCurrentIndex(self.REHEARSE)
        issues = self.readiness_issues(self.workshop.character())
        if any(issue["level"] == "error" for issue in issues):
            self.show_notice("A few scene details still need attention. Activate a check below to go straight to it.")
            self.update_preview()
            return False
        self.notice.hide()
        self._set_stage("ready")
        self.update_preview()
        return True

    def readiness_issues(self, character):
        # Draft references may be warnings. Preview the validation that will
        # apply after promotion before enabling or performing that promotion.
        candidate = deepcopy(character)
        event = candidate["events"][self.current]
        event["story"]["stage"] = "ready"
        return event_issues(event, candidate)

    def return_to_draft(self):
        if self.current >= 0:
            self._set_stage("scene")
            self.update_preview()

    def update_preview(self):
        if self.current < 0 or not hasattr(self, "checks"):
            return
        record = self.records[self.current]
        story = record["story"]
        self.source_action.setVisible(isinstance(story.get("vanilla_source"), dict))
        self.source_button.setVisible(self.source_action.isVisible())
        character = self.workshop.character()
        self.update_scene_preview(record, character)
        self.stage_label.setText("Ready to export" if story["stage"] == "ready" else "Draft")
        self.stage_label.setToolTip(STAGES[story["stage"]] + (" · included in export" if story["stage"] == "ready" else " · saved with your project"))
        relationship = {"any": "any relationship", "unmarried": "not married to this character", "dating": "dating this character", "married": "married to this character"}[story["relationship"]]
        when = f"Enter {record.get('location') or 'the chosen location'} at {record.get('hearts', 0)} or more hearts, {story['time_start'] // 100:02d}:{story['time_start'] % 100:02d}–{story['time_end'] // 100:02d}:{story['time_end'] % 100:02d}; {relationship}."
        if story["season"] != "any":
            when += f" {story['season'].title()} only."
        if story["weather"] != "any":
            when += f" {story['weather'].title()} weather."
        if story["min_house_upgrade"]:
            when += f" Farmhouse upgrade {story['min_house_upgrade']} or higher."
        if story["previous_event_id"]:
            prior = next((event.get("name") for event in character["events"] if event["id"] == story["previous_event_id"]), "missing event")
            when += f" Requires: {prior}."
        when += " Once per save." if story["repeat"] == "once" else " Repeats after a new day or reload."
        self.trigger_summary.setText(when)
        self.access_summary.setText(when)
        self.update_playtest_status(character)
        self.checks.clear()
        issues = self.readiness_issues(character)
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
            # The isolated script preview is not an edit to chapter membership.
            # Omit unrelated planning references from this temporary compiler input.
            candidate.pop("storyline", None)
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

    def update_playtest_status(self, character):
        from pixelheart_core.playtesting import content_fingerprint
        document = deepcopy(self.workshop.window.document)
        document["character"] = character
        evidence = document.get("creator", {}).get("tests", {}).get("event:" + self.records[self.current]["id"], {})
        if not evidence:
            text = "In-game test: not tested."
        elif evidence.get("fingerprint") != content_fingerprint(document):
            text = "In-game test: results from an earlier revision. Test the current export again."
        else:
            text = "In-game test: " + {"passed": "passed for this revision.", "failed": "needs a fix.", "untested": "not tested."}.get(evidence.get("status"), "not tested.")
        self.playtest_status.setText(text)

    def open_playtest(self):
        window = self.workshop.window
        window.open_section("export")
        window.export_page.tabs.setCurrentIndex(1)
        window.playtest.refresh()
        if self.current >= 0:
            identity = "event:" + self.records[self.current]["id"]
            for index in range(window.playtest.tests.count()):
                if window.playtest.tests.item(index).data(Qt.ItemDataRole.UserRole).get("id") == identity:
                    window.playtest.tests.setCurrentRow(index)
                    break

    def rehearse(self):
        from pixelheart_core.rehearsal import preview_dialogue, portrait_expression
        def rendered(text):
            return preview_dialogue(text, farmer_name=self.farmer_name.text(), farmer_gender=self.actors.farmer.currentData())

        record = self.records[self.current]
        beats = record["story"]["beats"]
        self.rehearsal_position = max(0, min(self.rehearsal_position, len(beats) - 1))
        index = self.rehearsal_position
        self.previous_beat.setEnabled(bool(beats) and index > 0)
        self.next_beat.setEnabled(bool(beats) and index < len(beats) - 1)
        self.rehearsal_choice.hide()
        self.choice_result.hide()
        self.rehearsal_portrait.hide()
        if not beats:
            self.rehearsal_counter.setText("YOUR SCENE STARTS HERE")
            self.rehearsal_text.setText("Add a dialogue or action beat in Scene to hear the story take shape.")
            return
        beat = beats[index]
        who = next((caption for caption, identity in self.actors.options if identity == beat.get("actor", "$npc")), actor_caption(beat.get("actor", "$npc"), self.workshop.character()))
        kind = beat["kind"]
        portrait = getattr(self, "_scene_assets", {}).get("portraits", {}).get(beat.get("actor", "$npc"))
        if portrait is not None and kind in ("dialogue", "choice"):
            from pixelheart_core.scene_preview import crop_portrait_frame
            frame = crop_portrait_frame(portrait, expression=portrait_expression(beat.get("text", ""), farmer_gender=self.actors.farmer.currentData()))
            if frame is not None:
                self.rehearsal_portrait.setPixmap(QPixmap.fromImage(_preview_image(frame)).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                self.rehearsal_portrait.show()
        self.rehearsal_counter.setText(f"BEAT {index + 1} OF {len(beats)}  ·  {KINDS[kind].upper()}")
        if kind == "dialogue":
            text = rendered(beat.get("text", ""))
            text = f"{who}\n\n{text or 'Their words are waiting for you.'}"
        elif kind == "choice":
            options = beat.get("choices", [])
            labels = [rendered(option.get("label", "")) or f"Answer {index + 1}" for index, option in enumerate(options)]
            if [self.rehearsal_choice.itemText(i) for i in range(self.rehearsal_choice.count())] != labels:
                selected_option = self.rehearsal_choice.currentIndex()
                self.rehearsal_choice.blockSignals(True)
                self.rehearsal_choice.clear()
                self.rehearsal_choice.addItems(labels)
                self.rehearsal_choice.setCurrentIndex(max(0, min(selected_option, len(labels) - 1)))
                self.rehearsal_choice.blockSignals(False)
            text = f"{who}\n\n{rendered(beat.get('text', ''))}\n\nChoose an answer to rehearse its consequence."
            self.rehearsal_choice.show()
            option_index = self.rehearsal_choice.currentIndex()
            if 0 <= option_index < len(options):
                option = options[option_index]
                self.choice_result.setText(f"{who}: {rendered(option.get('text', ''))}\n\nFriendship: {option.get('friendship', 0):+d} points. This answer ends the scene.")
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
        # The event tab may have been hidden behind Storyline. Its scroll range
        # settles on the next layout pass, so reveal the target again then.
        def reveal(widget):
            def ensure():
                if not isValid(widget):
                    return
                self.fit_scene_canvas()
                parent = widget.parentWidget()
                while parent is not None:
                    if isinstance(parent, QScrollArea):
                        parent.ensureWidgetVisible(widget)
                        # Text editors may be taller than their nested viewport.
                        # Qt reveals their cursor; also keep the field itself in
                        # view so the issue does not appear to focus empty space.
                        center = widget.mapTo(parent.widget(), widget.rect().center())
                        parent.ensureVisible(center.x(), center.y())
                    parent = parent.parentWidget()
            ensure()
            QTimer.singleShot(0, self, ensure)

        parts = field.split(".")
        if parts[0] == "events" and len(parts) > 2:
            parts = parts[2:]
        key = parts[-1]
        if self.romance and ("planned_effects" in parts or "arc_ids" in parts or key in {"aftermath_notes", "relationship_id"}):
            self.show_notice("This saved scene contains a legacy story link. Its data is preserved; review the linked entry in Play in Stardew.")
            return
        if self.romance and key in {"hearts", "season", "weather", "min_house_upgrade", "repeat", "previous_event_id"}:
            self.conditions_toggle.setChecked(True)
        if "actors" in parts:
            self.phases.setCurrentIndex(self.SCENE)
            self.scene_inspector.setCurrentIndex(1)
            self.actors.details_toggle.setChecked(True)
            offset = parts.index("actors") + 1
            if len(parts) > offset and parts[offset].isdigit():
                row = int(parts[offset])
                col = {"name": 0, "x": 1, "y": 2, "facing": 3}.get(key, 0)
                self.actors.table.setCurrentCell(row, col)
                widget = self.actors.table.cellWidget(row, col)
                if widget:
                    widget.setFocus()
                    reveal(widget)
            else:
                reveal(self.actors.table)
        elif "beats" in parts:
            self.phases.setCurrentIndex(self.SCENE)
            self.scene_inspector.setCurrentIndex(0)
            offset = parts.index("beats") + 1
            if len(parts) > offset and parts[offset].isdigit():
                self.beats.list.setCurrentRow(int(parts[offset]))
            if "choices" in parts:
                option_offset = parts.index("choices") + 1
                option = int(parts[option_offset]) if len(parts) > option_offset and parts[option_offset].isdigit() else 0
                widget = self.beats.choice_fields[min(1, option)].get(key, self.beats.choice_panel)
                widget.setFocus()
                reveal(widget)
            elif key in self.beats.fields:
                self.beats.fields[key].setFocus()
                reveal(self.beats.fields[key])
            else:
                reveal(self.beats)
        elif "planned_effects" in parts or key == "aftermath_notes":
            self.phases.setCurrentIndex(self.AFTERMATH)
            target = self.aftermath.focus_field(field)
            if target is not None:
                reveal(target)
        elif "arc_ids" in parts:
            self.phases.setCurrentIndex(self.PURPOSE)
            self.arc_links.setFocus()
            reveal(self.arc_links)
        elif key in self.fields or key in self.story_fields:
            purpose_fields = {"name", "description", "premise", "conflict", "outcome", "player_role", "before", "after", "motif"}
            phase = self.SCENE if self.romance and key == "name" else self.PURPOSE if key in purpose_fields else self.SCENE if key in {"music", "relationship_id"} else self.TRIGGER
            if key in {"description", "conflict", "before", "after", "motif"}:
                self.more_prompts.setChecked(True)
            if key in {"music", "relationship_id"}:
                self.stage_options_toggle.setChecked(True)
                self.scene_inspector.setCurrentIndex(2)
            self.phases.setCurrentIndex(phase)
            widget = self.story_fields.get(key) or self.fields[key]
            widget.setFocus()
            reveal(widget)
        else:
            self.phases.setCurrentIndex(self.SCENE)


class RelationshipsPage(StoryRecords):
    def __init__(self, workshop):
        super().__init__(workshop, "relationships")
        self.fields = {"name": line("Name this connection or aspiration", 80),
                       "relation": line("Friend, family, rival, creative life…", 80),
                       "description": prose("Where does this thread begin? What is already true?", 85)}
        self.story_fields = {
            "kind": choices([("Relationship", "relationship"), ("Personal aspiration", "personal")]),
            "target": ActorSelector("farmer"),
            "desire": prose("What do they want from this connection or pursuit?", 70),
            "tension": prose("What makes change difficult, if anything?", 70),
            "progression": prose("What do they begin to understand or do differently?", 70),
            "resolution": prose("What changes, and what remains recognizably theirs?", 70),
            "independent_desire": prose("What matters to them outside the farmer's approval?", 65),
            "boundaries": prose("What does trust look like, and what remains private?", 65),
            "motif": prose("An activity, place, object, or gesture that develops with them.", 65),
            "friendship_payoff": prose("What makes this bond satisfying even if they never date?", 65),
            "dating": prose("If they date, why do these people choose each other?", 65),
            "married": prose("How can love and independent identity coexist?", 65),
        }
        self.story_fields["target"].combo.setAccessibleName("Other person in this character's relationship")
        self.stage_label = label("IDEA", "badge")
        self.content.removeWidget(self.record_menu_button)
        header = QHBoxLayout()
        self.fields["name"].setObjectName("storyEventTitle")
        header.addWidget(self.fields["name"], 1)
        header.addWidget(self.stage_label)
        header.addWidget(self.record_menu_button)
        self.content.addLayout(header)
        basics, layout = card()
        layout.setContentsMargins(16, 14, 16, 14)
        add_form(layout, [("Thread", self.story_fields["kind"]),
                          ("Connection", self.fields["relation"]), ("Other character", self.story_fields["target"])])
        self.content.addWidget(basics)
        arc, layout = card("Make room for change")
        add_form(layout, [("The starting point", self.fields["description"]), ("They want…", self.story_fields["desire"]), ("What complicates it", self.story_fields["tension"]),
                          ("How it develops", self.story_fields["progression"]), ("What changes", self.story_fields["resolution"])])
        self.more_prompts = QCheckBox("Identity, boundaries, and optional romance")
        layout.addWidget(self.more_prompts)
        self.prompt_panel = QWidget()
        prompt_layout = QVBoxLayout(self.prompt_panel)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        add_form(prompt_layout, [("Independent desire", self.story_fields["independent_desire"]),
                                 ("Intimacy and boundaries", self.story_fields["boundaries"]),
                                 ("Recurring motif", self.story_fields["motif"]),
                                 ("Friendship payoff", self.story_fields["friendship_payoff"]),
                                 ("Dating continuation", self.story_fields["dating"]),
                                 ("Married life", self.story_fields["married"])])
        self.prompt_panel.hide()
        self.more_prompts.toggled.connect(self.prompt_panel.setVisible)
        layout.addWidget(self.prompt_panel)
        self.notes_panel = arc
        self.notes_toggle = QCheckBox("Arc notes and writing prompts")
        self.notes_toggle.toggled.connect(self.notes_panel.setVisible)
        self.notes_panel.hide()
        milestones, layout = card("Chapters that develop this arc", "Preview an editable starter or link existing events in Purpose. One scene can develop several arcs without requiring every person onstage.")
        self.create_arc_button = button("Preview chapter starter…", self.create_arc, "primary")
        layout.addWidget(self.create_arc_button)
        self.linked_chapters = QListWidget()
        self.linked_chapters.setWordWrap(True)
        self.linked_chapters.setMinimumHeight(80)
        self.linked_chapters.setMaximumHeight(160)
        self.linked_chapters.setAccessibleName("Chapters developing this arc; activate to edit chapter")
        self.linked_chapters.itemActivated.connect(self.open_chapter)
        layout.addWidget(self.linked_chapters)
        self.linked_events = QListWidget()
        self.linked_events.setWordWrap(True)
        self.linked_events.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.linked_events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.linked_events.setMinimumHeight(120)
        self.linked_events.setMaximumHeight(250)
        self.linked_events.setAccessibleName("Relationship milestones; activate to edit event")
        self.linked_events.itemActivated.connect(self.open_event)
        layout.addWidget(self.linked_events)
        self.arc_status = label("", "muted", True)
        layout.addWidget(self.arc_status)
        self.ready_button = button("Mark arc ready", self.mark_ready)
        self.draft_button = button("Keep as draft", self.return_to_draft)
        actions = QHBoxLayout()
        actions.addWidget(self.ready_button)
        actions.addWidget(self.draft_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.content.addWidget(milestones)
        self.content.addWidget(self.notes_toggle)
        self.content.addWidget(self.notes_panel)
        self.content.addStretch()
        self.bind_fields()

    def create_arc(self):
        if self.current >= 0:
            self.workshop.storyline.open_starter(starter="friendship", relationship_id=self.records[self.current]["id"])

    def update_preview(self):
        if self.current < 0 or not hasattr(self, "linked_events"):
            return
        relationship = self.records[self.current]
        character = self.workshop.character()
        linked = related_events(character, relationship["id"])
        self.linked_chapters.clear()
        for chapter in character.get("storyline", {}).get("chapters", []):
            if relationship["id"] in chapter["arc_ids"]:
                item = QListWidgetItem(chapter["name"] or "Untitled chapter")
                item.setData(Qt.ItemDataRole.UserRole, chapter["id"])
                item.setToolTip("Open this chapter in Storyline")
                self.linked_chapters.addItem(item)
        self.linked_chapters.setVisible(self.linked_chapters.count() > 0)
        self.linked_events.clear()
        for event in linked:
            item = QListWidgetItem(self.workshop.events.title(event))
            item.setData(Qt.ItemDataRole.UserRole, event["id"])
            item.setToolTip("Open this event in the story workshop")
            self.linked_events.addItem(item)
        ready = sum(event["story"]["stage"] == "ready" for event in linked)
        self.stage_label.setText("Ready" if relationship["story"]["stage"] == "ready" else "Draft")
        note_count = sum(bool(relationship["story"].get(key)) for key in ("desire", "tension", "progression", "resolution", "independent_desire", "boundaries", "motif", "friendship_payoff", "dating", "married")) + bool(relationship.get("description"))
        self.notes_toggle.setText("Arc notes and writing prompts" + (f" · {note_count} filled" if note_count else ""))
        self.create_arc_button.setEnabled(True)
        self.story_fields["target"].setEnabled(relationship["story"].get("kind") != "personal")
        self.linked_events.setVisible(bool(linked))
        self.arc_status.setText(f"{ready} of {len(linked)} moments ready for export. Activate a milestone to continue writing." if linked else "Your arc is still a note. Give it a sequence of scenes to bring it into the game.")
        self.ready_button.setEnabled(bool(linked) and ready == len(linked) and relationship["story"]["stage"] != "ready")
        self.draft_button.setEnabled(relationship["story"]["stage"] == "ready")

    def return_to_draft(self):
        if self.current >= 0:
            self._set_stage("outline")

    def mark_ready(self):
        if self.current < 0:
            return False
        character = self.workshop.character()
        character["relationships"][self.current]["story"]["stage"] = "ready"
        prefix = f"relationships.{self.current}"
        issues = [issue for issue in story_issues(character) if issue["level"] == "error"
                  and (issue["field"] == prefix or issue["field"].startswith(prefix + "."))]
        if issues:
            self.show_notice(" ".join(issue["message"] for issue in issues))
            return False
        self._set_stage("ready")
        self.update_preview()
        return True

    def open_event(self, item):
        self.workshop.open_event(item.data(Qt.ItemDataRole.UserRole))

    def open_chapter(self, item):
        page = self.workshop.storyline
        self.workshop.tabs.setCurrentWidget(page)
        page.set_current_chapter(item.data(Qt.ItemDataRole.UserRole))


class StoryPage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("storyModes")
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(18, 18))
        self.summary = label("0 drafts · 0 ready", "muted")
        self.summary.setAccessibleName("Event progress")
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(self.summary)
        heading.addStretch()
        self.template_button = button("Load vanilla template…", self.open_vanilla_template, "primary")
        heading.addWidget(self.template_button)
        heading.addWidget(button("Checks", lambda: self.window.open_section("export"), "quiet"))
        root.addLayout(heading)
        self.events = EventsPage(self)
        self.relationships = RelationshipsPage(self)
        self.storyline = StorylinePage(self)
        self.tabs.addTab(self.storyline, story_icon("storyline"), "Storyline")
        self.tabs.addTab(self.events, story_icon("events"), "Events")
        self.tabs.addTab(self.relationships, story_icon("relationships"), "Relationships")
        root.addWidget(self.tabs, 1)
        self.events.changed.connect(self.on_change)
        self.relationships.changed.connect(self.on_change)
        self.storyline.changed.connect(self.on_change)
        self.tabs.currentChanged.connect(self.sync_workspace_presentation)

    def sync_workspace_presentation(self):
        window = self.window
        if not hasattr(window, "section_pages") or window.stack.currentWidget() is not window.section_pages.get("story"):
            return
        window.title_label.hide()
        window.subtitle.hide()

    def character(self):
        character = deepcopy(self.window.document["character"])
        character.update(self.window.identity.dump())
        if hasattr(self, "events"):
            character["events"] = self.events.dump()
        if hasattr(self, "relationships"):
            character["relationships"] = self.relationships.dump()
        if hasattr(self, "storyline"):
            character["storyline"] = self.storyline.dump()
        if hasattr(self.window, "life"):
            character["life"] = self.window.life.dump()
        return character

    def load(self, character):
        self.loading = True
        self.relationships.load(character.get("relationships", []))
        self.storyline.load(character)
        self.events.load(character.get("events", []))
        self.tabs.setCurrentIndex(0)
        self.loading = False
        self.refresh_context()

    def on_change(self):
        if not self.loading:
            for index, relationship in enumerate(self.relationships.records):
                if relationship["story"]["stage"] != "ready":
                    continue
                linked = related_events(self.character(), relationship["id"])
                if not linked or any(event["story"]["stage"] != "ready" for event in linked):
                    relationship["story"]["stage"] = "outline"
                    self.relationships.list.item(index).setText(self.relationships.title(relationship))
                    self.relationships.show_notice("A milestone is back in development. Review the linked scenes, then mark the relationship ready again.")
            self.refresh_context()
            self.changed.emit()

    def refresh_context(self):
        events = self.events.records
        counts = {stage: sum(event["story"]["stage"] == stage for event in events) for stage in STAGES}
        drafts = len(events) - counts['ready']
        self.summary.setText(f"{drafts} {'draft' if drafts == 1 else 'drafts'} · {counts['ready']} ready")
        self.summary.setToolTip(f"{counts['idea']} ideas · {counts['outline'] + counts['scene']} in development · {counts['ready']} ready for export")
        self.tabs.setTabText(self.tabs.indexOf(self.events), f"Events  ({len(events)})")
        self.tabs.setTabText(self.tabs.indexOf(self.relationships), f"Relationships  ({len(self.relationships.records)})")
        self.events.refresh_links()
        self.storyline.refresh_context()
        self.events.aftermath.refresh_context(self.character())
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
        self.sync_workspace_presentation()

    def open_event(self, identity):
        index = next((index for index, event in enumerate(self.events.records) if event["id"] == identity), -1)
        if index >= 0:
            self.tabs.setCurrentWidget(self.events)
            self.events.search.clear()
            self.events.filter.setCurrentIndex(0)
            self.events.list.setCurrentRow(index)

    def open_vanilla_template(self, *, mode="initialize"):
        from .vanilla_story import VanillaStoryDialog
        dialog = VanillaStoryDialog(self.character(), self, mode=mode)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_vanilla_template(dialog.bundle, dialog.selected_event_keys(),
                                        mode=dialog.mode.currentData())
        dialog.deleteLater()

    def apply_vanilla_template(self, bundle, event_keys, *, replace_starter=False, mode="append"):
        from pixelheart_core.vanilla_story import apply_vanilla_story, initialize_vanilla_story
        before = self.character()
        try:
            updated = (initialize_vanilla_story(before, bundle, event_keys) if mode == "initialize" else
                       apply_vanilla_story(before, bundle, event_keys, replace_starter=replace_starter))
        except ValueError as exc:
            self.tabs.currentWidget().show_notice(str(exc))
            return False
        if updated == before:
            if mode == "initialize" and updated["events"]:
                self.open_event(updated["events"][0]["id"])
                self.events.phases.setCurrentIndex(self.events.SCENE)
                self.events.show_notice("This template is already loaded. Opened its first scene.")
            else:
                self.tabs.currentWidget().show_notice("Those source events are already in this project.")
            return False
        previous = {event["id"] for event in before.get("events", [])}
        added = updated["events"] if mode == "initialize" else [event for event in updated["events"] if event["id"] not in previous]
        history = getattr(self.window, "project_history", None)
        if history is not None:
            history.close_group()
        if mode == "initialize":
            self.events.event_views.clear()
        self.load(updated)
        self.on_change()
        if added:
            self.open_event(added[0]["id"])
            self.events.phases.setCurrentIndex(self.events.SCENE)
            action = f"Loaded {bundle['npc']['name']}’s template" if mode == "initialize" else f"Added {len(added)} scenes"
            self.events.show_notice(f"{action}. These are editable drafts. Compare the Original scene, then review Aftermath before export.")
        return True

    def open_issue(self, field):
        parts = field.split(".")
        if parts[0] == "storyline":
            self.tabs.setCurrentWidget(self.storyline)
            self.storyline.open_issue(field)
            return
        relationships = parts[0] == "relationships"
        page = self.relationships if relationships else self.events
        self.tabs.setCurrentWidget(page)
        page.search.clear()
        page.filter.setCurrentIndex(0)
        if len(parts) > 1 and parts[1].isdigit():
            page.list.setCurrentRow(int(parts[1]))
        if not relationships and len(parts) > 2:
            page.focus_field(field)
        elif relationships:
            widget = {**page.fields, **page.story_fields}.get(parts[-1])
            if widget:
                if parts[-1] in {"description", "desire", "tension", "progression", "resolution", "independent_desire", "boundaries", "motif", "friendship_payoff", "dating", "married"}:
                    page.notes_toggle.setChecked(True)
                if parts[-1] in {"independent_desire", "boundaries", "motif", "friendship_payoff", "dating", "married"}:
                    page.more_prompts.setChecked(True)
                widget.setFocus()
                page.editor_scroll.ensureWidgetVisible(widget)
                QTimer.singleShot(0, page, lambda: page.editor_scroll.ensureWidgetVisible(widget) if isValid(widget) else None)
            else:
                page.editor_scroll.ensureWidgetVisible(page.linked_events)
