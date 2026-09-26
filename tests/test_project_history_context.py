"""Undo restores content without discarding filters or the active routine stop."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from tests.qt_support import QtTestCase


class ProjectHistoryContextTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pixelheart-history-context-")))
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.window = MainWindow(auto_download_icons=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_story_search_and_stage_filters_survive_undo_and_redo(self):
        window = self.window
        window.open_section("story")
        window.events.add()
        window.events.fields["name"].setText("Forest meeting")
        window.relationships.add()
        window.relationships.fields["name"].setText("Forest friends")
        for editor in (window.events, window.relationships):
            editor.search.setText("Forest")
            editor.filter.setCurrentIndex(editor.filter.findData("idea"))
        window.story.tabs.setCurrentIndex(0)
        window.events.fields["description"].setPlainText("Meet beneath the trees.")

        for direction in ("undo", "redo"):
            self.assertTrue(getattr(window.project_history, direction)())
            self.assertEqual(window.story.tabs.currentIndex(), 0)
            for editor in (window.events, window.relationships):
                self.assertEqual(editor.search.text(), "Forest")
                self.assertEqual(editor.filter.currentData(), "idea")
                self.assertEqual(editor.list.currentRow(), 0)
                self.assertFalse(editor.list.item(0).isHidden())
        self.assertEqual(window.events.fields["description"].toPlainText(), "Meet beneath the trees.")
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

    def test_redo_keeps_the_edited_story_selected_when_its_name_no_longer_matches_search(self):
        window = self.window
        window.open_section("story")
        window.events.add()
        window.events.fields["name"].setText("Forest meeting")
        first_id = window.events.records[0]["id"]
        window.events.add()
        window.events.fields["name"].setText("Forest walk")
        window.events.search.setText("Forest")
        window.events.list.setCurrentRow(0)
        window.events.fields["name"].setText("Town meeting")
        self.assertEqual(window.events.current, 0)

        self.assertTrue(window.project_history.undo())
        self.assertTrue(window.project_history.redo())
        self.assertEqual(window.events.search.text(), "Forest")
        self.assertEqual(window.events.records[window.events.current]["id"], first_id)
        self.assertFalse(window.events.list.item(0).isHidden())
        window.events.fields["description"].setPlainText("Continue editing the same event.")
        self.assertEqual(window.events.records[0]["description"], "Continue editing the same event.")
        self.assertEqual(window.events.records[1]["name"], "Forest walk")
        self.errors.assert_not_called()
