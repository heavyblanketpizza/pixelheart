"""Heart milestones over the project's original, ordered event collection."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMenu, QVBoxLayout, QWidget

from pixelheart_core.romance import ROMANCE_HEARTS, new_romance_event
from pixelheart_core.story import exported_npc_id
from pixelheart_core.world import cast_actor_id
from .actor_picker import vanilla_actors
from .romance_milestones import HeartMilestones
from .story_page import EventsPage
from .widgets import button, card, label


class RomancePage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.loading = False
        self.selected_hearts = 2
        self.legacy_relationships = []
        self.legacy_storyline = {}
        self._last_parts = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.milestones = HeartMilestones()
        root.addWidget(self.milestones)
        self.parts_row = QWidget()
        row = QHBoxLayout(self.parts_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.parts_label = label("Scene", "muted")
        row.addWidget(self.parts_label)
        self.parts = QComboBox()
        self.parts.setAccessibleName("Scene at this heart milestone")
        self.parts.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.parts.setMinimumContentsLength(14)
        row.addWidget(self.parts, 1)
        row.addStretch()
        self.add_scene_button = button("Add scene", self.add_scene, "quiet")
        self.add_scene_button.setAccessibleName("Add a scene at this heart milestone")
        self.other_events_button = button("Other saved events", lambda: None, "quiet")
        self.other_events_button.setAccessibleName("Other saved events")
        self.other_events_menu = QMenu(self.other_events_button)
        self.other_events_button.setMenu(self.other_events_menu)
        row.addWidget(self.other_events_button)
        root.addWidget(self.parts_row)

        self.events = EventsPage(self, romance=True)
        self.events.heading.insertWidget(self.events.heading.count() - 1, self.add_scene_button)
        # Keep notices visible even when an empty milestone hides the editor.
        root.addWidget(self.events.notice)
        root.addWidget(self.events, 1)
        self.empty, empty_layout = card()
        empty_layout.addStretch()
        self.empty_title = label("Your 2-heart scene", "sectionTitle", True)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(label("Create a scene when you're ready to write this moment.", "muted", True))
        self.create_scene_button = button("Create scene", self.add_scene, "primary")
        self.create_scene_button.setAccessibleName("Create the first scene at this heart milestone")
        empty_layout.addWidget(self.create_scene_button, 0, Qt.AlignmentFlag.AlignLeft)
        empty_layout.addStretch()
        root.addWidget(self.empty, 1)

        self.milestones.selected.connect(self.select_heart)
        self.parts.currentIndexChanged.connect(self._choose_part)
        self.events.list.currentRowChanged.connect(self._selection_changed)
        self.events.changed.connect(self.on_change)
        self._refresh_navigation()

    def character(self):
        character = deepcopy(self.window.document["character"])
        character.update(self.window.identity.dump())
        character.update(self.dump())
        if hasattr(self.window, "life"):
            character["life"] = self.window.life.dump()
        return character

    def dump(self):
        return {"events": self.events.dump() if hasattr(self, "events") else [],
                "relationships": deepcopy(self.legacy_relationships),
                "storyline": deepcopy(self.legacy_storyline)}

    def load(self, character):
        self.loading = True
        self.legacy_relationships = deepcopy(character.get("relationships", []))
        self.legacy_storyline = deepcopy(character.get("storyline", {}))
        self.events.load(character.get("events", []))
        self.loading = False
        if self.selected_hearts in ROMANCE_HEARTS:
            self.select_heart(self.selected_hearts)
        else:
            self._selection_changed(self.events.current)
        self.refresh_context()

    def _current_event(self):
        return self.events.records[self.events.current] if 0 <= self.events.current < len(self.events.records) else None

    def _heart_records(self, hearts):
        return [record for record in self.events.records if type(record.get("hearts")) is int and record["hearts"] == hearts]

    def select_heart(self, hearts):
        if hearts not in ROMANCE_HEARTS:
            return
        self.selected_hearts = hearts
        records = self._heart_records(hearts)
        identity = self._last_parts.get(hearts)
        target = next((record for record in records if record["id"] == identity), records[0] if records else None)
        if target is not None:
            self.open_event(target["id"])
        else:
            self.events.list.setCurrentRow(-1)
            self._refresh_navigation()

    def open_event(self, identity):
        index = next((index for index, record in enumerate(self.events.records) if record["id"] == identity), -1)
        if index < 0:
            return
        self.events.search.clear()
        self.events.filter.setCurrentIndex(0)
        self.events.list.setCurrentRow(index)
        self._selection_changed(index)

    def _selection_changed(self, index):
        if self.loading:
            return
        if 0 <= index < len(self.events.records):
            event = self.events.records[index]
            hearts = event.get("hearts")
            self.selected_hearts = hearts if type(hearts) is int and hearts in ROMANCE_HEARTS else None
            if self.selected_hearts is not None:
                self._last_parts[hearts] = event["id"]
        self._refresh_navigation()

    def _choose_part(self, index):
        identity = self.parts.itemData(index)
        if identity is not None:
            self.open_event(identity)

    def _refresh_navigation(self):
        records = self.events.records
        counts = Counter(record.get("hearts") for record in records if type(record.get("hearts")) is int)
        self.milestones.set_counts(counts)
        self.milestones.set_current(self.selected_hearts)
        current = self._current_event()
        parts = self._heart_records(self.selected_hearts) if self.selected_hearts is not None else []
        with QSignalBlocker(self.parts):
            self.parts.clear()
            for index, record in enumerate(parts):
                self.parts.addItem(f"{index + 1}. {record.get('name') or 'Untitled scene'}", record["id"])
            self.parts.setCurrentIndex(self.parts.findData(current["id"]) if current else -1)
        self.parts.setVisible(len(parts) > 1)
        self.parts_label.setVisible(len(parts) > 1)
        extras = [record for record in records if type(record.get("hearts")) is not int or record["hearts"] not in ROMANCE_HEARTS]
        self.other_events_menu.clear()
        for record in extras:
            title = f"{record.get('hearts', 0)} hearts · {record.get('name') or 'Untitled scene'}"
            action = self.other_events_menu.addAction(title, lambda _checked=False, identity=record["id"]: self.open_event(identity))
            action.setCheckable(True)
            action.setChecked(current is not None and current["id"] == record["id"])
        self.other_events_button.setText(f"Other saved events ({len(extras)})")
        self.other_events_button.setVisible(bool(extras))
        self.parts_row.setVisible(len(parts) > 1 or bool(extras))
        can_add = self.selected_hearts in ROMANCE_HEARTS and len(records) < 100
        self.add_scene_button.setEnabled(can_add)
        self.create_scene_button.setEnabled(can_add)
        self.add_scene_button.setToolTip("Choose a heart milestone to add a scene." if self.selected_hearts is None else
                                       "This project already has 100 events." if len(records) >= 100 else "")
        self.empty_title.setText(f"Your {self.selected_hearts}-heart scene" if self.selected_hearts is not None else "Choose a heart milestone")
        self.empty.setVisible(current is None)
        self.events.setVisible(current is not None)
        self.events.empty.hide()

    def add_scene(self):
        if self.selected_hearts not in ROMANCE_HEARTS:
            return
        if len(self.events.records) >= 100:
            self.events.show_notice("This project can hold up to 100 events.")
            return
        record = new_romance_event(self.character(), self.selected_hearts)
        self.events.records.append(record)
        self.events.search.clear()
        self.events.filter.setCurrentIndex(0)
        self.events.refresh(len(self.events.records) - 1)
        self.events.phases.setCurrentIndex(self.events.SCENE)
        self.events.changed.emit()
        self.events.fields["name"].setFocus()
        self.events.fields["name"].selectAll()

    def on_change(self):
        if self.loading:
            return
        self._selection_changed(self.events.current)
        self.refresh_context()
        self.changed.emit()

    def refresh_context(self):
        character = self.character()
        options = [(character.get("name") or "Your character", "$npc"), ("Farmer", "farmer")]
        actor_names = {actor.get("name") for event in self.events.records for actor in event["story"]["actors"]}
        for companion in self.window.document.get("world", {}).get("characters", []):
            npc = companion["character"]
            caption = (npc.get("name") or "Supporting character") + " · your cast"
            options.append((caption, cast_actor_id(companion)))
            options.extend((caption, identity) for identity in (npc.get("internal_name"), exported_npc_id(npc))
                           if identity in actor_names)
        options.extend((name, name) for name in vanilla_actors())
        self.events.actors.set_options(options)
        self.events.refresh_actor_choices()
        self.events.refresh_links()
        self.events.update_preview()
        self._refresh_navigation()
        self.sync_workspace_presentation()

    def open_issue(self, field):
        parts = field.split(".")
        if parts[0] == "events" and len(parts) > 1 and parts[1].isdigit():
            index = int(parts[1])
            if 0 <= index < len(self.events.records):
                self.open_event(self.events.records[index]["id"])
                self.events.focus_field(field)
                return
        self.events.show_notice("This check refers to saved chapter or relationship settings. They remain in your project, but have no editor in Romance.")

    def sync_workspace_presentation(self):
        window = self.window
        if hasattr(window, "section_pages") and window.stack.currentWidget() is window.section_pages.get("story"):
            window.title_label.hide()
            window.subtitle.hide()
