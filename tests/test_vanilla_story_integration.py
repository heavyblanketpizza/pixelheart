"""Previously imported vanilla scenes remain usable in the Romance workspace."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart_core.projects import load_project, save_project, new_project
from pixelheart_core.vanilla_story import load_vanilla_story, apply_vanilla_story
from tests.qt_support import QtTestCase
from tests.test_vanilla_story import dictionary_xnb


class VanillaStoryIntegrationTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        content = self.root / "game" / "Content"
        (content / "Maps").mkdir(parents=True)
        (content / "Maps" / "Town.xnb").write_bytes(b"preview not needed in this fixture")
        (content / "Data" / "Events").mkdir(parents=True)
        self.source_script = 'none/10 10/Abigail 10 10 2 farmer 10 12 0/speak Abigail "A synthetic first meeting.$h"/pause 500/end'
        (content / "Data" / "Events" / "Town.xnb").write_bytes(dictionary_xnb({
            "501/f Abigail 500/t 900 1700": self.source_script,
            "502/f Abigail 1000/e 501": 'none/10 10/Abigail 10 10 2 farmer 10 12 0/speak Abigail "A synthetic follow-up."/end',
        }))
        self.bundle = load_vanilla_story("abigail", self.root / "game")
        self.keys = [entry["key"] for entry in self.bundle["events"] if not entry["optional"]]
        self.assertEqual(len(self.keys), 2)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False)
        self.addCleanup(self.close_window)
        self.window.open_section("story")

    def close_window(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def load_imported_project(self):
        document = new_project()
        document["character"] = apply_vanilla_story(document["character"], self.bundle, self.keys)
        self.window.load_document(document)
        self.window.open_section("story")
        return self.window.story

    def test_existing_imports_open_at_their_heart_milestones_as_drafts(self):
        story = self.load_imported_project()
        self.assertEqual([record["hearts"] for record in story.events.records], [2, 4])
        self.assertEqual(story.selected_hearts, 2)
        self.assertEqual(story.events.phases.currentIndex(), story.events.SCENE)
        self.assertTrue(story.events.source_action.isVisible())
        self.assertTrue(all(record["story"]["stage"] != "ready" for record in story.events.records))
        self.assertFalse(story.events.mark_ready())
        self.assertFalse(hasattr(story, "storyline"))
        self.assertFalse(hasattr(story, "relationships"))

    def test_original_reference_survives_editing_saving_and_reopening(self):
        story = self.load_imported_project()
        event = story.events.records[0]
        reference = deepcopy(event["story"]["vanilla_source"])
        legacy = {key: deepcopy(story.dump()[key]) for key in ("relationships", "storyline")}
        self.assertIn(self.source_script, str(reference))
        before = self.window.project_snapshot()
        story.events.fields["name"].setText("My adapted scene")
        self.assertEqual(event["story"]["vanilla_source"], reference)
        for key in legacy:
            self.assertEqual(story.dump()[key], legacy[key])
        destination = self.root / "saved" / "character.json"
        save_project(self.window.project_snapshot(), destination)
        reopened = load_project(destination)
        self.assertEqual(reopened["character"]["events"][0]["story"]["vanilla_source"], reference)
        with patch("pixelheart.vanilla_story.VanillaSourceDialog") as dialog:
            story.events.open_vanilla_source()
            dialog.assert_called_once_with(reference, story.events)
        self.window.project_history.undo()
        self.assertEqual(self.window.project_snapshot(), before)

    def test_imported_scenes_remember_their_view_without_mutating_saved_data(self):
        story = self.load_imported_project()
        before = self.window.project_snapshot()
        ids = [event["id"] for event in story.events.records]
        story.events.phases.setCurrentIndex(story.events.TRIGGER)
        story.open_event(ids[1])
        self.assertEqual(story.selected_hearts, 4)
        self.assertEqual(story.events.phases.currentIndex(), story.events.SCENE)
        story.open_event(ids[0])
        self.assertEqual(story.events.phases.currentIndex(), story.events.TRIGGER)
        self.assertEqual([story.events.phases.tabText(i) for i in range(3)], ["When", "Scene", "Preview"])
        for index in range(3):
            self.assertFalse(story.events.phases.tabIcon(index).isNull())
        self.assertEqual(self.window.project_snapshot(), before)

    def test_imported_prerequisite_blocks_removal_without_a_partial_change(self):
        story = self.load_imported_project()
        before = self.window.project_snapshot()
        story.events.remove()
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertTrue(story.events.reference_issues.count())
        story.events.open_reference_issue(story.events.reference_issues.item(0))
        self.assertEqual(story.selected_hearts, 4)
        self.assertEqual(story.events.phases.currentIndex(), story.events.TRIGGER)
