"""Integration coverage for turning story notes into playable NPC content."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtWidgets import QApplication, QScrollArea

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart_core.projects import load_project
from pixelheart_core.story import event_game_id


class StoryDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-story-test-")
        self.root = Path(self.temporary.name)
        self.file = self.root / "project" / "character.json"
        self.windows = []
        self.error_patch = patch.object(MainWindow, "show_error")
        self.errors = self.error_patch.start()
        self.window = self.make_window()

    def tearDown(self):
        for window in reversed(self.windows):
            window.dirty = False
            window.close()
            window.deleteLater()
        self.application.processEvents()
        self.error_patch.stop()
        self.temporary.cleanup()

    def make_window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def load_story(self, events=(), relationships=()):
        document = deepcopy(self.window.document)
        document["character"]["events"] = deepcopy(list(events))
        document["character"]["relationships"] = deepcopy(list(relationships))
        self.window.load_document(document)

    @staticmethod
    def event(entry_id="river", stage="scene", previous="", relationship=""):
        return {
            "id": entry_id, "name": "The river letter", "hearts": 4,
            "location": "Town", "description": "A quiet conversation by the river.",
            "story": {
                "stage": stage, "premise": "A letter arrives.",
                "conflict": "An old friend has stopped writing.",
                "outcome": "They choose to try again.",
                "relationship_id": relationship, "previous_event_id": previous,
                "season": "any", "weather": "any", "time_start": 600,
                "time_end": 1800, "music": "none",
                "actors": [
                    {"id": entry_id + "-npc", "name": "$npc", "x": 32, "y": 62, "facing": 2},
                    {"id": entry_id + "-farmer", "name": "farmer", "x": 32, "y": 64, "facing": 0},
                ],
                "beats": [
                    {"id": entry_id + "-line", "kind": "dialogue", "actor": "$npc",
                     "text": "I'll write again tomorrow. 안녕, @.$h"},
                ],
            },
        }

    @staticmethod
    def relationship(entry_id="friendship"):
        return {
            "id": entry_id, "name": "A friendship in letters", "relation": "Friend",
            "description": "Two people learning to stay in touch.",
            "story": {
                "stage": "idea", "target": "farmer", "desire": "To be understood.",
                "tension": "They struggle to trust another person.",
                "progression": "They trade letters and meet at the river.",
                "resolution": "They promise to keep writing.",
            },
        }

    def test_readiness_can_be_saved_reopened_and_returned_to_a_draft(self):
        self.load_story(events=[self.event()])
        page = self.window.events
        page.mark_ready()
        ready = page.dump()[0]
        self.assertEqual(ready["story"]["stage"], "ready")
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump(), [ready])
        self.assertFalse(reopened.dirty)

        reopened.events.return_to_draft()
        self.assertNotEqual(reopened.events.dump()[0]["story"]["stage"], "ready")
        self.assertEqual(reopened.events.dump()[0]["id"], ready["id"])
        self.assertEqual(reopened.events.dump()[0]["story"]["beats"], ready["story"]["beats"])
        self.assertTrue(reopened.dirty)
        self.assertTrue(reopened.save())
        self.assertEqual(load_project(self.file)["character"]["events"], reopened.events.dump())
        self.errors.assert_not_called()

    def test_template_can_be_developed_into_a_ready_scene_using_the_editor(self):
        window = self.window
        page = window.events
        page.add("first_meeting")
        page.fields["name"].setText("A letter at the river")
        self.assertTrue(page.dump()[0]["story"]["premise"])
        self.assertEqual(page.dump()[0]["story"]["stage"], "outline")
        self.assertEqual(len(page.dump()[0]["story"]["actors"]), 2)
        page.phases.setCurrentIndex(1)
        page.next_step()
        self.assertEqual(page.phases.currentIndex(), 2)
        page.beats.fields["text"].setPlainText("I thought you might come back. 안녕, @.$h")
        page.mark_ready()
        authored = page.dump()[0]
        self.assertEqual(authored["story"]["stage"], "ready")
        self.assertEqual(authored["story"]["beats"][0]["text"], "I thought you might come back. 안녕, @.$h")
        self.assertEqual(window.document["character"]["events"][0], authored)
        self.assertTrue(window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump()[0], authored)
        self.errors.assert_not_called()

    def test_legacy_note_gains_a_starter_scene_without_replacing_its_authored_prose(self):
        legacy = {
            "id": "legacy-river", "name": "The promised letter", "hearts": 6,
            "location": "Forest", "description": "달빛 아래, they meet again.\nA promise kept.",
        }
        self.load_story(events=[legacy])
        page = self.window.events
        self.assertEqual(page.dump()[0]["story"]["actors"], [])
        self.assertEqual(page.dump()[0]["story"]["beats"], [])
        prose = {"premise": "They want to reconnect.", "conflict": "Neither knows what to say.",
                 "outcome": "They decide to write again."}
        for key, text in prose.items():
            page.story_fields[key].setPlainText(text)
        page.phases.setCurrentIndex(1)
        page.next_step()
        developed = page.dump()[0]
        self.assertEqual(page.phases.currentIndex(), 2)
        self.assertEqual({key: developed[key] for key in legacy}, legacy)
        self.assertEqual({key: developed["story"][key] for key in prose}, prose)
        self.assertEqual(developed["story"]["stage"], "scene")
        self.assertEqual([actor["name"] for actor in developed["story"]["actors"]], ["$npc", "farmer"])
        self.assertEqual(len(developed["story"]["beats"]), 1)
        self.assertEqual(developed["story"]["beats"][0]["kind"], "dialogue")
        self.assertEqual(developed["story"]["beats"][0]["text"], "")
        self.assertEqual(page.beats.list.count(), 1)
        self.assertEqual(page.actors.table.rowCount(), 2)
        page.phases.setCurrentIndex(1)
        page.next_step()
        self.assertEqual(page.dump()[0], developed)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"][0], developed)
        self.errors.assert_not_called()

    def test_beat_editor_preserves_core_boundary_values_when_editing_another_field(self):
        bounds = {"x": (-100, 100), "y": (-100, 100), "duration": (1, 60000),
                  "amount": (-1000, 1000), "emote": (0, 100)}
        actor_name = "ModNpc_" + "a" * 185
        event = self.event()
        event["story"]["actors"].append({"id": "mod-actor", "name": actor_name,
                                           "x": 35, "y": 64, "facing": 3})
        event["story"]["beats"] = [
            {"id": f"boundary-{index}", "kind": "dialogue", "actor": actor_name,
             "text": "A boundary-safe line.", **{key: values[index] for key, values in bounds.items()}}
            for index in range(2)
        ]
        self.load_story(events=[event])
        page = self.window.events
        for key, limits in bounds.items():
            self.assertEqual((page.beats.fields[key].minimum(), page.beats.fields[key].maximum()), limits)
        self.assertEqual(page.beats.fields["actor"].maxLength(), 192)
        for index in range(2):
            with self.subTest(boundary=index):
                page.beats.list.setCurrentRow(index)
                for key, limits in bounds.items():
                    self.assertEqual(page.beats.fields[key].value(), limits[index])
                self.assertEqual(page.beats.fields["actor"].text(), actor_name)
                page.beats.fields["text"].setPlainText(f"A revised boundary {index}.")
                changed = page.dump()[0]["story"]["beats"][index]
                self.assertEqual(changed["actor"], actor_name)
                self.assertEqual({key: changed[key] for key in bounds}, {key: limits[index] for key, limits in bounds.items()})
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"], page.dump())
        self.errors.assert_not_called()

    def test_issue_navigation_scrolls_the_target_editor_into_view_at_minimum_size(self):
        self.load_story(events=[self.event()])
        window = self.window
        window.resize(1020, 700)
        window.show()
        window.navigation.setCurrentRow(4)
        self.application.processEvents()
        self.assertEqual((window.width(), window.height()), (1020, 700))
        page = window.events
        page.phases.setCurrentIndex(2)
        self.application.processEvents()
        scroll = page.phases.widget(2)
        self.assertIsInstance(scroll, QScrollArea)
        cases = [
            ("events.0.story.beats.0.text", page.beats.fields["text"], 0),
            ("events.0.story.actors.1.x", page.actors.table.cellWidget(1, 1), 0),
            ("events.0.story.actors", page.actors.table, 0),
            ("events.0.story.time_end", page.story_fields["time_end"], scroll.verticalScrollBar().maximum()),
        ]
        for field, widget, initial_scroll in cases:
            with self.subTest(field=field):
                scroll.verticalScrollBar().setValue(initial_scroll)
                page.phases.setCurrentIndex(0)
                window.story.open_issue(field)
                self.application.processEvents()
                self.assertEqual(page.phases.currentIndex(), 2)
                target_rect = QRect(widget.mapTo(scroll.viewport(), QPoint(0, 0)), widget.size())
                self.assertTrue(scroll.viewport().rect().contains(target_rect.center()))
                self.assertTrue(widget.isVisible())
        self.assertFalse(window.dirty)

    def test_incomplete_scene_stays_a_draft_and_remains_saveable(self):
        event = self.event()
        event["story"]["beats"] = []
        self.load_story(events=[event])
        self.window.events.mark_ready()
        self.assertNotEqual(self.window.events.dump()[0]["story"]["stage"], "ready")
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"][0]["story"]["beats"], [])
        self.errors.assert_not_called()

    def test_returning_a_milestone_to_draft_reopens_its_ready_relationship(self):
        relationship = self.relationship()
        relationship["story"]["stage"] = "ready"
        self.load_story(events=[self.event(stage="ready", relationship=relationship["id"])],
                        relationships=[relationship])
        self.window.events.return_to_draft()
        self.assertEqual(self.window.relationships.dump()[0]["story"]["stage"], "outline")
        self.assertFalse(self.window.relationships.ready_button.isEnabled())
        self.window.events.mark_ready()
        self.assertTrue(self.window.relationships.ready_button.isEnabled())
        self.assertTrue(self.window.relationships.mark_ready())
        self.window.relationships.return_to_draft()
        self.assertEqual(self.window.relationships.dump()[0]["story"]["stage"], "outline")
        self.assertEqual(self.window.events.dump()[0]["story"]["stage"], "ready")
        self.assertTrue(self.window.save_to(self.file))
        self.errors.assert_not_called()

    def test_relationship_arc_generation_is_linked_ordered_and_idempotent(self):
        self.load_story(relationships=[self.relationship()])
        page = self.window.relationships
        page.create_arc()
        events = self.window.events.dump()
        self.assertEqual(len(events), 4)
        self.assertEqual([event["hearts"] for event in events], [2, 4, 6, 8])
        self.assertEqual(len({event["id"] for event in events}), 4)
        for index, event in enumerate(events):
            self.assertEqual(event["story"]["relationship_id"], "friendship")
            self.assertEqual(event["story"]["previous_event_id"], events[index - 1]["id"] if index else "")
            self.assertNotEqual(event["story"]["stage"], "ready")
        self.assertEqual(page.linked_events.count(), 4)

        page.create_arc()
        self.assertEqual(self.window.events.dump(), events)
        self.assertTrue(self.window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump(), events)
        self.assertEqual(reopened.relationships.linked_events.count(), 4)
        self.errors.assert_not_called()

    def test_duplicate_ready_event_has_fresh_identities_and_no_prerequisite(self):
        relationship = self.relationship()
        relationship["story"]["stage"] = "ready"
        self.load_story(
            events=[self.event("first", "ready", relationship="friendship"),
                    self.event("second", "ready", previous="first", relationship="friendship")],
            relationships=[relationship],
        )
        page = self.window.events
        original_relationships = self.window.relationships.dump()
        page.list.setCurrentRow(1)
        original = deepcopy(page.dump()[1])
        page.duplicate()
        duplicated = page.dump()[2]
        self.assertEqual(page.dump()[1], original)
        self.assertNotEqual(duplicated["id"], original["id"])
        self.assertNotEqual(event_game_id(duplicated, self.window.document["character"]),
                            event_game_id(original, self.window.document["character"]))
        self.assertEqual(duplicated["story"]["stage"], "idea")
        self.assertEqual(duplicated["story"]["previous_event_id"], "")
        self.assertEqual(duplicated["story"]["relationship_id"], "")
        self.assertEqual(self.window.relationships.dump(), original_relationships)
        self.assertEqual(duplicated["story"]["beats"][0]["text"], original["story"]["beats"][0]["text"])
        for key in ("actors", "beats"):
            self.assertTrue(
                {item["id"] for item in duplicated["story"][key]}.isdisjoint(
                    item["id"] for item in original["story"][key]
                )
            )
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"], page.dump())
        self.errors.assert_not_called()

    def test_referenced_event_cannot_be_deleted_and_unreferenced_removal_can_be_undone(self):
        self.load_story(events=[self.event("first"), self.event("second", previous="first")])
        page = self.window.events
        original = page.dump()
        page.list.setCurrentRow(0)
        page.remove()
        self.assertEqual(page.dump(), original)
        self.assertFalse(self.window.dirty)

        page.list.setCurrentRow(1)
        page.remove()
        self.assertEqual([event["id"] for event in page.dump()], ["first"])
        self.assertTrue(self.window.dirty)
        page.restore_removed()
        self.assertEqual(page.dump(), original)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"], original)
        self.errors.assert_not_called()

    def test_linked_relationship_cannot_be_removed_while_its_events_still_use_it(self):
        self.load_story(events=[self.event(relationship="friendship")], relationships=[self.relationship()])
        before = self.window.relationships.dump()
        self.window.relationships.remove()
        self.assertEqual(self.window.relationships.dump(), before)
        self.assertEqual(self.window.events.dump()[0]["story"]["relationship_id"], "friendship")
        self.assertFalse(self.window.dirty)

    def test_removed_cast_and_beats_restore_their_order_identity_and_authored_properties(self):
        event = self.event()
        event["story"]["actors"][1]["extension"] = {"placement_note": "Keep the path clear"}
        event["story"]["beats"][0]["extension"] = {"revision": 3}
        event["story"]["beats"].append({"id": "friendship-reward", "kind": "friendship", "actor": "$npc", "amount": 100})
        self.load_story(events=[event])
        page = self.window.events
        original = page.dump()[0]
        page.beats.list.setCurrentRow(0)
        page.beats.remove()
        self.assertEqual([beat["id"] for beat in page.dump()[0]["story"]["beats"]], ["friendship-reward"])
        page.beats.restore_removed()
        self.assertEqual(page.dump()[0]["story"]["beats"], original["story"]["beats"])
        page.actors.table.setCurrentCell(1, 0)
        page.actors.remove()
        self.assertEqual([actor["name"] for actor in page.dump()[0]["story"]["actors"]], ["$npc"])
        page.actors.restore_removed()
        self.assertEqual(page.dump()[0], original)
        self.assertTrue(self.window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump()[0], original)
        self.errors.assert_not_called()

    def test_rehearsal_and_compiled_preview_do_not_promote_or_change_the_scene(self):
        event = self.event()
        event["story"]["beats"].extend([
            {"id": "step", "kind": "move", "actor": "$npc", "x": 1, "y": 0, "facing": 1},
            {"id": "trust", "kind": "friendship", "actor": "$npc", "amount": 25},
        ])
        self.load_story(events=[event])
        page = self.window.events
        before = page.dump()
        page.phases.setCurrentIndex(3)
        page.update_preview()
        self.assertIn("I'll write again tomorrow. 안녕, Farmer.", page.rehearsal_text.text())
        self.assertNotIn("$h", page.rehearsal_text.text())
        self.assertFalse(page.previous_beat.isEnabled())
        page.step_rehearsal(1)
        self.assertIn("moves 1 tiles", page.rehearsal_text.text())
        page.step_rehearsal(1)
        self.assertIn("+25", page.rehearsal_text.text())
        self.assertFalse(page.next_beat.isEnabled())
        page.restart_rehearsal()
        self.assertIn("BEAT 1 OF 3", page.rehearsal_counter.text())
        page.show_script.setChecked(True)
        compiled = json.loads(page.script_preview.toPlainText())
        self.assertEqual(compiled[0]["Target"], "Data/Events/Town")
        self.assertEqual(len(compiled[0]["Entries"]), 1)
        self.assertEqual(page.dump(), before)
        self.assertFalse(self.window.dirty)

    def test_editing_a_ready_scene_requires_another_review_before_export(self):
        self.load_story(events=[self.event(stage="ready")])
        page = self.window.events
        page.beats.fields["text"].setPlainText("The revised ending.$h")
        self.assertEqual(page.dump()[0]["story"]["stage"], "scene")
        self.assertTrue(self.window.dirty)
        page.mark_ready()
        self.assertEqual(page.dump()[0]["story"]["stage"], "ready")
        self.assertEqual(page.dump()[0]["story"]["beats"][0]["text"], "The revised ending.$h")

    def test_continued_typing_under_the_ready_filter_stays_on_the_original_event(self):
        self.load_story(events=[self.event("first", "ready"), self.event("second", "ready")])
        page = self.window.events
        page.filter.setCurrentIndex(page.filter.findData("ready"))
        page.list.setCurrentRow(0)
        untouched = deepcopy(page.dump()[1])
        page.beats.fields["text"].setPlainText("A revised first line.")
        page.beats.fields["text"].appendPlainText("A second line for the same event.")
        self.assertEqual(page.current, 0)
        self.assertEqual(page.dump()[0]["id"], "first")
        self.assertEqual(page.dump()[0]["story"]["stage"], "scene")
        self.assertEqual(page.dump()[0]["story"]["beats"][0]["text"],
                         "A revised first line.\nA second line for the same event.")
        self.assertEqual(page.dump()[1], untouched)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"], page.dump())
        self.errors.assert_not_called()

    def test_renaming_a_search_result_does_not_redirect_further_typing_to_another_event(self):
        first, second = self.event("first"), self.event("second")
        first["name"], second["name"] = "Needle first", "Needle second"
        self.load_story(events=[first, second])
        page = self.window.events
        page.search.setText("needle")
        page.list.setCurrentRow(0)
        untouched = deepcopy(page.dump()[1])
        page.fields["name"].setText("Renamed moment")
        page.fields["name"].insert(" together")
        self.assertEqual(page.current, 0)
        self.assertEqual(page.dump()[0]["name"], "Renamed moment together")
        self.assertEqual(page.dump()[1], untouched)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)["character"]["events"], page.dump())
        self.errors.assert_not_called()

    def test_changing_a_relationship_target_refreshes_its_linked_scene_checks(self):
        self.load_story(events=[self.event(relationship="friendship")], relationships=[self.relationship()])
        window = self.window
        page = window.events
        page.phases.setCurrentIndex(3)
        self.assertTrue(page.ready_button.isEnabled())
        window.story.tabs.setCurrentWidget(window.relationships)
        window.relationships.story_fields["target"].setText("Robin")
        window.story.tabs.setCurrentWidget(page)
        self.assertFalse(page.ready_button.isEnabled())
        cast_checks = [page.checks.item(index) for index in range(page.checks.count())
                       if page.checks.item(index).data(Qt.ItemDataRole.UserRole) == "story.actors"]
        self.assertTrue(any("Robin" in item.text() for item in cast_checks))
        self.assertFalse(page.mark_ready())
        self.assertEqual(page.dump()[0]["story"]["stage"], "scene")
        window.relationships.story_fields["target"].setText("farmer")
        self.assertTrue(page.ready_button.isEnabled())

    def test_selecting_story_tabs_phases_and_records_does_not_dirty_the_project(self):
        self.load_story(
            events=[self.event("first"), self.event("second")],
            relationships=[self.relationship("first-link"), self.relationship("second-link")],
        )
        window = self.window
        before = deepcopy(window.document)
        window.navigation.setCurrentRow(4)
        for index in range(window.story.tabs.count()):
            window.story.tabs.setCurrentIndex(index)
        for index in range(window.events.phases.count()):
            window.events.phases.setCurrentIndex(index)
        window.events.list.setCurrentRow(1)
        window.relationships.list.setCurrentRow(1)
        window.events.list.setCurrentRow(0)
        window.relationships.list.setCurrentRow(0)
        self.application.processEvents()
        self.assertFalse(window.dirty)
        self.assertEqual(window.document, before)

    def test_issue_navigation_opens_the_affected_event_and_scene(self):
        invalid = self.event("second", "ready")
        invalid["story"]["beats"][0]["text"] = ""
        self.load_story(events=[self.event("first"), invalid])
        window = self.window
        window.story.tabs.setCurrentWidget(window.relationships)
        window.events.phases.setCurrentIndex(0)
        window.export_page.refresh()
        items = [window.export_page.list.item(index) for index in range(window.export_page.list.count())]
        issue = next(item for item in items if item.data(Qt.ItemDataRole.UserRole)["field"] == "events.1.story.beats.0.text")
        window.export_page.open_issue(issue)
        self.assertEqual(window.navigation.currentRow(), 4)
        self.assertIs(window.story.tabs.currentWidget(), window.events)
        self.assertEqual(window.events.current, 1)
        self.assertEqual(window.events.phases.currentIndex(), 2)
        self.assertEqual(window.events.beats.list.currentRow(), 0)
        self.assertFalse(window.dirty)

    def test_ready_scene_exports_from_the_desktop_while_another_idea_stays_in_the_project(self):
        self.load_story(events=[self.event("playable"), self.event("idea", "idea")])
        window = self.window
        window.events.list.setCurrentRow(0)
        window.events.mark_ready()
        self.assertEqual(window.events.dump()[0]["story"]["stage"], "ready")
        self.assertTrue(window.save_to(self.file))
        for kind, dimensions in (("portrait", (128, 192)), ("sprite", (64, 416))):
            source = self.root / f"{kind}.png"
            with Image.new("RGBA", dimensions, (160, 90, 140, 255)) as image:
                image.save(source)
            with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
                window.artwork.upload(kind)
        self.assertEqual([issue for issue in window.validate_project() if issue["level"] == "error"], [])

        destination = self.root / "story.zip"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                patch("pixelheart.app.QMessageBox.information"):
            self.assertTrue(window.export_project())
        with zipfile.ZipFile(destination) as archive:
            content = json.loads(archive.read("[CP] NewCharacter/content.json"))
            archived = json.loads(archive.read("[CP] NewCharacter/project.json"))["character"]
        event_patches = [change for change in content["Changes"] if change.get("Target") == "Data/Events/Town"]
        self.assertEqual(len(event_patches), 1)
        self.assertEqual(len(event_patches[0]["Entries"]), 1)
        key, script = next(iter(event_patches[0]["Entries"].items()))
        self.assertTrue(key.startswith(event_game_id(archived["events"][0], archived)))
        self.assertIn("I'll write again tomorrow.", script)
        self.assertEqual(len(archived["events"]), 2)
        self.assertEqual(archived["events"][1]["story"]["stage"], "idea")
        self.errors.assert_not_called()

    def test_legacy_story_notes_survive_browsing_and_editing_without_losing_metadata(self):
        self.load_story(
            events=[
                {"id": "old-event", "name": "The river letter", "hearts": 4,
                 "location": "Forest", "description": "A letter left beneath the willow.",
                 "extension": {"credit": "은하", "revision": 2}},
                {"id": "other-event", "name": "The reply", "hearts": 6,
                 "location": "Town", "description": "A promise for another spring."},
            ],
            relationships=[
                {"id": "old-relationship", "name": "Leah", "relation": "Friend",
                 "description": "They share a sketchbook.", "extension": {"history": [1, 2]}},
            ],
        )
        window = self.window
        original_events = window.events.dump()
        original_relationships = window.relationships.dump()
        window.navigation.setCurrentRow(4)
        window.events.list.setCurrentRow(1)
        window.events.list.setCurrentRow(0)
        self.application.processEvents()
        self.assertFalse(window.dirty)
        self.assertEqual(window.events.dump(), original_events)
        self.assertEqual(window.relationships.dump(), original_relationships)

        window.events.fields["description"].setPlainText("달빛 아래, a second letter.")
        window.relationships.fields["relation"].setText("Painting partner")
        self.assertTrue(window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        events = reopened.events.dump()
        relationships = reopened.relationships.dump()
        self.assertEqual(events[0]["id"], "old-event")
        self.assertEqual(events[0]["description"], "달빛 아래, a second letter.")
        self.assertEqual(events[0]["extension"], {"credit": "은하", "revision": 2})
        self.assertEqual(events[1], original_events[1])
        self.assertEqual(relationships[0]["id"], "old-relationship")
        self.assertEqual(relationships[0]["extension"], {"history": [1, 2]})
        self.assertEqual(relationships[0]["relation"], "Painting partner")
        self.assertFalse(reopened.dirty)
        self.errors.assert_not_called()


if __name__ == "__main__":
    unittest.main()
