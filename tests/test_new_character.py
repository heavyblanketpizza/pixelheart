"""A new character needs only a name; Pixelheart picks the folder."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.new_character import NewCharacterDialog
from tests.qt_support import QtTestCase


class NewCharacterTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch.dict(os.environ, {"PIXELHEART_LIBRARY": str(self.folder / "Pixelheart")}))
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()

    def accept(self, name, romance=True):
        def run(dialog):
            dialog.name_field.setText(name)
            dialog.romance.setChecked(romance)
            return QDialog.DialogCode.Accepted
        return patch("pixelheart.new_character.NewCharacterDialog.exec", new=run)

    def test_dialog_requires_a_name(self):
        dialog = NewCharacterDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.create_button.isEnabled())
        dialog.name_field.setText("   ")
        self.assertFalse(dialog.create_button.isEnabled())
        dialog.name_field.setText("  Mira ")
        self.assertTrue(dialog.create_button.isEnabled())
        self.assertEqual(dialog.values(), ("Mira", True))

    def test_new_character_is_saved_into_the_library_without_asking_where(self):
        with self.accept("Mira"), patch("pixelheart.app.QFileDialog.getSaveFileName",
                                        side_effect=AssertionError("no save dialog")):
            self.assertTrue(self.window.start_new_character())
        expected = self.folder / "Pixelheart" / "Mira" / "character.json"
        self.assertEqual(self.window.project_file, expected)
        self.assertTrue(expected.is_file())
        character = self.window.document["character"]
        self.assertEqual((character["name"], character["internal_name"], character["romanceable"]), ("Mira", "Mira", True))
        self.assertTrue(character["events"], "new characters start with heart event drafts")
        self.assertFalse(self.window.dirty)
        self.assertEqual(self.window.recent_projects()[0], str(expected))
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["overview"])

    def test_second_character_with_same_name_gets_its_own_folder(self):
        with self.accept("Mira"):
            self.window.start_new_character()
        with self.accept("Mira", romance=False):
            self.window.start_new_character()
        self.assertEqual(self.window.project_file.parent.name, "Mira 2")
        self.assertFalse(self.window.document["character"]["romanceable"])

    def test_cancel_keeps_the_current_project(self):
        before = self.window.document["character"]["id"]
        with patch("pixelheart.new_character.NewCharacterDialog.exec", return_value=QDialog.DialogCode.Rejected):
            self.assertFalse(self.window.start_new_character())
        self.assertEqual(self.window.document["character"]["id"], before)

    def test_unsaved_changes_are_offered_before_starting_over(self):
        self.window.dirty = True
        with patch.object(self.window, "confirm_discard", return_value=False) as confirm, \
             patch("pixelheart.new_character.NewCharacterDialog.exec", side_effect=AssertionError("no dialog")):
            self.assertFalse(self.window.start_new_character())
        confirm.assert_called_once()

    def test_unwritable_library_falls_back_to_choosing_a_folder(self):
        blocked = self.folder / "blocked"
        blocked.write_text("a file, not a folder")
        with patch.dict(os.environ, {"PIXELHEART_LIBRARY": str(blocked)}), self.accept("Mira"), \
             patch.object(self.window, "show_error"), \
             patch.object(self.window, "save_as", return_value=False) as save_as:
            self.assertTrue(self.window.start_new_character())
        save_as.assert_called_once()
        self.assertEqual(self.window.document["character"]["name"], "Mira")

    def test_opening_and_saving_remember_recent_projects(self):
        path = self.folder / "Elsewhere" / "character.json"
        self.assertTrue(self.window.save_to(path))
        self.assertEqual(self.window.recent_projects(), [str(path)])
        self.window.forget_project(path)
        self.assertEqual(self.window.recent_projects(), [])
        self.assertTrue(self.window.open_path(path))
        self.assertEqual(self.window.recent_projects(), [str(path)])

    def test_file_menu_offers_save_a_copy(self):
        names = [action.text() for action in self.window.menuBar().actions()[0].menu().actions()]
        self.assertIn("Save a &copy…", names)


if __name__ == "__main__":
    unittest.main()
