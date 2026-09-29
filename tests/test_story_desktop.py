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

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.theme import apply_theme
from pixelheart_core.projects import load_project
from pixelheart_core.story import event_game_id, new_beat, normalize_relationship
from tests.qt_support import QtTestCase


class StoryDesktopTests(QtTestCase):
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
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
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
        window = MainWindow(preload_story=False)
        self.windows.append(window)
        return window

    def load_story(self, events=(), relationships=()):
        document = deepcopy(self.window.document)
        document["character"]["events"] = deepcopy(list(events))
        document["character"]["relationships"] = deepcopy(list(relationships))
        self.window.load_document(document)
        if events:
            self.window.story.open_event(events[0]["id"])

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

        reopened.story.open_event(ready["id"])
        reopened.events.return_to_draft()
        self.assertNotEqual(reopened.events.dump()[0]["story"]["stage"], "ready")
        self.assertEqual(reopened.events.dump()[0]["id"], ready["id"])
        self.assertEqual(reopened.events.dump()[0]["story"]["beats"], ready["story"]["beats"])
        self.assertTrue(reopened.dirty)
        self.assertTrue(reopened.save())
        self.assertEqual(load_project(self.file)["character"]["events"], reopened.events.dump())
        self.errors.assert_not_called()

    def test_blank_milestone_can_be_developed_into_a_ready_scene_using_the_editor(self):
        window = self.window
        window.story.milestones.buttons[6].click()
        window.story.create_scene_button.click()
        page = window.events
        page.fields['name'].setText('A letter at the river')
        self.assertFalse(page.dump()[0]['story']['premise'])
        self.assertEqual(page.dump()[0]['story']['stage'], 'outline')
        self.assertEqual(page.dump()[0]['hearts'], 6)
        self.assertEqual(len(page.dump()[0]['story']['actors']), 2)
        page.beats.fields['text'].setPlainText('I thought you might come back. 안녕, @.$h')
        self.assertTrue(page.mark_ready())
        authored = page.dump()[0]
        self.assertEqual(authored['story']['beats'][0]['text'], 'I thought you might come back. 안녕, @.$h')
        self.assertEqual(window.document['character']['events'][0], authored)
        self.assertTrue(window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump()[0], authored)
        self.errors.assert_not_called()

    def test_legacy_note_preserves_its_authored_prose_while_scene_fields_are_edited(self):
        legacy = {'id': 'legacy-river', 'name': 'The promised letter', 'hearts': 6,
                  'location': 'Forest', 'description': '달빛 아래, they meet again.\nA promise kept.',
                  'story': {'premise': 'They want to reconnect.', 'conflict': 'Neither knows what to say.',
                            'outcome': 'They decide to write again.'}}
        self.load_story(events=[legacy])
        page = self.window.events
        before = page.dump()[0]
        self.assertEqual(before['story']['actors'], [])
        self.assertEqual(before['story']['beats'], [])
        page.fields['name'].setText('A promise beneath the trees')
        page.story_fields['time_end'].setValue(2200)
        developed = page.dump()[0]
        self.assertEqual(developed['description'], legacy['description'])
        for key in ('premise', 'conflict', 'outcome'):
            self.assertEqual(developed['story'][key], legacy['story'][key])
        self.assertEqual(developed['story']['actors'], [])
        self.assertEqual(developed['story']['beats'], [])
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)['character']['events'][0], developed)
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
        event = self.event()
        event["story"]["beats"].append(new_beat("choice"))
        self.load_story(events=[event])
        window = self.window
        window.resize(1020, 700)
        window.show()
        window.navigation.setCurrentRow(SECTION_INDEX["story"])
        self.application.processEvents()
        self.assertEqual((window.width(), window.height()), (1020, 700))
        page = window.events
        page.phases.setCurrentIndex(page.SCENE)
        self.application.processEvents()
        scroll = page.phases.widget(page.SCENE)
        self.assertIsInstance(scroll, QScrollArea)
        cases = [
            ("events.0.story.beats.0.text", lambda: page.beats.fields["text"], 0),
            ("events.0.story.beats.1.choices.1.text", lambda: page.beats.choice_fields[1]["text"], 0),
            ("events.0.story.actors.1.x", lambda: page.actors.table.cellWidget(1, 1), 0),
            ("events.0.story.actors", lambda: page.actors.table, 0),
            ("events.0.story.time_end", lambda: page.story_fields["time_end"], scroll.verticalScrollBar().maximum()),
        ]
        for field, get_widget, initial_scroll in cases:
            with self.subTest(field=field):
                scroll.verticalScrollBar().setValue(initial_scroll)
                page.phases.setCurrentIndex(0)
                window.story.milestones.buttons[14].click()
                window.story.open_issue(field)
                self.application.processEvents()
                widget = get_widget()
                expected_phase = page.TRIGGER if field.endswith("time_end") else page.SCENE
                self.assertEqual(page.phases.currentIndex(), expected_phase)
                scroll = page.phases.currentWidget()
                target_rect = QRect(widget.mapTo(scroll.viewport(), QPoint(0, 0)), widget.size())
                self.assertTrue(scroll.viewport().rect().contains(target_rect.center()))
                self.assertTrue(widget.isVisible())
                parent = widget.parentWidget()
                while parent is not None:
                    if isinstance(parent, QScrollArea):
                        center = widget.mapTo(parent.viewport(), widget.rect().center())
                        self.assertTrue(parent.viewport().rect().contains(center),
                                        f"{field} remains clipped inside a nested editor")
                    parent = parent.parentWidget()
        self.assertFalse(window.dirty)

    def test_all_event_phases_fit_minimum_window_and_rehearsal_is_visible(self):
        self.load_story(events=[self.event()])
        window = self.window
        window.resize(1000, 700)
        window.show()
        window.open_section("story")
        window.story.open_event("river")
        page = window.events
        for phase in range(page.phases.count()):
            with self.subTest(phase=phase):
                page.phases.setCurrentIndex(phase)
                self.application.processEvents()
                self.application.processEvents()
                self.assertEqual(page.phases.currentWidget().horizontalScrollBar().maximum(), 0)
        self.assertTrue(page.rehearsal_text.isVisibleTo(page.phases.currentWidget()))
        self.assertIn("I'll write again tomorrow.", page.rehearsal_text.text())
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

    def test_missing_saved_arc_stays_preserved_and_blocks_ready_promotion(self):
        event = self.event()
        event['story']['arc_ids'] = ['missing-arc']
        self.load_story(events=[event])
        page = self.window.events
        before = page.dump()
        self.assertFalse(page.ready_button.isEnabled())
        self.assertFalse(page.mark_ready())
        self.window.story.open_issue('events.0.story.arc_ids.0')
        self.assertFalse(page.notice.isHidden())
        self.assertIn('preserved', page.notice.text())
        self.assertEqual(page.dump(), before)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)['character']['events'][0]['story']['arc_ids'], ['missing-arc'])
        self.errors.assert_not_called()

    def test_saved_pending_effect_blocks_export_and_survives_scene_edits(self):
        from pixelheart_core.story import new_planned_effect
        event = self.event()
        event['story']['planned_effects'] = [new_planned_effect('Send a letter three days later')]
        self.load_story(events=[event])
        page = self.window.events
        self.assertFalse(page.mark_ready())
        self.window.story.open_issue('events.0.story.planned_effects.0.resolution')
        self.assertIn('preserved', page.notice.text())
        page.fields['name'].setText('A letter to remember')
        self.assertFalse(page.mark_ready())
        self.assertTrue(self.window.save_to(self.file))
        saved = load_project(self.file)['character']['events'][0]
        self.assertEqual(saved['story']['planned_effects'], event['story']['planned_effects'])
        self.assertEqual(saved['story']['planned_effects'][0]['resolution'], 'pending')
        self.errors.assert_not_called()

    def test_scene_edit_preserves_every_saved_arc_and_its_existing_stage(self):
        arcs = [self.relationship(f'arc-{index}') for index in range(11)]
        arcs[10]['story']['stage'] = 'ready'
        event = self.event(stage='ready')
        event['story']['arc_ids'] = [arcs[1]['id']]
        self.load_story(events=[event], relationships=arcs)
        self.window.events.fields['name'].setText('A revised scene')
        self.assertEqual(self.window.story.dump()['relationships'], arcs)
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)['character']['relationships'], [normalize_relationship(arc) for arc in arcs])

    def test_multiple_saved_arcs_do_not_require_absent_people_in_the_cast(self):
        arcs = [self.relationship('family'), self.relationship('ambition')]
        arcs[0]['story']['target'] = 'George'
        arcs[1]['story'].update(kind='personal', target='')
        event = self.event()
        event['story']['arc_ids'] = [arc['id'] for arc in arcs]
        self.load_story(events=[event], relationships=arcs)
        page = self.window.events
        self.assertTrue(page.mark_ready())
        self.assertEqual(page.dump()[0]['story']['actors'], event['story']['actors'])
        page.return_to_draft()
        self.assertEqual(self.window.story.dump()['relationships'], arcs)

    def test_returning_scene_to_draft_keeps_saved_relationship_data_unchanged(self):
        relationship = self.relationship()
        relationship['story']['stage'] = 'ready'
        self.load_story(events=[self.event(stage='ready', relationship=relationship['id'])], relationships=[relationship])
        self.window.events.return_to_draft()
        self.assertEqual(self.window.story.dump()['relationships'], [relationship])
        self.assertNotEqual(self.window.events.dump()[0]['story']['stage'], 'ready')
        self.assertTrue(self.window.events.mark_ready())
        self.assertEqual(self.window.story.dump()['relationships'], [relationship])
        self.assertTrue(self.window.save_to(self.file))
        self.errors.assert_not_called()

    def test_adding_heart_scene_leaves_saved_relationships_and_chapters_unchanged(self):
        self.load_story(relationships=[self.relationship()])
        before = self.window.story.dump()
        self.window.story.milestones.buttons[8].click()
        self.window.story.create_scene_button.click()
        events = self.window.events.dump()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['hearts'], 8)
        for key in ('relationship_id', 'previous_event_id'):
            self.assertEqual(events[0]['story'][key], '')
        self.assertEqual(events[0]['story']['arc_ids'], [])
        self.assertEqual(self.window.story.dump()['relationships'], before['relationships'])
        self.assertEqual(self.window.story.dump()['storyline'], before['storyline'])
        self.assertTrue(self.window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.events.dump(), events)
        self.assertEqual(reopened.story.dump()['relationships'], load_project(self.file)['character']['relationships'])
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
        original_relationships = self.window.story.dump()["relationships"]
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
        self.assertEqual(self.window.story.dump()["relationships"], original_relationships)
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

    def test_saved_relationship_link_survives_scene_title_edits(self):
        self.load_story(events=[self.event(relationship='friendship')], relationships=[self.relationship()])
        before = self.window.story.dump()['relationships']
        self.window.events.fields['name'].setText('Letters between friends')
        self.assertEqual(self.window.story.dump()['relationships'], before)
        self.assertEqual(self.window.events.dump()[0]['story']['relationship_id'], 'friendship')
        self.assertTrue(self.window.save_to(self.file))
        self.assertEqual(load_project(self.file)['character']['events'][0]['story']['relationship_id'], 'friendship')

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
        page.phases.setCurrentIndex(page.REHEARSE)
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

    def test_continued_typing_stays_on_the_chosen_multipart_scene(self):
        self.load_story(events=[self.event("first", "ready"), self.event("second", "ready")])
        page = self.window.events
        self.assertEqual(self.window.story.parts.count(), 2)
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

    def test_renaming_a_multipart_scene_does_not_redirect_further_typing(self):
        first, second = self.event("first"), self.event("second")
        first["name"], second["name"] = "Needle first", "Needle second"
        self.load_story(events=[first, second])
        page = self.window.events
        self.assertEqual(self.window.story.parts.count(), 2)
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

    def test_saved_relationship_target_remains_a_cast_readiness_requirement(self):
        relationship = self.relationship()
        relationship['story']['target'] = 'Robin'
        self.load_story(events=[self.event(relationship='friendship')], relationships=[relationship])
        page = self.window.events
        page.phases.setCurrentIndex(page.REHEARSE)
        self.assertFalse(page.ready_button.isEnabled())
        cast_checks = [page.checks.item(index) for index in range(page.checks.count())
                       if page.checks.item(index).data(Qt.ItemDataRole.UserRole) == 'story.actors']
        self.assertTrue(any('Robin' in item.text() for item in cast_checks))
        self.assertFalse(page.mark_ready())
        self.assertEqual(self.window.story.dump()['relationships'], [relationship])
        self.assertTrue(self.window.save_to(self.file))

    def test_selecting_milestones_phases_and_parts_does_not_dirty_the_project(self):
        self.load_story(events=[self.event('first'), self.event('second')],
                        relationships=[self.relationship('first-link'), self.relationship('second-link')])
        window = self.window
        before = deepcopy(window.document)
        window.navigation.setCurrentRow(SECTION_INDEX["story"])
        for hearts in (2, 4, 6, 8, 10, 14, 4):
            window.story.milestones.buttons[hearts].click()
        for index in range(window.events.phases.count()):
            window.events.phases.setCurrentIndex(index)
        window.story.parts.setCurrentIndex(1)
        window.story.parts.setCurrentIndex(0)
        self.application.processEvents()
        self.assertFalse(window.dirty)
        self.assertEqual(window.document, before)

    def test_issue_navigation_opens_the_affected_event_and_scene(self):
        invalid = self.event('second', 'ready')
        invalid['story']['beats'][0]['text'] = ''
        self.load_story(events=[self.event('first'), invalid])
        window = self.window
        window.story.milestones.buttons[14].click()
        window.export_page.refresh()
        items = [window.export_page.list.item(index) for index in range(window.export_page.list.count())]
        issue = next(item for item in items if item.data(Qt.ItemDataRole.UserRole)['field'] == 'events.1.story.beats.0.text')
        window.export_page.open_issue(issue)
        self.assertEqual(window.navigation.currentRow(), SECTION_INDEX["story"])
        self.assertEqual(window.story.selected_hearts, 4)
        self.assertEqual(window.events.current, 1)
        self.assertEqual(window.events.phases.currentIndex(), window.events.SCENE)
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

    def test_legacy_story_notes_survive_browsing_and_scene_edits_without_losing_metadata(self):
        self.load_story(events=[
            {'id': 'old-event', 'name': 'The river letter', 'hearts': 4, 'location': 'Forest',
             'description': 'A letter left beneath the willow.', 'extension': {'credit': '은하', 'revision': 2}},
            {'id': 'other-event', 'name': 'The reply', 'hearts': 6, 'location': 'Town', 'description': 'A promise for another spring.'},
        ], relationships=[{'id': 'old-relationship', 'name': 'Leah', 'relation': 'Friend',
                            'description': 'They share a sketchbook.', 'extension': {'history': [1, 2]}}])
        window = self.window
        original_events = window.events.dump()
        original_relationships = window.story.dump()['relationships']
        window.navigation.setCurrentRow(SECTION_INDEX["story"])
        window.story.milestones.buttons[6].click()
        window.story.milestones.buttons[4].click()
        self.application.processEvents()
        self.assertFalse(window.dirty)
        self.assertEqual(window.events.dump(), original_events)
        self.assertEqual(window.story.dump()['relationships'], original_relationships)
        window.events.fields['name'].setText('달빛 아래, a second letter.')
        self.assertTrue(window.save_to(self.file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        events = reopened.events.dump()
        self.assertEqual(events[0]['id'], 'old-event')
        self.assertEqual(events[0]['name'], '달빛 아래, a second letter.')
        self.assertEqual(events[0]['description'], original_events[0]['description'])
        self.assertEqual(events[0]['extension'], {'credit': '은하', 'revision': 2})
        self.assertEqual(events[1], original_events[1])
        self.assertEqual(reopened.story.dump()['relationships'], load_project(self.file)['character']['relationships'])
        self.assertEqual(reopened.story.dump()['relationships'][0]['extension'], {'history': [1, 2]})
        self.assertFalse(reopened.dirty)
        self.errors.assert_not_called()


if __name__ == "__main__":
    unittest.main()
