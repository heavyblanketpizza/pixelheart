"""The app remembers one game folder and explains its state in plain words."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.game_connection import (
    SETTINGS_KEY, FindGameDialog, GameConnection, game_connection, reset_game_connection,
)
from pixelheart_core.game_install import GameInstallError, inspect_game
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game


class GameConnectionTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.game = fake_game(self.folder / "Just for Fun" / "Stardew Valley")

    def dialog(self):
        dialog = FindGameDialog()
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_not_set_until_a_folder_is_chosen(self):
        connection = GameConnection(self.settings)
        self.assertEqual(connection.state(), "not_set")
        self.assertIsNone(connection.content_root())
        self.assertIn("hasn't found Stardew Valley", connection.message())

    def test_legacy_installation_setting_is_migrated_once(self):
        self.settings.setValue("localGame/installationFolder", str(self.game))
        connection = GameConnection(self.settings)
        self.assertEqual(connection.state(), "connected")
        self.assertEqual(self.settings.value(SETTINGS_KEY), str(self.game))
        self.assertEqual(self.settings.value("localGame/installationFolder"), str(self.game))

    def test_set_folder_saves_and_emits_once(self):
        connection = GameConnection(self.settings)
        spy = QSignalSpy(connection.changed)
        install = connection.set_folder(self.game)
        self.assertEqual(install.version, "1.6.15")
        self.assertEqual(spy.count(), 1)
        self.assertEqual(connection.content_root(), (self.game / "Contents/Resources/Content").resolve())
        self.assertIn("1.6.15", connection.message())
        self.assertIn("SMAPI isn't installed yet", connection.tools_message())

    def test_bad_folder_raises_and_keeps_previous(self):
        connection = GameConnection(self.settings)
        connection.set_folder(self.game)
        (self.folder / "Photos").mkdir()
        with self.assertRaises(GameInstallError):
            connection.set_folder(self.folder / "Photos")
        self.assertEqual(connection.state(), "connected")

    def test_unplugged_drive_is_reported_not_raised(self):
        self.settings.setValue(SETTINGS_KEY, str(self.folder / "Unplugged" / "Stardew Valley"))
        connection = GameConnection(self.settings)
        self.assertEqual(connection.state(), "unplugged")
        self.assertIn("isn't connected right now", connection.message())

    def test_auto_detect_only_when_nothing_is_saved(self):
        connection = GameConnection(self.settings)
        with patch("pixelheart.game_connection.find_games", return_value=[]) as finder:
            self.assertFalse(connection.auto_detect())
        finder.assert_called_once()
        with patch("pixelheart.game_connection.find_games", return_value=[inspect_game(self.game)]):
            self.assertTrue(connection.auto_detect())
        self.assertEqual(connection.state(), "connected")
        with patch("pixelheart.game_connection.find_games", side_effect=AssertionError("no rescan")):
            self.assertFalse(connection.auto_detect())

    def test_singleton_uses_current_settings_and_resets(self):
        first = game_connection()
        self.assertIs(first, game_connection())
        self.assertIs(first.settings, self.settings)
        reset_game_connection()
        self.assertIsNot(first, game_connection())

    def test_find_dialog_chooses_a_folder_and_shows_mod_tools(self):
        dialog = self.dialog()
        with patch("pixelheart.game_connection.QFileDialog.getExistingDirectory", return_value=str(self.game)):
            dialog.choose_button.click()
        self.assertEqual(game_connection().state(), "connected")
        self.assertIn("1.6.15", dialog.status.text())
        self.assertIn("SMAPI", dialog.tools.text())

    def test_find_dialog_search_lists_and_uses_found_games(self):
        dialog = self.dialog()
        with patch("pixelheart.game_connection.find_games", return_value=[inspect_game(self.game)]):
            dialog.search_button.click()
        self.assertEqual(dialog.results.count(), 1)
        dialog.results.setCurrentRow(0)
        dialog.use_button.click()
        self.assertEqual(game_connection().state(), "connected")

    def test_find_dialog_reports_wrong_folder_inline(self):
        (self.folder / "Photos").mkdir()
        dialog = self.dialog()
        with patch("pixelheart.game_connection.QFileDialog.getExistingDirectory", return_value=str(self.folder / "Photos")):
            dialog.choose_button.click()
        self.assertIn("doesn't look like Stardew Valley", dialog.status.text())


if __name__ == "__main__":
    unittest.main()
