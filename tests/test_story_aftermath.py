"""Aftermath links use the real Life editors and keep planning local."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from pixelheart.app import MainWindow
from pixelheart.life_page import LifePage
from pixelheart.story_aftermath import EventAftermath
from pixelheart.theme import apply_theme
from pixelheart_core.life import COLLECTIONS, new_life_record
from pixelheart_core.projects import load_project
from pixelheart_core.story import new_beat, new_event, new_planned_effect
from tests.qt_support import QtTestCase


class EventAftermathTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.character = {"id": "test-character", "name": "Rowan", "romanceable": True,
                          "schedule": [{"id": "morning", "time": "06:00", "location": "Town",
                                        "x": 10, "y": 10, "facing": "down", "activity": "Reading"}]}
        self.event = new_event(self.character)
        self.event.update(id="first-scene", name="The first tune", hearts=8)
        self.character["events"] = [self.event]
        self.character["relationships"] = []
        self.life = LifePage()
        self.life.load(self.character)
        self.window = SimpleNamespace(life=self.life, open_life_editor=Mock())
        self.workshop = SimpleNamespace(window=self.window, character=lambda: deepcopy(self.character))
        self.widget = EventAftermath(self.workshop)
        self.widget.load(self.event, self.character)
        self.plan_changes = []
        self.life_changes = []
        self.widget.changed.connect(self.capture)
        self.life.changed.connect(lambda: self.life_changes.append(True))

    def capture(self):
        self.plan_changes.append(True)
        self.widget.capture(self.event)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        for editor in self.life.editors.values():
            editor.close()
            editor.deleteLater()
        self.life.deleteLater()
        self.app.processEvents()

    def test_viewing_and_refreshing_links_do_not_mutate_sources_or_planning(self):
        self.event["story"]["aftermath_notes"] = "Keep the quiet ending."
        effect = new_planned_effect("A letter some day")
        effect["extension"] = {"author": "Keep this"}
        self.event["story"]["planned_effects"] = [effect]
        row = new_life_record("dialogues")
        row["conditions"]["after_event_id"] = self.event["id"]
        row["text"] = "I remember that tune."
        self.life.editors["dialogues"].load([row])
        before_event, before_life = deepcopy(self.event), self.life.dump()
        self.widget.load(self.event, self.character)
        self.widget.effect_list.setCurrentRow(0)
        self.widget.refresh_context()
        self.widget.open_linked()
        self.assertEqual(self.event, before_event)
        self.assertEqual(self.life.dump(), before_life)
        self.assertEqual(self.plan_changes, [])

        self.assertEqual(self.life_changes, [])
        self.assertIn("Draft: this rule has no effect", self.widget.linked_detail.toPlainText())
        self.assertIn(row["text"], self.widget.linked_detail.toPlainText())
        self.window.open_life_editor.assert_called_once_with("dialogues")

    def test_create_all_supported_rules_as_drafts_and_open_the_real_editor(self):
        for kind in COLLECTIONS:
            with self.subTest(kind=kind):
                row = self.widget.create_rule(kind)
                editor = self.life.editors[kind]
                self.assertEqual(editor.current, 0)
                self.assertIs(editor.records[0], row)
                self.assertFalse(row["enabled"])
                self.assertEqual(row["conditions"]["after_event_id"], self.event["id"])
                self.assertEqual(row["conditions"]["min_hearts"], 0)
                self.assertEqual(row["conditions"]["relationship"], "married" if kind == "spouse_dialogue" else "any")
                self.window.open_life_editor.assert_called_with(kind)
        self.assertEqual(len(self.life_changes), 3)
        self.assertEqual(self.plan_changes, [])
        self.assertEqual(self.widget.linked.count(), 3)
        self.assertEqual(self.life.editors["routines"].records[0]["stops"], self.character["schedule"])
        self.life.editors["routines"].records[0]["stops"][0]["x"] = 55
        self.assertEqual(self.character["schedule"][0]["x"], 10)

    def test_link_existing_rule_is_explicitly_drafted_and_preserves_its_content(self):
        row = new_life_record("dialogues")
        row.update(name="Remembered melody", text="That song stayed with me.", enabled=True)
        row["conditions"]["season"] = "winter"
        row["extension"] = {"credit": "Retain"}
        other = new_life_record("dialogues")
        other["conditions"]["after_event_id"] = "different-scene"
        self.life.editors["dialogues"].load([row, other])
        self.widget.refresh_context()
        self.assertEqual(self.widget.existing.count(), 2)
        self.widget.existing.setCurrentIndex(1)
        self.widget.link_existing()
        linked = self.life.editors["dialogues"].records[0]
        self.assertFalse(linked["enabled"])
        self.assertEqual(linked["text"], row["text"])
        self.assertEqual(linked["extension"], row["extension"])
        self.assertEqual(linked["conditions"]["season"], "winter")
        self.assertEqual(linked["conditions"]["after_event_id"], self.event["id"])
        self.assertEqual(self.life.editors["dialogues"].records[1], other)
        self.assertEqual(len(self.life_changes), 1)
        self.window.open_life_editor.assert_called_once_with("dialogues")

    def test_daily_events_nonromance_spouse_and_capacity_cannot_create_links(self):
        self.event["story"]["repeat"] = "daily"
        self.widget.refresh_context()
        for kind in COLLECTIONS:
            self.assertFalse(self.widget.create_buttons[kind].isEnabled())
            self.assertIsNone(self.widget.create_rule(kind))
        self.event["story"]["repeat"] = "once"
        self.character["romanceable"] = False
        self.widget.refresh_context()
        self.assertFalse(self.widget.create_buttons["spouse_dialogue"].isEnabled())
        self.assertIsNone(self.widget.create_rule("spouse_dialogue"))
        self.life.editors["routines"].load([new_life_record("routines") for _ in range(100)])
        self.widget.refresh_context()
        self.assertFalse(self.widget.create_buttons["routines"].isEnabled())
        self.assertIsNone(self.widget.create_rule("routines"))
        self.assertEqual(len(self.life.editors["routines"].records), 100)
        self.assertEqual(self.life_changes, [])
        self.window.open_life_editor.assert_not_called()

    def test_planned_effects_roundtrip_and_refresh_never_overwrites_prose(self):
        effect = new_planned_effect("A reminder letter")
        effect["extension"] = {"draft": 2}
        self.event["story"]["planned_effects"] = [effect]
        self.event["story"]["extension"] = {"keep": "Other story metadata"}
        self.widget.load(self.event, self.character)
        self.widget.notes.setPlainText("Only a memory remains in this version.")
        self.widget.effect_description.setPlainText("A letter after the concert")
        self.widget.effect_resolution.setCurrentIndex(1)
        self.widget.refresh_context()
        self.assertEqual(self.widget.notes.toPlainText(), self.event["story"]["aftermath_notes"])
        saved = deepcopy(self.event)
        self.widget.load(saved)
        self.widget.capture(saved)
        self.assertEqual(saved, self.event)
        self.assertEqual(saved["story"]["planned_effects"][0]["id"], effect["id"])
        self.assertEqual(saved["story"]["planned_effects"][0]["resolution"], "omitted")
        self.assertEqual(saved["story"]["planned_effects"][0]["extension"], effect["extension"])
        self.widget.add_effect()
        self.assertEqual(self.event["story"]["planned_effects"][1]["resolution"], "pending")
        self.assertNotEqual(self.event["story"]["planned_effects"][0]["id"], self.event["story"]["planned_effects"][1]["id"])
        self.widget.remove_effect()
        self.assertEqual(self.event, saved)
        self.assertEqual(self.life_changes, [])

    def test_capture_cannot_overwrite_a_different_event(self):
        other = new_event(self.character)
        before = deepcopy(other)
        self.widget.capture(other)
        self.assertEqual(other, before)

    def test_enabled_but_invalid_rule_is_not_described_as_playable(self):
        row = new_life_record("dialogues")
        row["enabled"] = True
        row["conditions"]["after_event_id"] = self.event["id"]
        self.life.editors["dialogues"].load([row])
        self.widget.refresh_context()
        detail = self.widget.linked_detail.toPlainText()
        self.assertIn("Export blocked:", detail)
        self.assertIn("Write their dialogue", detail)
        self.assertNotIn("Included in export;", detail)

    def test_issue_focus_selects_the_correct_effect_without_editing_it(self):
        self.event["story"]["planned_effects"] = [new_planned_effect("First"), new_planned_effect("Second")]
        self.widget.load(self.event)
        before = deepcopy(self.event)
        target = self.widget.focus_field("events.0.story.planned_effects.1.resolution")
        self.assertIs(target, self.widget.effect_resolution)
        self.assertEqual(self.widget.effect_list.currentRow(), 1)
        self.assertEqual(self.widget.effect_description.toPlainText(), "Second")
        self.assertEqual(self.event, before)
        self.assertEqual(self.plan_changes, [])

    def test_themed_aftermath_fits_a_narrow_native_detail_pane(self):
        stylesheet, palette, font = self.app.styleSheet(), self.app.palette(), self.app.font()
        style = self.app.style().objectName()
        scroll = QScrollArea()
        try:
            apply_theme(self.app)
            self.widget.add_effect()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.widget)
            scroll.resize(460, 700)
            scroll.show()
            self.app.processEvents()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(self.widget.width(), scroll.viewport().width())
            decision = next(item for item in self.widget.findChildren(QLabel) if item.text() == "Decision")
            self.assertGreater(decision.width(), 0)
        finally:
            scroll.takeWidget()
            scroll.close()
            scroll.deleteLater()
            self.app.setStyle(style)
            self.app.setPalette(palette)
            self.app.setFont(font)
            self.app.setStyleSheet(stylesheet)


class EventAftermathHistoryTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(auto_download_icons=False, preload_story=False)
        document = deepcopy(self.window.document)
        event = new_event(document["character"])
        event.update(id="after-history", name="Shared tune")
        beat = new_beat()
        beat["text"] = "I remember that tune."
        effect = new_planned_effect("Send a letter after the concert")
        effect["extension"] = {"author_notes": ["Keep the original plan"]}
        event["story"].update(stage="outline", beats=[beat], planned_effects=[effect],
                              aftermath_notes="The next conversation remembers the tune.")
        event["story"]["extension"] = {"legacy": {"keep": True}}
        document["character"]["events"] = [event]
        reaction = new_life_record("dialogues")
        reaction.update(name="Remembered tune", text="That song stayed with me.")
        reaction["conditions"]["after_event_id"] = event["id"]
        document["character"]["life"]["dialogues"] = [reaction]
        self.window.load_document(document)

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_scene_and_life_edits_preserve_legacy_planning_through_project_undo(self):
        window = self.window
        before = window.project_snapshot()
        self.assertFalse(hasattr(window.events, "aftermath"))
        window.events.fields["name"].setText("The shared melody")
        edited = window.project_snapshot()
        self.assertEqual(edited["character"]["events"][0]["story"], before["character"]["events"][0]["story"])
        self.assertTrue(window.dirty)
        self.assertEqual(window.project_history.history.undo_count, 1)
        window.project_history.undo()
        self.assertEqual(window.project_snapshot(), before)
        self.assertFalse(window.dirty)
        window.project_history.redo()
        self.assertEqual(window.project_snapshot(), edited)

        window.open_life_editor("dialogues")
        editor = window.life.editors["dialogues"]
        editor.fields["text"].setPlainText("I still remember our melody.")
        with_reaction = window.project_snapshot()
        self.assertEqual(with_reaction["character"]["events"], edited["character"]["events"])
        self.assertEqual(editor.records[0]["conditions"]["after_event_id"], "after-history")
        self.assertFalse(editor.records[0]["enabled"])
        window.project_history.undo()
        self.assertEqual(window.project_snapshot(), edited)
        window.project_history.redo()
        self.assertEqual(window.project_snapshot(), with_reaction)

    def test_saved_pending_effect_blocks_ready_without_a_planning_editor(self):
        window = self.window
        before = window.project_snapshot()
        errors = [issue for issue in window.events.readiness_issues(window.story.character()) if issue["level"] == "error"]
        self.assertEqual([issue["field"] for issue in errors], ["story.planned_effects.0.resolution"])
        self.assertFalse(window.events.mark_ready())
        window.story.open_issue("events.0.story.planned_effects.0.resolution")
        self.assertIn("preserved", window.events.notice.text())
        self.assertEqual(window.project_snapshot(), before)
        self.assertEqual(window.project_history.history.undo_count, 0)
        self.assertFalse(window.dirty)

    def test_saved_omitted_effect_and_legacy_notes_survive_ready_and_disk_roundtrip(self):
        document = self.window.project_snapshot()
        story = document["character"]["events"][0]["story"]
        story["planned_effects"][0]["resolution"] = "omitted"
        self.window.load_document(document)
        self.assertTrue(self.window.events.mark_ready())
        ready = self.window.project_snapshot()
        for key in ("planned_effects", "aftermath_notes", "extension"):
            self.assertEqual(ready["character"]["events"][0]["story"][key], story[key])
        with tempfile.TemporaryDirectory(prefix="pixelheart-legacy-aftermath-") as directory:
            path = Path(directory) / "character.json"
            self.assertTrue(self.window.save_to(path))
            saved = load_project(path)
        self.assertEqual(saved["character"]["events"], ready["character"]["events"])
        self.assertEqual(saved["character"]["life"], ready["character"]["life"])
