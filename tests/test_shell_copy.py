"""The main screens speak to players, not modders."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow, _export_completion_message
from tests.qt_support import QtTestCase


class ShellCopyTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()

    def test_play_page_uses_friendly_tabs_buttons_and_summary(self):
        page = self.window.export_page
        self.assertEqual([page.tabs.tabText(i) for i in range(page.tabs.count())],
                         ["Check && export", "Put in game && playtest"])
        self.assertEqual(page.export_button.text(), "Export for Stardew…")
        self.window.open_section("export")
        self.assertRegex(page.summary.text(), r"^\d+ things? to fix before they can move in\.$")
        first = page.list.item(0).text()
        self.assertTrue(first.startswith(("Fix  ·", "Check  ·", "Ready  ·")), first)

    def test_welcome_back_message_names_the_character(self):
        import tempfile
        from pathlib import Path
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.window.identity.fields["name"].setText("Mira")
        self.assertTrue(self.window.save_to(folder / "Mira" / "character.json"))
        self.assertTrue(self.window.open_path(folder / "Mira" / "character.json"))
        self.assertIn("Welcome back to Mira's story.", self.window.statusBar().currentMessage())

    def test_export_message_points_to_the_next_step(self):
        message = _export_completion_message("Mira.zip", {"Dependencies": []})
        self.assertTrue(message.startswith("Saved Mira.zip."))
        self.assertIn("Put in game & playtest", message)
        self.assertIn("SMAPI and Content Patcher", message)
        self.assertIn("test save", message)


if __name__ == "__main__":
    unittest.main()
