"""The in-game menu skin, with game art or original pieces."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton

from pixelheart import skin
from pixelheart.theme import apply_theme
from tests.qt_support import QtTestCase
from tests import test_game_ui


class SkinTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch.dict(os.environ, {"PIXELHEART_CACHE_DIR": str(self.folder / "cache")}))
        self.addCleanup(apply_theme, self.app, content_root=None)

    def test_pixel_font_is_bundled_and_registered(self):
        self.assertTrue((Path(skin.__file__).parent / "resources/fonts/OFL.txt").is_file())
        self.assertIn("Pixelify", skin.pixel_family())

    def test_stylesheet_uses_every_piece_by_object_name(self):
        pieces, source = skin.refresh_pieces(None)
        sheet = skin.build_stylesheet(pieces)
        self.assertEqual(source, "original")
        for selector in ("QFrame#card", "QPushButton#primary", "QPushButton#quiet", "QPushButton#danger",
                         "QLineEdit", "QComboBox::drop-down", "QCheckBox::indicator:checked",
                         "QWidget#sidebar", "QListWidget#navigation::item:selected", "QScrollBar::handle:vertical",
                         "QTabBar::tab:selected"):
            self.assertIn(selector, sheet)
        for role in ("panel", "button", "button_primary", "textbox", "checkbox_on", "wood", "tab"):
            self.assertIn(pieces[role].path.as_posix(), sheet)
        self.assertNotIn("__", sheet)

    def test_easy_read_swaps_body_font_only(self):
        pieces, _ = skin.refresh_pieces(None)
        pixel, easy = skin.build_stylesheet(pieces), skin.build_stylesheet(pieces, easy_read=True)
        family = skin.pixel_family()
        self.assertIn(f'QWidget {{ font-family: "{family}"', pixel)
        self.assertNotIn(f'QWidget {{ font-family: "{family}"', easy)
        self.assertIn(f'QPushButton {{ font-family: "{family}"', easy)
        self.assertFalse(skin.easy_read_enabled())
        skin.set_easy_read(True)
        self.assertTrue(skin.easy_read_enabled())
        self.assertEqual(self.settings.value("view/easyRead"), "true")

    def test_pixel_font_ligatures_are_off_so_fi_and_fl_read_correctly(self):
        from PySide6.QtGui import QFont
        apply_theme(self.app, content_root=None)
        font = self.app.font()
        for tag in ("liga", "clig"):
            self.assertTrue(font.isFeatureSet(QFont.Tag(tag)), tag)
            self.assertEqual(font.featureValue(QFont.Tag(tag)), 0, tag)

    def test_unwritable_cache_falls_back_to_a_temporary_folder(self):
        from pixelheart_core.game_ui import ui_pieces as real
        calls = []
        def flaky(content, cache_root):
            calls.append(cache_root)
            if len(calls) == 1:
                raise PermissionError("read-only home")
            return real(content, cache_root)
        with patch("pixelheart.skin.ui_pieces", side_effect=flaky):
            apply_theme(self.app, content_root=None)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertIn("url(", self.app.styleSheet())

    def test_no_writable_folder_at_all_still_starts_with_a_plain_look(self):
        with patch("pixelheart.skin.ui_pieces", side_effect=PermissionError("no disk")):
            apply_theme(self.app, content_root=None)
        sheet = self.app.styleSheet()
        self.assertIn("font-family", sheet)
        self.assertNotIn("url(", sheet)
        self.assertEqual(skin.current_pieces(), {})

    def test_skin_uses_game_pieces_when_connected(self):
        fixture = test_game_ui.GameUiTests("test_fallback_draws_every_required_role")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        apply_theme(self.app, content_root=fixture.content)
        self.assertEqual(skin.current_source(), "game")
        self.assertIn("game-", self.app.styleSheet())

    def test_skin_falls_back_when_game_disappears(self):
        apply_theme(self.app, content_root=self.folder / "gone")
        self.assertEqual(skin.current_source(), "original")
        self.assertIn("original-v", self.app.styleSheet())
        button = QPushButton("Hello")
        self.addCleanup(button.deleteLater)
        button.ensurePolished()


if __name__ == "__main__":
    unittest.main()
