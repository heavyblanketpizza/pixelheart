"""Heart events speak plainly: feelings for dialogue steps, and one way to find the game."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QPushButton

from pixelheart.app import MainWindow
from pixelheart.dialogue_tools import EmotionBar
from tests.qt_support import QtTestCase


class HeartEventPolishTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False)
        self.window.resize(1360, 900)
        self.window.show()
        self.window.open_section("story")
        self.app.processEvents()
        self.beats = self.window.events.beats

    def tearDown(self):
        self.window.autosave.timer.stop()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_dialogue_steps_have_feeling_buttons(self):
        beats = self.beats
        self.assertIsInstance(beats.emotion_bar, EmotionBar)
        beats.load([{"id": "a", "kind": "dialogue", "actor": "", "text": "Hello."},
                    {"id": "b", "kind": "move", "actor": "", "x": 1, "y": 0}])
        beats.list.setCurrentRow(0)
        self.assertTrue(beats.emotion_bar.isVisibleTo(beats))
        beats.fields["text"].moveCursor(QTextCursor.MoveOperation.End)
        beats.emotion_bar.buttons["happy"].trigger()
        self.assertEqual(beats.records[0]["text"], "Hello.$h")
        beats.list.setCurrentRow(1)
        self.assertFalse(beats.emotion_bar.isVisibleTo(beats))

    def test_their_words_ask_in_plain_words(self):
        self.assertEqual(self.beats.fields["text"].placeholderText(), "What do they say?")

    def test_one_way_to_find_the_game(self):
        texts = {button.text() for button in self.window.events.findChildren(QPushButton)}
        self.assertNotIn("Locate Stardew Valley…", texts)
        self.assertEqual(self.window.events.actors.game_artwork.text(), "Find Stardew Valley…")
