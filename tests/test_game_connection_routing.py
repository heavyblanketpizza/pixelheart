"""Every game-reading feature uses the one remembered game folder."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import pixelheart_core.scene_preview as scene_preview
from pixelheart.game_connection import game_connection
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game


class RoutingTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.game = fake_game(self.folder / "Stardew Valley", smapi=True)

    def test_scene_locator_opens_the_shared_dialog(self):
        from pixelheart.story_page import CastEditor
        editor = CastEditor()
        self.addCleanup(editor.deleteLater)
        with patch("pixelheart.game_connection.open_find_game", return_value=True) as opener:
            editor.choose_game_artwork()
        opener.assert_called_once()

    def test_scene_preview_uses_connection_folder_without_scanning(self):
        game_connection().set_folder(self.game)
        from pixelheart.app import MainWindow
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        with patch("pixelheart_core.game_scene_assets.discover_game_root", side_effect=AssertionError("no scan")), \
             patch.object(scene_preview, "resolve_scene_preview", wraps=scene_preview.resolve_scene_preview) as resolver:
            window.open_section("story")
            window.events.update_preview()
        self.assertEqual(resolver.call_args.kwargs["game_root"], str(self.game.resolve()))
        window.dirty = False
        window.close()

    def test_scene_preview_without_a_game_does_not_scan(self):
        from pixelheart.app import MainWindow
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        with patch("pixelheart_core.game_scene_assets.discover_game_root", side_effect=AssertionError("no scan")), \
             patch.object(scene_preview, "resolve_scene_preview", wraps=scene_preview.resolve_scene_preview) as resolver:
            window.open_section("story")
            window.events.update_preview()
        self.assertIsNone(resolver.call_args.kwargs["game_root"])
        window.dirty = False
        window.close()

    def test_vanilla_story_remembers_through_connection(self):
        from pixelheart.vanilla_story import VanillaStoryDialog
        from pixelheart_core.projects import new_project
        game_connection().set_folder(self.game)
        with patch("pixelheart.vanilla_story.QTimer.singleShot"):
            dialog = VanillaStoryDialog(new_project()["character"])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.game_root, str(self.game.resolve()))
        other = fake_game(self.folder / "Other Stardew")
        dialog._remember_source(str(other))
        self.assertEqual(game_connection().install().root, other.resolve())

    def test_install_defaults_to_the_games_mods_folder(self):
        from pixelheart.playtest_page import default_mods_folder
        self.assertEqual(default_mods_folder(), "")
        game_connection().set_folder(self.game)
        self.assertEqual(default_mods_folder(), str((self.game / "Contents/MacOS/Mods").resolve()))


if __name__ == "__main__":
    unittest.main()
