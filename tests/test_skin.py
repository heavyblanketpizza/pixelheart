"""The paper skin, with hearts and lettering borrowed from a connected game."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton

from meetcute import skin
from meetcute.theme import apply_theme
from tests.qt_support import QtTestCase
from tests import test_game_ui

GAME_CROPS = r"/game-[0-9a-f]{16}/"


class SkinTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("meetcute.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch.dict(os.environ, {"MEETCUTE_CACHE_DIR": str(self.folder / "cache")}))
        self.addCleanup(apply_theme, self.app, content_root=None)

    def test_pixel_font_is_bundled_and_registered(self):
        self.assertTrue((Path(skin.__file__).parent / "resources/fonts/OFL.txt").is_file())
        self.assertIn("Pixelify", skin.pixel_family())

    def test_stylesheet_uses_every_piece_by_object_name(self):
        pieces = skin.refresh_pieces(None)
        sheet = skin.build_stylesheet(pieces)
        for selector in ("QFrame#card", "QPushButton#primary", "QPushButton#quiet", "QPushButton#danger",
                         "QLineEdit", "QComboBox::drop-down", "QCheckBox::indicator:checked",
                         "QWidget#sidebar", "QListWidget#navigation::item:selected", "QScrollBar::handle:vertical",
                         "QTabBar::tab:selected"):
            self.assertIn(selector, sheet)
        for role in ("panel", "button", "button_primary", "textbox", "checkbox_on", "sidebar", "tab"):
            self.assertIn(pieces[role].path.as_posix(), sheet)
        self.assertNotIn("__", sheet)

    def test_easy_read_swaps_body_font_only(self):
        pieces = skin.refresh_pieces(None)
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
        from meetcute_core.game_ui import ui_pieces as real
        calls = []
        def flaky(content, cache_root):
            calls.append(cache_root)
            if len(calls) == 1:
                raise PermissionError("read-only home")
            return real(content, cache_root)
        with patch("meetcute.skin.ui_pieces", side_effect=flaky):
            apply_theme(self.app, content_root=None)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertIn("url(", self.app.styleSheet())
        self.assertIn("temporary folder", skin.current_notice())

    def test_no_writable_folder_at_all_still_starts_with_a_plain_look(self):
        with patch("meetcute.skin.ui_pieces", side_effect=PermissionError("no disk")):
            apply_theme(self.app, content_root=None)
        sheet = self.app.styleSheet()
        self.assertIn("font-family", sheet)
        self.assertNotIn("url(", sheet)
        self.assertEqual(skin.current_pieces(), {})
        self.assertIn("plain look", skin.current_notice())

    def test_a_healthy_cache_has_nothing_to_report(self):
        with patch("meetcute.skin.ui_pieces", side_effect=PermissionError("no disk")):
            skin.refresh_pieces(None)
        skin.refresh_pieces(None)
        self.assertEqual(skin.current_notice(), "")
        skin.refresh_pieces(self.connected_game())
        self.assertEqual(skin.current_notice(), "")

    def test_unreadable_game_pieces_are_reported(self):
        content = self.connected_game()
        (content / "LooseSprites/font_bold.xnb").write_bytes(b"not a texture")
        pieces = skin.refresh_pieces(content)
        self.assertIn("couldn't be read from your game", skin.current_notice())
        self.assertNotIn("font", pieces)

    def test_reapplying_an_unchanged_theme_keeps_the_font(self):
        # Qt skips an equal application font, so repeat calls only cost a relayout if the font drifts.
        from PySide6.QtCore import QEvent, QObject

        class FontChanges(QObject):
            count = 0

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.ApplicationFontChange:
                    FontChanges.count += 1
                return False
        watcher = FontChanges()
        apply_theme(self.app, content_root=None)
        self.app.processEvents()  # Font changes are delivered later; let the first call's settle.
        self.app.installEventFilter(watcher)
        self.addCleanup(self.app.removeEventFilter, watcher)
        apply_theme(self.app, content_root=None)
        self.app.processEvents()
        self.assertEqual(FontChanges.count, 0)
        skin.set_easy_read(True)
        apply_theme(self.app, content_root=None)
        self.app.processEvents()
        self.assertGreater(FontChanges.count, 0)
        self.assertIn("Helvetica", self.app.font().family())

    def connected_game(self):
        fixture = test_game_ui.GameUiTests("test_without_a_game_everything_is_drawn")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture.content

    def test_palette_is_warm_white_and_ink(self):
        reds = {"danger", "danger_soft", "danger_rule", "error"}
        for name, value in skin.COLORS.items():
            if value.startswith("#") and name not in reds:
                channels = [int(value[index:index + 2], 16) for index in (1, 3, 5)]
                self.assertLessEqual(max(channels) - min(channels), 24, name)

    def test_connected_game_lends_only_hearts_and_lettering(self):
        apply_theme(self.app, content_root=self.connected_game())
        self.assertEqual(self.app.palette().window().color().name(), skin.COLORS["page"])
        sheet = self.app.styleSheet()
        self.assertIn("/paper-v", sheet)
        self.assertNotRegex(sheet, GAME_CROPS)
        self.assertIn("game-", skin.current_pieces()["heart_full"].path.parent.name)
        self.assertIn("game-", skin.current_pieces()["font"].path.parent.name)

    def test_missing_game_draws_its_own_hearts_and_headings(self):
        apply_theme(self.app, content_root=self.folder / "gone")
        self.assertIn("paper-v", skin.current_pieces()["heart_full"].path.parent.name)
        self.assertNotIn("font", skin.current_pieces())
        button = QPushButton("Hello")
        self.addCleanup(button.deleteLater)
        button.ensurePolished()


class ThemeNoticeTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("meetcute.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch.dict(os.environ, {"MEETCUTE_CACHE_DIR": str(self.folder / "cache")}))
        self.addCleanup(apply_theme, self.app, content_root=None)

    def window(self):
        from meetcute.app import MainWindow
        window = MainWindow()

        def close():
            window.dirty = False
            window.autosave.timer.stop()
            window.close()
            window.deleteLater()
        self.addCleanup(close)
        return window

    def test_each_fallback_is_noted_once_in_the_status_bar(self):
        from meetcute_core.game_ui import ui_pieces as real
        with patch("meetcute.skin.ui_pieces", side_effect=PermissionError("no disk")):
            apply_theme(self.app, content_root=None)
            window = self.window()
            self.assertIn("plain look", window.statusBar().currentMessage())
            window.statusBar().clearMessage()
            window.toggle_easy_read(True)
            self.assertEqual(window.statusBar().currentMessage(), "")

        def flaky(content, cache_root):
            if cache_root == skin.ui_cache_dir():
                raise PermissionError("read-only home")
            return real(content, cache_root)
        with patch("meetcute.skin.ui_pieces", side_effect=flaky):
            window._game_changed()
        self.assertIn("temporary folder", window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
