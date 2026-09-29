"""Undo restores content without losing a heart milestone, scene, or routine stop."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from copy import deepcopy
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart_core.projects import new_project
from pixelheart_core.romance import new_romance_event
from pixelheart_core.story import new_relationship
from pixelheart_core.story_planning import new_chapter
from tests.qt_support import QtTestCase


class ProjectHistoryContextTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pixelheart-history-context-")))
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False, preload_story=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_heart_milestone_scene_part_and_editor_step_survive_undo_and_redo(self):
        window = self.window
        document = new_project()
        character = document["character"]
        character["events"] = [new_romance_event(character, hearts) for hearts in (2, 6, 6)]
        arc = new_relationship()
        arc["story"]["desire"] = "Keep this existing relationship writing."
        character["relationships"] = [arc]
        chapter = new_chapter("An existing chapter")
        chapter.update(event_ids=[character["events"][2]["id"]], arc_ids=[arc["id"]])
        character["storyline"]["chapters"] = [chapter]
        character["storyline"]["extension"] = {"keep": "legacy metadata"}
        window.load_document(document)
        window.open_section("story")
        window.story.milestones.buttons[6].click()
        window.story.parts.setCurrentIndex(1)
        identity = window.events.records[window.events.current]["id"]
        window.events.phases.setCurrentIndex(window.events.SCENE)
        baseline = window.project_snapshot()
        window.project_history.close_group()
        window.events.beats.fields["text"].setPlainText("Meet beneath the trees.")
        edited = window.project_snapshot()

        for direction in ("undo", "redo"):
            self.assertTrue(getattr(window.project_history, direction)())
            self.assertEqual(window.events.phases.currentIndex(), window.events.SCENE)
            self.assertEqual(window.story.selected_hearts, 6)
            self.assertTrue(window.story.milestones.buttons[6].isChecked())
            self.assertEqual(window.story.parts.currentData(), identity)
            self.assertEqual(window.events.records[window.events.current]["id"], identity)
            self.assertEqual(window.project_snapshot(), baseline if direction == "undo" else edited)
            self.assertEqual(window.story.dump()["relationships"], character["relationships"])
            self.assertEqual(window.story.dump()["storyline"], character["storyline"])
        self.assertEqual(window.events.beats.fields["text"].toPlainText(), "Meet beneath the trees.")
        self.errors.assert_not_called()

    def test_conditional_schedule_selection_survives_undo_and_removal_uses_that_stop(self):
        window = self.window
        window.open_life_editor("routines")
        routine = window.life.editors["routines"]
        routine.add()
        routine.schedule.add()
        routine.schedule.table.setCurrentCell(1, 5)
        first_id, second_id = (record["id"] for record in routine.schedule.records)
        routine.schedule.table.cellWidget(1, 5).setText("Read under the tree.")

        self.assertTrue(window.project_history.undo())
        self.assertEqual(routine.schedule.table.currentRow(), 1)
        self.assertEqual(routine.schedule.table.currentColumn(), 5)
        self.assertEqual(routine.schedule.records[1]["id"], second_id)
        routine.schedule.remove()
        self.assertEqual([record["id"] for record in routine.schedule.records], [first_id])
        self.assertTrue(window.project_history.undo())
        self.assertEqual([record["id"] for record in routine.schedule.records], [first_id, second_id])
        self.errors.assert_not_called()

    def test_redo_keeps_the_same_scene_part_selected_after_renaming(self):
        window = self.window
        window.open_section("story")
        window.story.milestones.buttons[6].click()
        window.story.create_scene_button.click()
        window.events.fields["name"].setText("Forest meeting")
        first_id = window.events.records[0]["id"]
        window.story.add_scene_button.click()
        window.events.fields["name"].setText("Forest walk")
        second_id = window.events.records[1]["id"]
        window.story.parts.setCurrentIndex(window.story.parts.findData(first_id))
        before = deepcopy(window.events.dump())
        window.project_history.close_group()
        window.events.fields["name"].setText("Town meeting")
        self.assertEqual(window.events.current, 0)
        self.assertEqual(window.story.parts.currentData(), first_id)

        self.assertTrue(window.project_history.undo())
        self.assertEqual(window.events.dump(), before)
        self.assertTrue(window.project_history.redo())
        self.assertEqual(window.story.selected_hearts, 6)
        self.assertEqual(window.story.parts.currentData(), first_id)
        self.assertEqual(window.events.records[window.events.current]["id"], first_id)
        self.assertFalse(window.events.list.item(0).isHidden())
        window.events.beats.fields["text"].setPlainText("Continue editing the same event.")
        self.assertEqual(window.events.records[0]["story"]["beats"][0]["text"], "Continue editing the same event.")
        self.assertEqual(window.events.records[1]["id"], second_id)
        self.assertEqual(window.events.records[1]["name"], "Forest walk")
        self.errors.assert_not_called()
