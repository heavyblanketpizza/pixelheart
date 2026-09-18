"""Local source setup shares one private folder preference across template screens."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.game_import import LocalGameSourceWidget


class LocalGameSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.settings = QSettings(str(Path(self.temporary) / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def widget(self, kind):
        widget = LocalGameSourceWidget("abigail", kind)
        self.widgets.append(widget)
        return widget

    def test_browsing_remembers_folder_for_other_template_screen(self):
        dialogue = self.widget("dialogue")
        folder = str(Path(self.temporary) / "Game with spaces" / "patch export")
        with patch("pixelheart.game_import.QFileDialog.getExistingDirectory", return_value=folder):
            dialogue.browse_button.click()
        artwork = self.widget("artwork")
        self.assertEqual(artwork.directory(), folder)
        self.assertEqual(self.settings.value(dialogue.SETTINGS_KEY), folder)

    def test_cancel_does_not_clear_folder_and_character_change_does_not_change_it(self):
        widget = self.widget("dialogue")
        widget.folder.setText(self.temporary)
        spy = QSignalSpy(widget.changed)
        with patch("pixelheart.game_import.QFileDialog.getExistingDirectory", return_value=""):
            widget.browse_button.click()
        widget.set_template("elliott")
        self.assertEqual(widget.directory(), self.temporary)
        self.assertEqual(spy.count(), 0)
        self.assertEqual(widget.commands.toPlainText(), 'patch export "Characters/Dialogue/Elliott"')

    def test_copy_commands_uses_selected_character_and_no_machine_paths(self):
        widget = self.widget("artwork")
        widget.set_template("elliott")
        widget.copy_button.click()
        copied = self.app.clipboard().text()
        self.assertEqual(copied, 'patch export "Portraits/Elliott" image\npatch export "Characters/Elliott" image')
        self.assertNotIn(self.temporary, copied)


if __name__ == "__main__":
    unittest.main()
