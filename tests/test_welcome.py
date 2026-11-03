"""The welcome screen: your game, your characters, and a way to start."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from meetcute.app import MainWindow
from meetcute.game_connection import game_connection
from meetcute_core.library import TRASH_NAME
from meetcute_core.projects import new_project, save_project
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game


class WelcomeTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("meetcute.game_import.game_import_settings", return_value=self.settings))
        self.library = self.folder / "MEETC#TE"
        self.projects = self.folder / "projects"
        self.enterContext(patch.dict(os.environ, {"MEETCUTE_LIBRARY": str(self.library),
                                                  "MEETCUTE_PROJECTS": str(self.projects)}))

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

    def test_characters_in_the_projects_folder_are_listed_before_first_open(self):
        self.character("Mira")
        self.character("Iris", self.projects / "iris")
        window = self.window(show_welcome=True)
        self.assertEqual([card.name.text() for card in window.welcome.cards], ["Mira", "Iris"])

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

    def menu_items(self, card):
        return [action.text() for action in card.menu.actions() if action.text()]

    def fake_trash(self):
        trash = self.folder / "Trash"
        trash.mkdir(exist_ok=True)

        def move(path):
            source = Path(path)
            if not source.exists():
                return False
            source.rename(trash / source.name)
            return True
        return trash, self.enterContext(patch("PySide6.QtCore.QFile.moveToTrash", side_effect=move))

    def test_library_cards_offer_remove_and_trash_but_outside_cards_only_remove(self):
        self.character("Mira")
        outside = self.character("Theo", self.folder / "elsewhere" / "Theo")
        window = self.window(show_welcome=True)
        window.remember_project(outside)
        window.welcome.refresh()
        cards = {card.name.text(): card for card in window.welcome.cards}
        self.assertEqual(self.menu_items(cards["Mira"]), ["Remove from list", f"Move to {TRASH_NAME}…"])
        self.assertEqual(self.menu_items(cards["Theo"]), ["Remove from list"])
        self.assertEqual(cards["Mira"].more_button.accessibleName(), "More for Mira")

    def test_removing_a_library_character_stays_hidden_after_restart_until_reopened(self):
        path = self.character("Mira")
        self.character("Iris")
        window = self.window(show_welcome=True)
        card = next(card for card in window.welcome.cards if card.name.text() == "Mira")
        card.remove_action.trigger()
        self.assertEqual([card.name.text() for card in window.welcome.cards], ["Iris"])
        self.assertTrue(path.is_file())
        again = self.window(show_welcome=True)
        self.assertEqual([card.name.text() for card in again.welcome.cards], ["Iris"])
        self.assertTrue(again.open_path(path))
        again.show_home()
        self.assertEqual(sorted(card.name.text() for card in again.welcome.cards), ["Iris", "Mira"])

    def test_moving_a_character_to_trash_takes_its_whole_folder_after_confirming(self):
        path = self.character("Mira")
        (path.parent / "artwork").mkdir()
        (path.parent / "artwork" / "portrait.png").write_bytes(b"png")
        trash, mover = self.fake_trash()
        window = self.window(show_welcome=True)
        window.remember_project(path)
        card = window.welcome.cards[0]
        with patch.object(window.welcome, "confirm_trash", return_value=False) as confirm:
            card.trash_action.trigger()
        confirm.assert_called_once_with("Mira")
        self.assertTrue(path.is_file())
        with patch.object(window.welcome, "confirm_trash", return_value=True):
            window.welcome.cards[0].trash_action.trigger()
        mover.assert_called_once_with(str(path.parent))
        self.assertFalse(path.parent.exists())
        self.assertTrue((trash / "Mira" / "artwork" / "portrait.png").is_file())
        self.assertEqual(window.welcome.cards, [])
        self.assertEqual(window.recent_projects(), [])

    def test_trashing_the_open_character_closes_it_without_saving_it_back(self):
        path = self.character("Mira")
        self.fake_trash()
        window = self.window(show_welcome=True)
        window.open_path(path)
        window.document["character"]["tagline"] = "changed"
        window.dirty = True
        window.autosave.schedule()
        window.show_home = lambda: True
        window.root_stack.setCurrentWidget(window.welcome)
        window.welcome.refresh()
        with patch.object(window.welcome, "confirm_trash", return_value=True):
            window.welcome.cards[0].trash_action.trigger()
        self.assertIsNone(window.project_file)
        self.assertFalse(window.dirty)
        self.assertFalse(window.autosave.pending())
        self.assertFalse(window.autosave.flush(force=True) and path.exists())
        self.assertFalse(path.parent.exists())

    def test_a_failed_trash_keeps_the_character_and_says_so(self):
        path = self.character("Mira")
        self.enterContext(patch("PySide6.QtCore.QFile.moveToTrash", return_value=False))
        window = self.window(show_welcome=True)
        with patch.object(window.welcome, "confirm_trash", return_value=True), \
             patch.object(window, "show_error") as error:
            window.welcome.cards[0].trash_action.trigger()
        error.assert_called_once()
        self.assertIn(TRASH_NAME, error.call_args.args[0])
        self.assertTrue(path.is_file())
        self.assertEqual([card.name.text() for card in window.welcome.cards], ["Mira"])

    def test_characters_sharing_a_name_show_their_folder(self):
        self.character("Iris")
        self.character("Iris", self.library / "Iris 2")
        self.character("Mira")
        window = self.window(show_welcome=True)
        details = {card.path.parent.name: card.detail.text() for card in window.welcome.cards}
        self.assertTrue(details["Iris"].startswith("Iris · Edited"))
        self.assertTrue(details["Iris 2"].startswith("Iris 2 · Edited"))
        self.assertTrue(details["Mira"].startswith("Edited"))

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
        from meetcute import __main__ as launcher
        with patch.object(launcher, "MainWindow") as window_class, \
             patch("PySide6.QtWidgets.QApplication.exec", return_value=0), \
             patch("meetcute.game_connection.find_games", return_value=[]) as finder:
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
