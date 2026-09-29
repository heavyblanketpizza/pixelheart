"""The main window saves each change quietly and keeps undo working."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from pixelheart.app import MainWindow
from pixelheart.autosave import autosave_enabled
from pixelheart_core.projects import ProjectError
from tests.qt_support import QtTestCase


class AutosaveDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))
        self.history = self.window.project_history.history
        self.path = self.root / "project" / "character.json"
        self.name = self.window.identity.fields["name"]

    def tearDown(self):
        self.window.autosave.timer.stop()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def saved_project(self):
        self.assertTrue(self.window.save_to(self.path))

    def on_disk(self):
        return json.loads(self.path.read_text())["character"]["name"]

    def pause(self):
        self.assertTrue(self.window.autosave.pending())
        self.window.autosave.timer.timeout.emit()

    def test_an_edit_is_saved_after_a_pause_and_undo_survives(self):
        self.saved_project()
        self.name.setText("Mira")
        self.assertEqual(self.window.save_state.text(), "Saving…")
        self.assertTrue(self.window.save_button.isHidden())
        self.pause()
        self.assertEqual(self.on_disk(), "Mira")
        self.assertFalse(self.window.dirty)
        self.assertEqual(self.window.save_state.text(), "All changes saved")
        self.assertTrue(self.window.save_button.isHidden())
        self.assertTrue(self.history.can_undo)

    def test_undo_after_an_automatic_save_is_saved_too(self):
        self.saved_project()
        self.name.setText("Mira")
        self.pause()
        self.name.setText("Rin")
        self.pause()
        self.window.project_history.undo()
        self.assertEqual(self.name.text(), "Mira")
        self.assertTrue(self.window.dirty)
        self.pause()
        self.assertEqual(self.on_disk(), "Mira")
        self.assertFalse(self.window.dirty)

    def test_loading_a_project_does_not_schedule_a_save(self):
        self.saved_project()
        self.window.open_path(self.path)
        self.assertFalse(self.window.autosave.pending())

    def test_failed_autosave_keeps_file_and_offers_retry(self):
        self.saved_project()
        before = self.path.read_bytes()
        self.name.setText("Mira")
        with patch("pixelheart.app.save_project", side_effect=ProjectError("Disk full")):
            self.pause()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.window.save_state.text(), "Couldn't save your changes")
        self.assertIn("Disk full", self.window.save_state.toolTip())
        self.assertFalse(self.window.save_button.isHidden())
        self.assertEqual(self.window.save_button.text(), "Try again")
        self.assertTrue(self.window.dirty)
        self.errors.assert_not_called()
        self.window.save_button.click()
        self.assertEqual(self.on_disk(), "Mira")
        self.assertEqual(self.window.save_state.text(), "All changes saved")
        self.assertTrue(self.window.save_button.isHidden())

    def test_save_command_saves_now_and_keeps_undo(self):
        self.saved_project()
        self.name.setText("Mira")
        self.assertTrue(self.window.save())
        self.assertFalse(self.window.autosave.pending())
        self.assertEqual(self.on_disk(), "Mira")
        self.assertTrue(self.history.can_undo)

    def test_closing_saves_without_asking(self):
        self.saved_project()
        self.name.setText("Mira")
        with patch("pixelheart.app.QMessageBox.warning") as question:
            self.assertTrue(self.window.close())
        question.assert_not_called()
        self.assertEqual(self.on_disk(), "Mira")

    def test_closing_after_a_failed_save_asks(self):
        self.saved_project()
        self.name.setText("Mira")
        with (patch("pixelheart.app.save_project", side_effect=ProjectError("Disk full")),
              patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Cancel) as question):
            self.assertFalse(self.window.close())
        question.assert_called_once()
        self.assertIn("Disk full", question.call_args.args[2])

    def test_going_to_the_background_saves(self):
        self.saved_project()
        self.name.setText("Mira")
        self.window._application_state_changed(Qt.ApplicationState.ApplicationInactive)
        self.assertEqual(self.on_disk(), "Mira")

    def test_autosave_off_keeps_classic_save(self):
        self.saved_project()
        action = self.window.autosave_action
        self.assertTrue(action.isCheckable())
        self.assertTrue(action.isChecked())
        action.setChecked(False)
        self.assertFalse(autosave_enabled())
        self.name.setText("Mira")
        self.assertFalse(self.window.autosave.pending())
        self.assertEqual(self.window.save_state.text(), "Unsaved changes")
        self.assertFalse(self.window.save_button.isHidden())
        self.assertEqual(self.window.save_button.text(), "Save")
        self.assertTrue(self.window.save())
        self.assertEqual(self.on_disk(), "Mira")
        self.assertFalse(self.history.can_undo)
        self.assertEqual(self.window.save_state.text(), "Saved locally")

    def test_turning_autosave_back_on_saves_pending_changes(self):
        self.saved_project()
        self.window.autosave_action.setChecked(False)
        self.name.setText("Mira")
        self.window.autosave_action.setChecked(True)
        self.pause()
        self.assertEqual(self.on_disk(), "Mira")

    def test_without_a_project_file_nothing_is_written(self):
        self.name.setText("Mira")
        self.assertFalse(self.window.autosave.pending())
        self.assertEqual(self.window.save_state.text(), "Unsaved changes")
        self.assertFalse(self.window.save_button.isHidden())
        self.assertEqual(self.window.save_button.text(), "Save")
        self.assertFalse(self.path.exists())
