"""Small layout promises that keep the in-game look readable."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QWidget

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.pixel_widgets import PortraitFrame
from pixelheart.theme import apply_theme
from tests.qt_support import QtTestCase


class ShellPolishTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, content_root=None)

    def setUp(self):
        self.window = MainWindow()
        self.window.resize(1020, 700)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()

    def test_a_blank_start_does_not_welcome_back_a_placeholder(self):
        self.assertNotIn("New character's story", self.window.statusBar().currentMessage())

    def test_version_label_is_readable_on_the_wooden_status_bar(self):
        self.assertIn("QStatusBar QLabel#hint", self.app.styleSheet())

    def test_every_sidebar_title_fits_beside_its_heart(self):
        navigation = self.window.navigation
        metrics = navigation.fontMetrics()
        room = navigation.viewport().width() - navigation.iconSize().width() - 24 - 34
        for row in range(navigation.count()):
            text = navigation.item(row).text()
            self.assertLessEqual(metrics.horizontalAdvance(text), room, text)

    def test_primary_actions_use_short_labels(self):
        self.assertEqual(self.window.save_button.text(), "Save")

    def test_sidebar_footer_wraps_instead_of_clipping(self):
        sidebar = self.window.findChild(QWidget, "sidebar")
        footer = [label for label in sidebar.findChildren(QLabel) if "stay on your computer" in label.text()]
        self.assertEqual(len(footer), 1)
        self.assertTrue(footer[0].wordWrap())

    def test_home_selection_buttons_keep_their_labels_at_minimum_size(self):
        self.window.open_section("home")
        for _ in range(5):
            self.app.processEvents()
        editor = self.window.world.interior_editor
        self.assertIsNotNone(editor)
        for widget in (editor.rotate_button, editor.duplicate_button, editor.remove_button):
            if widget.isVisible():
                self.assertGreaterEqual(widget.width(), widget.sizeHint().width(), widget.text())

    def test_overview_fits_the_minimum_window_without_sideways_scrolling(self):
        self.window.open_section("overview")
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(self.window.section_pages["overview"].horizontalScrollBar().maximum(), 0)
        status = self.window.overview.step_status["identity"]
        self.assertGreaterEqual(status.width(), status.fontMetrics().horizontalAdvance("1 of 8 everyday lines"))

    def test_character_card_uses_new_section_names_and_wraps_its_badge(self):
        identity = self.window.identity
        self.assertIn("Portraits & sprites", identity.portrait.empty_text)
        self.assertTrue(identity.profile_romance.wordWrap())

    def test_portrait_frames_do_not_stretch(self):
        frame = PortraitFrame(64)
        self.addCleanup(frame.deleteLater)
        self.assertEqual(frame.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Fixed)
        self.assertEqual(frame.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Fixed)


if __name__ == "__main__":
    unittest.main()
