"""Dialogue tools: plain-language occasions, feeling buttons and the game-style box."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from pixelheart.app import MainWindow
from pixelheart.dialogue_tools import DialogueBoxPreview, EmotionBar, TriggerPicker, portrait_frames
from tests.qt_support import QtTestCase


class TriggerPickerTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.picker = TriggerPicker(items=lambda: [("(O)109", "Poppy"), ("(O)18", "Daffodil")])
        self.addCleanup(self.picker.deleteLater)
        self.changes = []
        self.picker.changed.connect(lambda: self.changes.append(self.picker.value()))

    def test_a_day_of_the_week_round_trips_and_edits(self):
        self.picker.set_value("summer_Thu4")
        self.assertEqual(self.picker.occasion.currentData(), "weekday")
        self.assertEqual((self.picker.day.currentData(), self.picker.season.currentData(), self.picker.hearts.currentData()),
                         ("Thu", "summer", 4))
        self.assertEqual(self.picker.value(), "summer_Thu4")
        self.assertIn("summer_Thu4", self.picker.key_label.text())
        self.assertEqual(self.changes, [])
        self.picker.hearts.setCurrentIndex(self.picker.hearts.findData(6))
        self.assertEqual(self.changes, ["summer_Thu6"])

    def test_changing_the_occasion_builds_its_key(self):
        self.picker.set_value("Mon")
        self.picker.occasion.setCurrentIndex(self.picker.occasion.findData("introduction"))
        self.assertEqual(self.picker.value(), "Introduction")
        self.picker.occasion.setCurrentIndex(self.picker.occasion.findData("date"))
        self.assertEqual(self.picker.value(), "spring_1")
        self.picker.occasion.setCurrentIndex(self.picker.occasion.findData("birthday_gift"))
        self.assertEqual(self.picker.value(), "AcceptBirthdayGift_Positive")

    def test_places_and_gifts(self):
        self.picker.set_value("Saloon_Tue")
        self.assertEqual((self.picker.place.value(), self.picker.place_day.currentData()), ("Saloon", "Tue"))
        self.assertEqual(self.picker.value(), "Saloon_Tue")
        self.picker.set_value("AcceptGift_(O)109")
        self.assertEqual(self.picker.gift.currentData(), "(O)109")
        self.assertEqual(self.picker.value(), "AcceptGift_(O)109")
        self.picker.gift.setCurrentIndex(self.picker.gift.findData("(O)18"))
        self.assertEqual(self.changes[-1], "AcceptGift_(O)18")

    def test_unknown_trigger_survives_selection(self):
        self.picker.set_value("Resort_Bar")
        self.assertEqual(self.picker.occasion.currentData(), "other")
        self.assertEqual(self.picker.value(), "Resort_Bar")
        self.picker.other.setText("eventSeen_3")
        self.assertEqual(self.changes, ["eventSeen_3"])


class EmotionAndBoxTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.edit = QPlainTextEdit()
        self.addCleanup(self.edit.deleteLater)

    def put_cursor(self, position):
        cursor = self.edit.textCursor()
        cursor.setPosition(position)
        self.edit.setTextCursor(cursor)

    def test_feeling_buttons_set_the_box_expression(self):
        bar = EmotionBar(self.edit)
        self.addCleanup(bar.deleteLater)
        self.edit.setPlainText("Hi!#$b#Bye.")
        self.put_cursor(2)
        bar.buttons["happy"].click()
        self.assertEqual(self.edit.toPlainText(), "Hi!$h#$b#Bye.")
        self.put_cursor(len(self.edit.toPlainText()))
        bar.buttons["sad"].click()
        self.assertEqual(self.edit.toPlainText(), "Hi!$h#$b#Bye.$s")
        bar.new_box.click()
        bar.farmer.click()
        self.assertEqual(self.edit.toPlainText(), "Hi!$h#$b#Bye.$s#$b#@")

    def test_buttons_show_the_characters_portraits(self):
        sheet = self.root / "portraits.png"
        Image.new("RGBA", (128, 192), (200, 80, 80, 255)).save(sheet)
        frames = portrait_frames(sheet)
        self.assertEqual(len(frames), 6)
        self.assertEqual((frames[0].width(), frames[0].height()), (64, 64))
        bar = EmotionBar(self.edit)
        self.addCleanup(bar.deleteLater)
        bar.set_portraits(frames)
        self.assertFalse(bar.buttons["love"].icon().isNull())
        self.assertEqual(portrait_frames(None), [])

    def test_the_box_pages_through_the_dialogue(self):
        box = DialogueBoxPreview()
        self.addCleanup(box.deleteLater)
        box.set_dialogue("Hi, @!$h#$b#See you.#$e#Back again?", name="Mira")
        self.assertEqual(box.page_count(), 3)
        self.assertEqual((box.page, box.current().portrait), (0, 1))
        self.assertEqual(box.position.text(), "Box 1 of 3")
        self.assertFalse(box.previous.isEnabled())
        box.next.click()
        box.next.click()
        self.assertEqual(box.position.text(), "Box 3 of 3 · next time you talk")
        self.assertFalse(box.next.isEnabled())
        box.set_dialogue("Hi", name="Mira")
        self.assertEqual(box.page, 0)
        box.grab()  # paints without errors

    def test_the_box_says_when_commands_are_shown_as_written(self):
        box = DialogueBoxPreview()
        self.addCleanup(box.deleteLater)
        box.set_dialogue("$q 1/2 q#Yes#$r 1 0 a", name="Mira")
        self.assertIn("as written", box.note.text())
        box.set_dialogue("", name="Mira")
        self.assertEqual(box.page_count(), 0)
        box.grab()


class DialoguePageWiringTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False)
        self.page = self.window.dialogue

    def tearDown(self):
        self.window.autosave.timer.stop()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_list_titles_are_plain_language(self):
        self.page.load([{"id": "a", "trigger": "Introduction", "text": "Hi"},
                        {"id": "b", "trigger": "summer_Thu4", "text": "Warm."}])
        titles = [self.page.list.item(i).text() for i in range(self.page.list.count())]
        self.assertEqual(titles, ["The first time they meet", "Summer Thursdays · 4+ hearts"])

    def test_picker_edits_the_record_and_the_title(self):
        self.page.load([{"id": "a", "trigger": "Mon", "text": "Hi"}])
        picker = self.page.fields["trigger"]
        picker.season.setCurrentIndex(picker.season.findData("fall"))
        self.assertEqual(self.page.dump()[0]["trigger"], "fall_Mon")
        self.assertEqual(self.page.list.item(0).text(), "Fall Mondays")

    def test_switching_records_keeps_triggers(self):
        records = [{"id": "a", "trigger": "Introduction", "text": "Hi"},
                   {"id": "b", "trigger": "Resort_Bar", "text": "Island!"},
                   {"id": "c", "trigger": "Sun4", "text": "Sunday."}]
        self.page.load(records)
        for row in (1, 2, 0, 1):
            self.page.list.setCurrentRow(row)
        self.assertEqual([record["trigger"] for record in self.page.dump()], ["Introduction", "Resort_Bar", "Sun4"])
        self.assertEqual(self.page.fields["trigger"].value(), "Resort_Bar")

    def test_feelings_and_box_follow_the_text(self):
        self.page.load([{"id": "a", "trigger": "Mon", "text": "Hello."}])
        edit = self.page.fields["text"]
        edit.moveCursor(QTextCursor.MoveOperation.End)
        self.page.emotion_bar.buttons["love"].click()
        self.assertEqual(self.page.dump()[0]["text"], "Hello.$l")
        self.assertEqual(self.page.box.current().portrait, 4)

    def test_life_dialogue_editors_have_the_tools(self):
        for kind in ("dialogues", "spouse_dialogue"):
            editor = self.window.life.editors[kind]
            self.assertIsInstance(editor.emotion_bar, EmotionBar)
            self.assertIsInstance(editor.box, DialogueBoxPreview)
        self.assertFalse(hasattr(self.window.life.editors["routines"], "emotion_bar"))
