"""Starting from a villager needs no console commands when the game is connected."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.game_connection import game_connection
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game
from tests.test_game_templates import texture_from
from tests.test_vanilla_story import dictionary_xnb


class GameFirstTemplateTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        game = fake_game(self.folder / "Stardew Valley")
        content = game / "Contents/Resources/Content"
        (content / "Characters/Dialogue").mkdir(parents=True)
        (content / "Characters/Dialogue/Haley.xnb").write_bytes(dictionary_xnb({"Introduction": "Oh. Hi.", "Mon": "Ugh, Monday."}))
        (content / "Portraits").mkdir()
        (content / "Portraits/Haley.xnb").write_bytes(texture_from(Image.new("RGBA", (128, 192), (250, 200, 0, 255))))
        (content / "Characters/Haley.xnb").write_bytes(texture_from(Image.new("RGBA", (64, 416), (0, 0, 250, 255))))
        self.game = game

    def wait(self, dialog):
        while dialog.worker is not None:
            self.app.processEvents()

    def test_dialogue_template_reads_the_game_without_a_saved_project(self):
        game_connection().set_folder(self.game)
        from pixelheart.dialogue_templates import DialogueTemplateDialog
        dialog = DialogueTemplateDialog([], project_file=None)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.source_choice.currentData(), "game")
        self.assertEqual([dialog.character.itemText(i) for i in range(dialog.character.count())], ["Haley"])
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertTrue(dialog.game_source.isHidden())
        dialog.load_button.click()
        self.wait(dialog)
        self.assertEqual(dialog.list.count(), 2)
        dialog.accept()
        self.assertEqual([row["trigger"] for row in dialog.imported_records], ["Introduction", "Mon"])

    def test_switching_to_export_mode_restores_the_console_flow(self):
        game_connection().set_folder(self.game)
        from pixelheart.dialogue_templates import DialogueTemplateDialog
        dialog = DialogueTemplateDialog([], project_file=None)
        self.addCleanup(dialog.deleteLater)
        dialog.source_choice.setCurrentIndex(dialog.source_choice.findData("export"))
        self.assertFalse(dialog.game_source.isHidden())
        self.assertEqual(dialog.character.itemText(0), "Abigail")
        self.assertFalse(dialog.load_button.isEnabled())

    def test_without_a_game_the_export_flow_is_unchanged(self):
        from pixelheart.dialogue_templates import DialogueTemplateDialog
        dialog = DialogueTemplateDialog([], project_file=None)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.source_choice.currentData(), "export")
        self.assertFalse(dialog.load_button.isEnabled())
        self.assertEqual(dialog.character.itemText(0), "Abigail")

    def test_artwork_template_reads_the_game_and_imports_into_a_project(self):
        game_connection().set_folder(self.game)
        from pixelheart.app import MainWindow
        from pixelheart.artwork_templates import ArtworkTemplateDialog
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.assertTrue(window.save_to(self.folder / "Haley fan" / "character.json"))
        dialog = ArtworkTemplateDialog()
        self.assertEqual(dialog.source_choice.currentData(), "game")
        self.assertEqual(dialog.character.currentText(), "Haley")
        dialog.load_button.click()
        self.wait(dialog)
        self.assertIsNotNone(dialog.loaded)
        temporary = Path(dialog.loaded["portrait"]).parent
        self.assertTrue(window.artwork.apply_template(dialog.loaded))
        dialog.deleteLater()
        self.assertFalse(temporary.exists())
        self.assertTrue(window.document["artwork"]["portrait"]["original"].startswith("artwork/"))
        window.dirty = False
        window.close()

    def test_dialogue_page_opens_game_templates_without_forcing_a_save(self):
        game_connection().set_folder(self.game)
        from pixelheart.app import MainWindow
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        with patch.object(window, "save_as", side_effect=AssertionError("no save needed")), \
             patch("pixelheart.editors.DialogueTemplateDialog.exec", return_value=0):
            window.dialogue.open_examples()
        self.assertTrue(window.dialogue.project_location.isHidden())
        window.dirty = False
        window.close()


if __name__ == "__main__":
    unittest.main()
