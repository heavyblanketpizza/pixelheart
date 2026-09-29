"""The welcome screen: your game, your characters, and a way to start."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.game_connection import game_connection
from pixelheart_core.projects import new_project, save_project
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game


class WelcomeTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.library = self.folder / "Pixelheart"
        self.enterContext(patch.dict(os.environ, {"PIXELHEART_LIBRARY": str(self.library)}))

    def window(self, **options):
        window = MainWindow(**options)

        def close():
            window.dirty = False
            window.close()
            window.deleteLater()
        self.addCleanup(close)
        return window

    def character(self, name, folder=None):
        document = new_project()
        document["character"]["name"] = name
        return save_project(document, (folder or self.library / name) / "character.json")

    def test_launch_without_a_project_shows_welcome_with_library_characters(self):
        self.character("Mira")
        window = self.window(show_welcome=True)
        self.assertIs(window.root_stack.currentWidget(), window.welcome)
        self.assertEqual([card.name.text() for card in window.welcome.cards], ["Mira"])
        self.assertEqual(window.welcome.cards[0].meter.filled(), 1)
        self.assertTrue(window.welcome.empty.isHidden())

    def test_empty_library_invites_a_first_character(self):
        window = self.window(show_welcome=True)
        self.assertEqual(window.welcome.cards, [])
        self.assertFalse(window.welcome.empty.isHidden())

    def test_default_construction_still_opens_the_workspace(self):
        window = self.window()
        self.assertIsNot(window.root_stack.currentWidget(), window.welcome)

    def test_opening_a_card_loads_the_character_and_remembers_it(self):
        path = self.character("Theo", self.folder / "elsewhere" / "Theo")
        window = self.window(show_welcome=True)
        window.remember_project(path)
        window.welcome.refresh()
        window.welcome.cards[0].open_button.click()
        self.assertEqual(window.project_file, path)
        self.assertIsNot(window.root_stack.currentWidget(), window.welcome)

    def test_missing_and_broken_recents_do_not_break_welcome(self):
        broken = self.library / "Broken" / "character.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{nope")
        window = self.window(show_welcome=True)
        window.remember_project(self.folder / "gone" / "character.json")
        window.welcome.refresh()
        errors = [card for card in window.welcome.cards if card.error]
        self.assertEqual(len(errors), 2)
        errors[0].remove_button.click()
        self.assertEqual(len([card for card in window.welcome.cards if card.error]), 1)

    def test_game_status_reflects_the_connection(self):
        window = self.window(show_welcome=True)
        self.assertIn("hasn't found", window.welcome.game_status.text())
        game_connection().set_folder(fake_game(self.folder / "Stardew Valley"))
        self.assertIn("found", window.welcome.game_status.text())
        self.assertIn("SMAPI", window.welcome.tools_status.text())
        self.assertEqual(window.welcome.find_button.text(), "Change…")

    def test_plugging_the_drive_back_in_is_noticed_when_the_app_is_reactivated(self):
        from PySide6.QtCore import Qt
        game = self.folder / "Just for Fun" / "Stardew Valley"
        self.settings.setValue("game/folder", str(game))
        window = self.window(show_welcome=True)
        self.assertIn("isn't connected right now", window.welcome.game_status.text())
        fake_game(game)
        self.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        self.assertIn("found", window.welcome.game_status.text())

    def test_file_home_returns_to_welcome_after_discard_prompt(self):
        window = self.window()
        window.dirty = False
        self.assertTrue(window.show_home())
        self.assertIs(window.root_stack.currentWidget(), window.welcome)
        window.show_workspace()
        window.dirty = True
        with patch.object(window, "confirm_discard", return_value=False):
            self.assertFalse(window.show_home())
        self.assertIsNot(window.root_stack.currentWidget(), window.welcome)

    def test_launch_detects_the_game_once_and_opens_welcome(self):
        from pixelheart import __main__ as launcher
        with patch.object(launcher, "MainWindow") as window_class, \
             patch("PySide6.QtWidgets.QApplication.exec", return_value=0), \
             patch("pixelheart.game_connection.find_games", return_value=[]) as finder:
            self.assertEqual(launcher.main([]), 0)
            self.assertTrue(window_class.call_args.kwargs["show_welcome"])
            finder.assert_called_once()
            launcher.main(["some/character.json"])
            self.assertFalse(window_class.call_args.kwargs["show_welcome"])

    def test_menus_offer_home_find_game_easy_read_and_help(self):
        window = self.window()
        menus = {action.text(): action.menu() for action in window.menuBar().actions()}
        self.assertEqual(list(menus), ["&File", "&Edit", "&View", "&Help"])
        file_items = [action.text() for action in menus["&File"].actions() if action.text()]
        self.assertEqual(file_items[:7], ["&Home", "&New character", "&Open…", "&Save", "Save &automatically", "Save a &copy…", "&Play in Stardew…"])
        view_items = {action.text(): action for action in menus["&View"].actions()}
        self.assertIn("&Find Stardew Valley…", view_items)
        easy = view_items["&Easier-to-read text"]
        self.assertTrue(easy.isCheckable())
        easy.trigger()
        self.assertEqual(self.settings.value("view/easyRead"), "true")
        easy.trigger()
        self.assertEqual(self.settings.value("view/easyRead"), "false")
        help_items = [action.text() for action in menus["&Help"].actions()]
        self.assertIn("&Getting started", help_items)


if __name__ == "__main__":
    unittest.main()
