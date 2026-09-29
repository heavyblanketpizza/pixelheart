"""Explicit new-project stories never leak into imports or erase authored work."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from pixelheart.storyline_page import StorylinePage
from pixelheart_core.life import new_life_record
from pixelheart_core.projects import (
    ProjectError, new_project, save_project, load_project,
    untouched_story_starter, clear_story_starter,
)
from pixelheart_core.story import compile_story
from tests.qt_support import QtTestCase
from tests.test_storyline_desktop import WorkshopStub


class StoryPreloadTests(unittest.TestCase):
    def test_default_factory_remains_blank_and_explicit_preload_contains_only_drafts(self):
        blank = new_project()["character"]
        self.assertEqual(blank["events"], [])
        self.assertEqual(blank["storyline"]["chapters"], [])
        loaded = new_project(story_starter="romance")["character"]
        self.assertEqual(len(loaded["events"]), 6)
        self.assertEqual([chapter["hearts"] for chapter in loaded["storyline"]["chapters"]], [2, 4, 6, 8, 10, 14])
        self.assertTrue(all(event["story"]["stage"] == "outline" for event in loaded["events"]))
        self.assertTrue(all(not event["story"]["previous_event_id"] for event in loaded["events"]))
        self.assertEqual(compile_story(loaded), [])
        self.assertTrue(untouched_story_starter(loaded))

    def test_preloaded_identities_are_independent_and_survive_roundtrip(self):
        first, second = new_project(story_starter="romance"), new_project(story_starter="romance")
        self.assertNotEqual(first["character"]["events"][0]["id"], second["character"]["events"][0]["id"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            save_project(first, path)
            loaded = load_project(path)
        self.assertEqual(loaded, first)
        self.assertTrue(untouched_story_starter(loaded["character"]))

    def test_existing_explicitly_empty_and_legacy_missing_storylines_stay_empty(self):
        variants = [new_project(), new_project(), {"name": "Existing", "internal_name": "Existing"}]
        variants[1]["character"].pop("storyline")
        with tempfile.TemporaryDirectory() as folder:
            for index, raw in enumerate(variants):
                path = Path(folder) / f"existing-{index}.json"
                path.write_text(json.dumps(raw))
                character = load_project(path)["character"]
                self.assertEqual(character["events"], [])
                self.assertEqual(character["storyline"]["chapters"], [])
                self.assertFalse(untouched_story_starter(character))

    def test_start_blank_retains_identity_and_unrelated_authoring(self):
        character = new_project(story_starter="romance")["character"]
        character.update(name="An authored character", bio="Keep this backstory", custom={"keep": True})
        character["gifts"]["love"] = ["(O)74"]
        original = deepcopy(character)
        cleared = clear_story_starter(character)
        self.assertEqual(character, original)
        self.assertEqual(cleared["events"], [])
        self.assertEqual(cleared["storyline"]["chapters"], [])
        self.assertNotIn("starter_origin", cleared["storyline"])
        for key in set(character) - {"events", "storyline"}:
            self.assertEqual(cleared[key], character[key])

    def test_any_authored_story_change_blocks_bulk_clear_without_modifying_data(self):
        base = new_project(story_starter="romance")["character"]
        for label, mutate in (
            ("event dialogue", lambda c: c["events"][0]["story"]["beats"][0].update(text="My own words")),
            ("event trigger", lambda c: c["events"][0].update(hearts=3)),
            ("chapter purpose", lambda c: c["storyline"]["chapters"][0].update(purpose="A different purpose")),
            ("brief", lambda c: c["storyline"]["brief"].update(desire="My own desire")),
            ("chapter extension", lambda c: c["storyline"]["chapters"][0].update(custom={"authored": True})),
            ("reordered chapter", lambda c: c["storyline"]["chapters"].reverse()),
        ):
            with self.subTest(change=label):
                character = deepcopy(base)
                mutate(character)
                before = deepcopy(character)
                self.assertFalse(untouched_story_starter(character))
                with self.assertRaises(ProjectError):
                    clear_story_starter(character)
                self.assertEqual(character, before)

    def test_even_draft_daily_life_references_protect_starter_events(self):
        for kind in ("dialogues", "routines", "spouse_dialogue"):
            with self.subTest(kind=kind):
                character = new_project(story_starter="romance")["character"]
                rule = new_life_record(kind)
                rule["conditions"]["after_event_id"] = character["events"][0]["id"]
                character["life"] = {kind: [rule]}
                self.assertFalse(rule["enabled"])
                self.assertFalse(untouched_story_starter(character))
                with self.assertRaises(ProjectError):
                    clear_story_starter(character)


class StoryPreloadDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.character = new_project(story_starter="romance")["character"]
        self.workshop = WorkshopStub(self.character)
        self.page = StorylinePage(self.workshop)
        self.workshop.page = self.page
        self.page.load(self.character)
        self.page.changed.connect(self.page.refresh_context)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.processEvents()

    def test_start_blank_and_local_undo_restore_exact_starter_identities(self):
        original = self.workshop.character()
        self.assertTrue(self.page.start_blank_button.isEnabled())
        self.assertTrue(self.page.start_blank())
        self.assertEqual(self.page.records, [])
        self.assertEqual(self.workshop.events.records, [])
        self.assertTrue(self.page._can_restore_starter())
        self.page.restore_removed()
        self.assertEqual(self.workshop.character(), original)
        self.assertTrue(self.page.start_blank_button.isEnabled())

    def test_authored_changes_disable_bulk_clear_but_keep_normal_removal(self):
        self.page.fields["purpose"].setPlainText("A purpose written by the author.")
        self.assertFalse(self.page.start_blank_button.isEnabled())
        before = self.workshop.character()
        self.assertFalse(self.page.start_blank())
        self.assertEqual(self.workshop.character(), before)
        self.assertTrue(self.page.remove_button.isEnabled())

    def test_local_restore_cannot_overwrite_work_written_after_start_blank(self):
        self.page.start_blank()
        self.page.add_chapter()
        self.page.fields["name"].setText("My new beginning")
        before = self.workshop.character()
        self.assertFalse(self.page._can_restore_starter())
        self.page.restore_removed()
        self.assertEqual(self.workshop.character(), before)

    def test_loading_an_empty_project_does_not_preload_or_offer_bulk_removal(self):
        self.workshop.events.records = []
        self.page.load(new_project()["character"])
        self.assertEqual(self.page.records, [])
        self.assertTrue(self.page.start_blank_button.isHidden())


if __name__ == "__main__":
    unittest.main()
