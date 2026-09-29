"""Chapter planning above the existing, independently exportable story scenes."""
from __future__ import annotations

from copy import deepcopy
import uuid

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QListWidget, QListWidgetItem, QSplitter, QScrollArea, QPlainTextEdit,
    QComboBox, QDialog, QTableWidget, QHeaderView, QAbstractItemView,
    QCheckBox, QDialogButtonBox, QPushButton,
)

from pixelheart_core.story import new_event
from pixelheart_core.projects import untouched_story_starter, clear_story_starter
from pixelheart_core.story_planning import (
    normalize_storyline, new_chapter, STORY_STARTERS,
    preview_story_starter, apply_story_starter,
)
from .editors import line, number, value, set_value, connect_change
from .widgets import label, button, card


PHASES = (("Any phase", "any"), ("Friendship", "friendship"),
          ("Dating", "dating"), ("Married life", "married"))
STAGES = {"idea": "Idea", "outline": "Outline", "scene": "Scene draft", "ready": "Ready for export"}


def _choices(options):
    widget = QComboBox()
    for caption, key in options:
        widget.addItem(caption, key)
    return widget


def _prose(placeholder):
    widget = QPlainTextEdit()
    widget.setPlaceholderText(placeholder)
    widget.setMinimumHeight(72)
    widget.setMaximumHeight(110)
    return widget


def _form(layout, entries):
    form = QFormLayout()
    form.setSpacing(10)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for caption, widget in entries:
        widget.setAccessibleName(caption)
        form.addRow(caption, widget)
    layout.addLayout(form)
    return form


class StoryStarterDialog(QDialog):
    """Editable, disposable preview; accepting does not itself change a project."""

    def __init__(self, character, parent=None, *, starter=None, relationship_id=""):
        super().__init__(parent)
        self.character = deepcopy(character)
        self.relationship_id = relationship_id
        self.preview = {}
        self.rows = []
        self.setWindowTitle("Choose a story starter")
        self.resize(760, 600)
        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.addWidget(label("A structure to make your own", "sectionTitle"))
        root.addWidget(label("Choose the moments you need. Titles, heart suggestions, and relationship phases are editable before adding drafts.", "muted", True))
        self.starter = _choices([(entry["name"], key) for key, entry in STORY_STARTERS.items()])
        self.starter.setAccessibleName("Story starter")
        if starter is None:
            starter = "romance" if character.get("romanceable") else "friendship"
        index = self.starter.findData(starter)
        self.starter.setCurrentIndex(max(index, 0))
        root.addWidget(self.starter)
        self.description = label("", "hint", True)
        root.addWidget(self.description)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Include", "Chapter", "Hearts", "Phase"])
        self.table.setAccessibleName("Editable story starter preview")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(220)
        root.addWidget(self.table, 1)
        self.summary = label("", "notice", True)
        root.addWidget(self.summary)
        root.addWidget(label("Only new selected chapters and their event drafts are added. Existing writing stays intact. Event prerequisites are left for you to choose.", "hint", True))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = actions.addButton("Add selected drafts", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName("primary")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)
        self.starter.currentIndexChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        starter = self.starter.currentData()
        self.preview = preview_story_starter(self.character, starter=starter, relationship_id=self.relationship_id)
        self.description.setText(STORY_STARTERS[starter]["description"])
        self.rows = []
        self.table.setRowCount(0)
        for index, chapter in enumerate(self.preview["chapters"]):
            self.table.insertRow(index)
            include = QCheckBox()
            include.setChecked(True)
            include.setAccessibleName(f"Include {chapter['name']}")
            name = line("Chapter title", 100)
            name.setText(chapter["name"])
            name.setAccessibleName(f"Chapter {index + 1} title")
            hearts = number(0, 14)
            hearts.setValue(chapter["hearts"])
            hearts.setAccessibleName(f"Chapter {index + 1} suggested hearts")
            phase = _choices(PHASES)
            set_value(phase, chapter["phase"])
            phase.setAccessibleName(f"Chapter {index + 1} relationship phase")
            widgets = {"include": include, "name": name, "hearts": hearts, "phase": phase}
            self.rows.append(widgets)
            for column, widget in enumerate(widgets.values()):
                self.table.setCellWidget(index, column, widget)
                connect_change(widget, self.update_summary)
            self.table.setRowHeight(index, 44)
        self.update_summary()

    def edited_preview(self):
        result = deepcopy(self.preview)
        for chapter, widgets in zip(result["chapters"], self.rows):
            chapter.update({key: value(widgets[key]) for key in ("name", "hearts", "phase")})
        return result

    def selected_chapter_ids(self):
        return [chapter["id"] for chapter, widgets in zip(self.preview["chapters"], self.rows)
                if widgets["include"].isChecked()]

    def update_summary(self):
        selected = self.selected_chapter_ids()
        existing = self.preview.get("existing_count", 0)
        self.summary.setText(f"{len(selected)} new chapters selected · {existing} existing milestones kept")
        self.apply_button.setEnabled(bool(selected) and all(
            widgets["name"].text().strip() for widgets in self.rows if widgets["include"].isChecked()))


class StorylinePage(QWidget):
    changed = Signal()

    def __init__(self, workshop):
        super().__init__()
        self.workshop = workshop
        self.data = normalize_storyline()
        self.current_chapter_id = ""
        self.loading = False
        self.removed = None
        self.cleared_starter = None
        self.compact = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)
        root.addWidget(label("Chapters organize your scenes. Each event keeps its own trigger.", "muted", True))
        actions = QHBoxLayout()
        actions.addWidget(button("+ New chapter", self.add_chapter))
        self.starter_button = button("Original outline…", self.open_starter, "quiet")
        actions.addWidget(self.starter_button)
        self.start_blank_button = button("Start blank", self.start_blank, "quiet")
        self.start_blank_button.setToolTip("Remove the preloaded outline and its event drafts while they are still untouched. Authored changes and linked daily-life rules must be reviewed individually.")
        actions.addWidget(self.start_blank_button)
        actions.addStretch()
        root.addLayout(actions)
        self.brief_toggle = QPushButton("Edit story brief")
        self.brief_toggle.setObjectName("quiet")
        self.brief_toggle.setCheckable(True)
        self.brief_toggle.toggled.connect(self.toggle_brief)
        actions.addWidget(self.brief_toggle)
        self.brief_summary = label("What changes in this character’s life?", "muted", True)
        root.addWidget(self.brief_summary)
        self.brief_panel, brief_layout = card()
        self.brief_fields = {
            "desire": _prose("What do they want beyond the romance?"),
            "question": _prose("What question does their story explore?"),
            "boundaries": _prose("What remains theirs to decide? What should friendship never require?"),
            "motif": _prose("A place, object, ritual, or image that gains meaning."),
        }
        _form(brief_layout, [("Desire", self.brief_fields["desire"]),
                             ("Story question", self.brief_fields["question"]),
                             ("Boundaries", self.brief_fields["boundaries"]),
                             ("Recurring motif", self.brief_fields["motif"])])
        self.brief_scroll = QScrollArea()
        self.brief_scroll.setWidgetResizable(True)
        self.brief_scroll.setWidget(self.brief_panel)
        self.brief_scroll.hide()
        root.addWidget(self.brief_scroll, 1)
        self.notice = label("", "notice", True)
        self.notice.hide()
        root.addWidget(self.notice)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        rail = QWidget()
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.addWidget(label("CHAPTER OUTLINE", "eyebrow"))
        self.list = QListWidget()
        self.list.setAccessibleName("Story chapters in author order")
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(210)
        self.list.currentItemChanged.connect(self.select_item)
        rail_layout.addWidget(self.list, 1)
        order = QHBoxLayout()
        self.earlier_button = button("↑", lambda: self.move_chapter(-1))
        self.earlier_button.setAccessibleName("Move chapter earlier")
        self.later_button = button("↓", lambda: self.move_chapter(1))
        self.later_button.setAccessibleName("Move chapter later")
        self.duplicate_button = button("Duplicate", self.duplicate_chapter)
        self.remove_button = button("Remove", self.remove_chapter, "quiet")
        for control in (self.earlier_button, self.later_button, self.duplicate_button, self.remove_button):
            order.addWidget(control)
        rail_layout.addLayout(order)
        self.undo_button = button("Undo removed chapter", self.restore_removed, "quiet")
        self.undo_button.hide()
        rail_layout.addWidget(self.undo_button)
        self.loose_heading = label("EVENTS OUTSIDE CHAPTERS", "eyebrow")
        rail_layout.addWidget(self.loose_heading)
        self.loose_events = QListWidget()
        self.loose_events.setAccessibleName("Events outside chapters; activate to open")
        self.loose_events.setWordWrap(True)
        self.loose_events.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.loose_events.setMaximumHeight(150)
        self.loose_events.itemActivated.connect(lambda item: self.workshop.open_event(item.data(Qt.ItemDataRole.UserRole)))
        rail_layout.addWidget(self.loose_events)
        self.splitter.addWidget(rail)
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor = QWidget()
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(12, 0, 0, 0)
        self.empty = label("Begin with a chapter or explore a starter. Existing events can stay independent, or be linked to chapters whenever you are ready.", "muted", True)
        editor_layout.addWidget(self.empty)
        self.chapter_panel = QWidget()
        detail = QVBoxLayout(self.chapter_panel)
        detail.setContentsMargins(0, 0, 0, 0)
        self.fields = {
            "name": line("A name for this chapter", 100),
            "purpose": _prose("Why does this chapter belong in their story?"),
            "before": _prose("What do they believe or feel before this moment?"),
            "after": _prose("What changes? What remains unresolved?"),
            "player_role": _prose("What can the player witness, offer, or choose?"),
            "phase": _choices(PHASES), "hearts": number(0, 14),
        }
        purpose, purpose_layout = card()
        self.fields["purpose"].setFixedHeight(60)
        _form(purpose_layout, [("Chapter title", self.fields["name"]), ("Purpose", self.fields["purpose"])])
        suggestions = QHBoxLayout()
        for caption, key in (("Relationship phase", "phase"), ("Suggested hearts", "hearts")):
            column = QVBoxLayout()
            column.addWidget(label(caption, "hint"))
            self.fields[key].setAccessibleName(caption)
            self.fields[key].setToolTip("Chapter suggestions only. Each event keeps its own playable conditions.")
            column.addWidget(self.fields[key])
            suggestions.addLayout(column, 1)
        purpose_layout.addLayout(suggestions)
        detail.addWidget(purpose)
        self.connections, connections_layout = card("Relationships this chapter develops")
        self.arc_list = QListWidget()
        self.arc_list.setAccessibleName("Relationships associated with this chapter")
        self.arc_list.setWordWrap(True)
        self.arc_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.arc_list.setMinimumHeight(90)
        self.arc_list.setMaximumHeight(170)
        self.arc_list.itemChanged.connect(self.edit_arcs)
        connections_layout.addWidget(self.arc_list)
        connections_layout.addWidget(label("A chapter can develop several arcs. These links organize the story; they do not require every relationship’s target to appear in each scene.", "hint", True))
        parts, parts_layout = card()
        parts_heading = QHBoxLayout()
        parts_heading.addWidget(label("Playable parts", "sectionTitle"), 1)
        self.open_event_button = button("Open event", self.open_selected_event)
        parts_heading.addWidget(self.open_event_button)
        parts_layout.addLayout(parts_heading)
        self.event_list = QListWidget()
        self.event_list.setAccessibleName("Chapter events in author order; activate to open")
        self.event_list.setToolTip("A chapter can contain several separately triggered events. Activate an event to open it.")
        self.event_list.setWordWrap(True)
        self.event_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.event_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.event_list.setMinimumHeight(110)
        self.event_list.setMaximumHeight(230)
        self.event_list.itemActivated.connect(self.open_selected_event)
        self.event_list.currentItemChanged.connect(self.update_actions)
        parts_layout.addWidget(self.event_list)
        self.event_summary = label("", "muted", True)
        parts_layout.addWidget(self.event_summary)
        event_actions = QGridLayout()
        self.new_event_button = button("+ New event", self.add_event_to_chapter, "primary")
        self.unlink_button = button("Unlink", self.unlink_event, "quiet")
        self.unlink_button.setToolTip("Remove this chapter link. The event remains in the project.")
        self.event_earlier = button("↑", lambda: self.move_event(-1))
        self.event_earlier.setAccessibleName("Move chapter event earlier")
        self.event_later = button("↓", lambda: self.move_event(1))
        self.event_later.setAccessibleName("Move chapter event later")
        event_actions.addWidget(self.new_event_button, 0, 0, 1, 2)
        event_actions.addWidget(self.unlink_button, 0, 2, 1, 2)
        event_actions.addWidget(self.event_earlier, 1, 2)
        event_actions.addWidget(self.event_later, 1, 3)
        parts_layout.addLayout(event_actions)
        link_row = QHBoxLayout()
        self.event_picker = QComboBox()
        self.event_picker.setMinimumWidth(0)
        self.event_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.event_picker.setMinimumContentsLength(10)
        self.event_picker.setAccessibleName("Existing event to link to this chapter")
        link_row.addWidget(self.event_picker, 1)
        self.link_button = button("Link event", self.link_event)
        link_row.addWidget(self.link_button)
        parts_layout.addLayout(link_row)
        detail.addWidget(parts)
        self.prompts_toggle = QCheckBox("More chapter prompts")
        self.prompts_toggle.setToolTip("Optional notes about the character before and after the chapter, and the player's role.")
        detail.addWidget(self.prompts_toggle)
        self.prompts_panel, prompts_layout = card()
        _form(prompts_layout, [("Before", self.fields["before"]), ("After", self.fields["after"]),
                              ("Player’s role", self.fields["player_role"])])
        self.prompts_panel.hide()
        self.prompts_toggle.toggled.connect(self.prompts_panel.setVisible)
        detail.addWidget(self.prompts_panel)
        self.arcs_toggle = QCheckBox("Connect relationship arcs")
        self.arcs_toggle.setToolTip("Connect this chapter with one or more relationship or personal arcs.")
        detail.addWidget(self.arcs_toggle)
        self.connections.hide()
        self.arcs_toggle.toggled.connect(self.connections.setVisible)
        detail.addWidget(self.connections)
        editor_layout.addWidget(self.chapter_panel)
        editor_layout.addStretch()
        self.editor_scroll.setWidget(self.editor)
        self.splitter.addWidget(self.editor_scroll)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([265, 630])
        root.addWidget(self.splitter, 1)
        for widget in self.brief_fields.values():
            connect_change(widget, self.edit_brief)
        for widget in self.fields.values():
            connect_change(widget, self.edit_chapter)
        self.refresh_context()

    @property
    def records(self):
        return self.data["chapters"]

    @property
    def current(self):
        return next((i for i, record in enumerate(self.records) if record["id"] == self.current_chapter_id), -1)

    def chapter(self):
        return self.records[self.current] if self.current >= 0 else None

    def load(self, character):
        self.loading = True
        self.data = normalize_storyline(character.get("storyline"))
        self.removed = None
        self.cleared_starter = None
        self.undo_button.setText("Undo removed chapter")
        self.undo_button.hide()
        for key, widget in self.brief_fields.items():
            set_value(widget, self.data["brief"].get(key, ""))
        if not any(record["id"] == self.current_chapter_id for record in self.records):
            self.current_chapter_id = self.records[0]["id"] if self.records else ""
        self.loading = False
        self.refresh_context()
        self.load_chapter()

    def dump(self):
        return deepcopy(self.data)

    def toggle_brief(self):
        self.brief_scroll.setVisible(self.brief_toggle.isChecked())
        self.splitter.setVisible(not self.brief_toggle.isChecked())
        self.brief_toggle.setText("Close story brief" if self.brief_toggle.isChecked() else "Edit story brief")

    def edit_brief(self):
        if self.loading:
            return
        self.data["brief"].update({key: value(widget) for key, widget in self.brief_fields.items()})
        self.update_brief_summary()
        self.changed.emit()

    def update_brief_summary(self):
        brief = self.data["brief"]
        summary = brief.get("desire") or brief.get("question") or ""
        self.brief_summary.setText(summary)
        self.brief_summary.setVisible(bool(summary))

    def select_item(self, item, _previous=None):
        if self.loading:
            return
        self.current_chapter_id = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.load_chapter()

    def set_current_chapter(self, identity):
        if not any(record["id"] == identity for record in self.records):
            return
        self.brief_toggle.setChecked(False)
        self.current_chapter_id = identity
        self.refresh_context()
        self.load_chapter()

    def load_chapter(self):
        self.loading = True
        chapter = self.chapter()
        self.chapter_panel.setVisible(chapter is not None)
        self.empty.setVisible(chapter is None)
        if chapter is not None:
            for key, widget in self.fields.items():
                set_value(widget, chapter.get(key, 0 if key == "hearts" else ""))
            self.arcs_toggle.setChecked(bool(chapter.get("arc_ids")))
        self.update_prompt_summary()
        self.loading = False
        self.refresh_links()

    def edit_chapter(self):
        chapter = self.chapter()
        if self.loading or chapter is None:
            return
        chapter.update({key: value(widget) for key, widget in self.fields.items()})
        self.update_prompt_summary()
        self.refresh_titles()
        self.changed.emit()

    def update_prompt_summary(self):
        chapter = self.chapter() or {}
        count = sum(bool(chapter.get(key)) for key in ("before", "after", "player_role"))
        self.prompts_toggle.setText("More chapter prompts" + (f" · {count} filled" if count else ""))

    def refresh_titles(self):
        events = {entry["id"]: entry for entry in self.workshop.events.records}
        for index, chapter in enumerate(self.records):
            ready = sum(events.get(identity, {}).get("story", {}).get("stage") == "ready" for identity in chapter["event_ids"])
            count = len(chapter["event_ids"])
            phase = next((caption for caption, key in PHASES if key == chapter["phase"]), "Any phase")
            caption = f"{index + 1:02d}  {chapter['name'] or 'Untitled chapter'}\n{phase} · {chapter['hearts']} hearts\n{count} {'part' if count == 1 else 'parts'} · {ready} ready"
            item = self.list.item(index)
            if item:
                item.setText(caption)
                item.setToolTip(caption + "\nChapter suggestions; each event has its own playable conditions.")

    def refresh_context(self):
        """Refresh references without reloading text fields or moving their cursors."""
        old_loading = self.loading
        self.loading = True
        with QSignalBlocker(self.list):
            identities = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
            if identities != [record["id"] for record in self.records]:
                self.list.clear()
                for chapter in self.records:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, chapter["id"])
                    self.list.addItem(item)
            self.list.setCurrentRow(self.current)
            self.refresh_titles()
        self.update_brief_summary()
        self.loading = old_loading
        self.refresh_links()

    def refresh_links(self):
        chapter = self.chapter()
        old_loading = self.loading
        self.loading = True
        arcs = {entry["id"]: entry for entry in self.workshop.relationships.records}
        checked = chapter["arc_ids"] if chapter else []
        with QSignalBlocker(self.arc_list):
            self.arc_list.clear()
            for identity in [*arcs, *(identity for identity in checked if identity not in arcs)]:
                item = QListWidgetItem(arcs.get(identity, {}).get("name") or ("Missing relationship" if identity not in arcs else "Unnamed relationship"))
                item.setData(Qt.ItemDataRole.UserRole, identity)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if identity in checked else Qt.CheckState.Unchecked)
                self.arc_list.addItem(item)
        events = {entry["id"]: entry for entry in self.workshop.events.records}
        selected = self.event_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else ""
        linked = chapter["event_ids"] if chapter else []
        with QSignalBlocker(self.event_list):
            self.event_list.clear()
            for index, identity in enumerate(linked):
                event = events.get(identity)
                stage = STAGES.get(event.get("story", {}).get("stage", "idea"), "Draft") if event else "Missing event"
                item = QListWidgetItem(f"{index + 1}. {event.get('name') or 'Untitled event'}\n{stage} · {event.get('hearts', 0)} minimum hearts" if event else f"{index + 1}. Missing event — unlink or restore")
                item.setData(Qt.ItemDataRole.UserRole, identity)
                self.event_list.addItem(item)
                if identity == selected_id:
                    self.event_list.setCurrentItem(item)
            if self.event_list.currentRow() < 0 and linked:
                self.event_list.setCurrentRow(0)
        ready = sum(events.get(identity, {}).get("story", {}).get("stage") == "ready" for identity in linked)
        missing = sum(identity not in events for identity in linked)
        self.event_list.setFixedHeight(max(74, min(220, len(linked) * 52 + 8)))
        self.event_summary.setText(f"{ready} of {len(linked)} parts ready for export" + (f" · {missing} missing references" if missing else ""))
        self.event_summary.setToolTip("Gameplay testing is recorded in Play in Stardew.")
        picker_id = self.event_picker.currentData()
        with QSignalBlocker(self.event_picker):
            self.event_picker.clear()
            for identity, event in events.items():
                if identity not in linked:
                    self.event_picker.addItem(event.get("name") or "Untitled event", identity)
            index = self.event_picker.findData(picker_id)
            if index >= 0:
                self.event_picker.setCurrentIndex(index)
        all_linked = {identity for record in self.records for identity in record["event_ids"]}
        loose_id = self.loose_events.currentItem().data(Qt.ItemDataRole.UserRole) if self.loose_events.currentItem() else ""
        with QSignalBlocker(self.loose_events):
            self.loose_events.clear()
            for identity, event in events.items():
                if identity not in all_linked:
                    item = QListWidgetItem(event.get("name") or "Untitled event")
                    item.setData(Qt.ItemDataRole.UserRole, identity)
                    self.loose_events.addItem(item)
                    if identity == loose_id:
                        self.loose_events.setCurrentItem(item)
        self.loose_heading.setText(f"EVENTS OUTSIDE CHAPTERS ({self.loose_events.count()})")
        self.loose_events.setVisible(self.loose_events.count() > 0)
        self.loading = old_loading
        self.update_actions()

    def update_actions(self, *_):
        current = self.current
        self.chapter_panel.setVisible(current >= 0)
        self.empty.setVisible(current < 0)
        self.earlier_button.setEnabled(current > 0)
        self.later_button.setEnabled(0 <= current < len(self.records) - 1)
        self.duplicate_button.setEnabled(current >= 0 and len(self.records) < 100)
        self.remove_button.setEnabled(current >= 0)
        event_row = self.event_list.currentRow()
        item = self.event_list.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.open_event_button.setEnabled(any(entry["id"] == identity for entry in self.workshop.events.records))
        self.unlink_button.setEnabled(item is not None)
        self.event_earlier.setEnabled(event_row > 0)
        self.event_later.setEnabled(0 <= event_row < self.event_list.count() - 1)
        self.link_button.setEnabled(current >= 0 and self.event_picker.count() > 0)
        self.new_event_button.setEnabled(current >= 0 and len(self.workshop.events.records) < 100)
        origin = self.data.get("starter_origin")
        self.start_blank_button.setVisible(isinstance(origin, dict))
        if isinstance(origin, dict):
            character = self.workshop.character()
            character["storyline"] = self.dump()
            self.start_blank_button.setEnabled(untouched_story_starter(character))
        if self.cleared_starter is not None:
            self.undo_button.setEnabled(self._can_restore_starter())

    def edit_arcs(self, _item=None):
        if self.loading or self.chapter() is None:
            return
        self.chapter()["arc_ids"] = [self.arc_list.item(i).data(Qt.ItemDataRole.UserRole)
                                      for i in range(self.arc_list.count())
                                      if self.arc_list.item(i).checkState() == Qt.CheckState.Checked]
        self.changed.emit()

    def show_notice(self, message):
        self.notice.setText(message)
        self.notice.show()

    def add_chapter(self):
        if len(self.records) >= 100:
            self.show_notice("A storyline can hold up to 100 chapters.")
            return
        chapter = new_chapter()
        self.records.append(chapter)
        self.set_current_chapter(chapter["id"])
        self.changed.emit()
        self.fields["name"].setFocus()
        self.fields["name"].selectAll()

    def move_chapter(self, delta):
        index = self.current
        target = index + delta
        if index < 0 or not 0 <= target < len(self.records):
            return
        self.records[index], self.records[target] = self.records[target], self.records[index]
        self.refresh_context()
        self.changed.emit()

    def duplicate_chapter(self):
        if self.chapter() is None or len(self.records) >= 100:
            return
        chapter = deepcopy(self.chapter())
        chapter.update(id=str(uuid.uuid4()), name=chapter["name"][:93] + " (copy)", event_ids=[])
        self.records.insert(self.current + 1, chapter)
        self.set_current_chapter(chapter["id"])
        self.show_notice("The chapter notes were copied. Link or create its playable parts separately.")
        self.changed.emit()

    def remove_chapter(self):
        index = self.current
        if index < 0:
            return
        self.removed = (index, deepcopy(self.records.pop(index)))
        self.cleared_starter = None
        self.undo_button.setEnabled(True)
        self.undo_button.setText("Undo removed chapter")
        self.current_chapter_id = self.records[min(index, len(self.records) - 1)]["id"] if self.records else ""
        self.refresh_context()
        self.load_chapter()
        self.undo_button.show()
        self.show_notice("Chapter removed. Its events remain in the project.")
        self.changed.emit()

    def restore_removed(self):
        if self.cleared_starter is not None:
            if self._can_restore_starter():
                before, _after = self.cleared_starter
                self.data = deepcopy(before["storyline"])
                self.workshop.events.records = deepcopy(before["events"])
                self.workshop.events.refresh()
                self.cleared_starter = None
                self.undo_button.hide()
                if self.records:
                    self.set_current_chapter(self.records[0]["id"])
                else:
                    self.refresh_context()
                self.changed.emit()
            return
        if self.removed is None or len(self.records) >= 100:
            return
        index, chapter = self.removed
        self.records.insert(min(index, len(self.records)), chapter)
        self.removed = None
        self.undo_button.hide()
        self.set_current_chapter(chapter["id"])
        self.changed.emit()

    def _can_restore_starter(self):
        if self.cleared_starter is None:
            return False
        _before, after = self.cleared_starter
        return self.data == after["storyline"] and self.workshop.events.records == after["events"]

    def start_blank(self):
        character = self.workshop.character()
        try:
            updated = clear_story_starter(character)
        except ValueError as exc:
            self.show_notice(str(exc))
            return False
        before = {"storyline": self.dump(), "events": deepcopy(self.workshop.events.records)}
        self.data = normalize_storyline(updated["storyline"])
        self.workshop.events.records = deepcopy(updated["events"])
        self.workshop.events.refresh()
        self.cleared_starter = (before, {"storyline": self.dump(), "events": deepcopy(self.workshop.events.records)})
        self.removed = None
        self.current_chapter_id = ""
        self.brief_toggle.setChecked(False)
        self.refresh_context()
        self.load_chapter()
        self.undo_button.setText("Undo start blank")
        self.undo_button.show()
        self.show_notice("The untouched starter outline and its event drafts were removed. Use Undo to restore them.")
        self.changed.emit()
        return True

    def open_selected_event(self, item=None):
        if not isinstance(item, QListWidgetItem):
            item = self.event_list.currentItem()
        if item and any(entry["id"] == item.data(Qt.ItemDataRole.UserRole) for entry in self.workshop.events.records):
            self.workshop.open_event(item.data(Qt.ItemDataRole.UserRole))

    def link_event(self):
        chapter, identity = self.chapter(), self.event_picker.currentData()
        if chapter is None or not identity or identity in chapter["event_ids"]:
            return
        chapter["event_ids"].append(identity)
        self.refresh_context()
        self.changed.emit()

    def unlink_event(self):
        chapter = self.chapter()
        index = self.event_list.currentRow()
        if chapter is None or index < 0:
            return
        chapter["event_ids"].pop(index)
        self.refresh_context()
        self.changed.emit()

    def move_event(self, delta):
        chapter = self.chapter()
        index = self.event_list.currentRow()
        target = index + delta
        if chapter is None or index < 0 or not 0 <= target < len(chapter["event_ids"]):
            return
        chapter["event_ids"][index], chapter["event_ids"][target] = chapter["event_ids"][target], chapter["event_ids"][index]
        self.refresh_context()
        self.changed.emit()

    def add_event_to_chapter(self):
        chapter = self.chapter()
        if chapter is None or len(self.workshop.events.records) >= 100:
            return
        event = new_event(self.workshop.character())
        suffix = f" · Part {len(chapter['event_ids']) + 1}" if chapter["event_ids"] else ""
        event.update(name=(chapter["name"] or "New event")[:100 - len(suffix)] + suffix, hearts=chapter["hearts"])
        event["story"].update(stage="outline", premise=chapter["purpose"],
                                relationship=chapter["phase"] if chapter["phase"] in ("dating", "married") else "any")
        self.workshop.events.records.append(event)
        chapter["event_ids"].append(event["id"])
        self.workshop.events.refresh(len(self.workshop.events.records) - 1)
        self.refresh_context()
        self.changed.emit()
        self.workshop.open_event(event["id"])

    def open_starter(self, starter=None, relationship_id=""):
        character = self.workshop.character()
        if not isinstance(starter, str):
            starter = "romance" if character.get("romanceable") else "friendship"
        try:
            dialog = StoryStarterDialog(character, self, starter=starter, relationship_id=relationship_id)
        except ValueError as exc:
            self.show_notice(str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_starter_preview(dialog.edited_preview(), dialog.selected_chapter_ids())
        dialog.deleteLater()

    def apply_starter_preview(self, preview, chapter_ids=None):
        before_ids = {chapter["id"] for chapter in self.records}
        try:
            updated = apply_story_starter(self.workshop.character(), preview, chapter_ids=chapter_ids)
        except ValueError as exc:
            self.show_notice(str(exc))
            return False
        storyline = normalize_storyline(updated.get("storyline"))
        if storyline == self.data and updated.get("events", []) == self.workshop.events.records:
            self.show_notice("Those milestones are already in the storyline. Your existing writing is unchanged.")
            return False
        self.data = storyline
        self.workshop.events.records = deepcopy(updated.get("events", []))
        self.workshop.events.refresh()
        added = [chapter for chapter in self.records if chapter["id"] not in before_ids]
        if added:
            self.current_chapter_id = added[0]["id"]
        self.brief_toggle.setChecked(False)
        self.refresh_context()
        self.load_chapter()
        self.show_notice(f"Added {len(added)} chapter drafts. Review each event’s triggers and write its scene before marking it ready.")
        self.changed.emit()
        return True

    def open_issue(self, field):
        parts = field.split(".")
        if "chapters" in parts:
            self.brief_toggle.setChecked(False)
            position = parts.index("chapters") + 1
            if position < len(parts) and parts[position].isdigit() and int(parts[position]) < len(self.records):
                self.set_current_chapter(self.records[int(parts[position])]["id"])
            key = parts[position + 1] if position + 1 < len(parts) else "name"
            if key in ("before", "after", "player_role"):
                self.prompts_toggle.setChecked(True)
            elif key == "arc_ids":
                self.arcs_toggle.setChecked(True)
            widget = self.fields.get(key) or (self.arc_list if key == "arc_ids" else self.event_list)
            scroll = self.editor_scroll
        else:
            self.brief_toggle.setChecked(True)
            widget = self.brief_fields.get(parts[-1], self.brief_fields["desire"])
            scroll = self.brief_scroll
        widget.setFocus()
        scroll.ensureWidgetVisible(widget)
        # The tab or brief editor may just have become visible. Its viewport
        # dimensions settle on the next layout pass, after this call returns.
        QTimer.singleShot(0, self, lambda: scroll.ensureWidgetVisible(widget))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 680
        if compact != self.compact:
            self.compact = compact
            self.splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
            self.list.setMinimumWidth(0 if compact else 210)
            self.list.setMinimumHeight(150 if compact else 0)
            self.list.setMaximumHeight(210 if compact else 16777215)
            self.splitter.setSizes([250, 570] if compact else [265, 630])
