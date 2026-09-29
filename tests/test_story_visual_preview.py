"""The scene preview uses authored assets without editing gameplay or the farmer."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_event, new_beat
from tests.qt_support import QtTestCase


class StoryVisualPreviewTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.window = MainWindow(preload_story=False)
        self.document = new_project()
        self.event = new_event(self.document["character"], "first_meeting")
        self.event["story"]["beats"][0]["text"] = "Welcome, ${sir^ma'am}$ @!$h"
        self.document["character"]["events"] = [self.event]
        self.settings = patch("pixelheart.game_import.game_import_settings", return_value=SimpleNamespace(value=lambda *_: str(self.root)))
        self.settings.start()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.settings.stop()
        self.temporary.cleanup()

    def load(self):
        self.window.load_document(self.document, self.root / "character.json")
        return self.window.events

    def test_project_sprites_and_selected_portrait_load_automatically(self):
        art = self.root / "artwork"
        art.mkdir()
        sprite = Image.new("RGBA", (64, 416), "#449966")
        sprite.save(art / "sprite.png")
        portrait = Image.new("RGBA", (128, 192), "#113355")
        ImageDraw.Draw(portrait).rectangle((64, 0, 127, 63), fill="#cc6633")
        portrait.save(art / "portrait.png")
        self.document["artwork"] = {"sprite": "artwork/sprite.png", "portrait": "artwork/portrait.png"}
        page = self.load()
        self.assertIn("$npc", page.actors.canvas.sprites)
        self.assertEqual(page.actors.canvas.sprite_frame(page.actors.records[0]).pixelColor(5, 5).name(), "#449966")
        self.assertFalse(page.rehearsal_portrait.isHidden())
        self.assertEqual(page.rehearsal_portrait.pixmap().toImage().pixelColor(10, 10).name(), "#cc6633")
        self.assertFalse(self.window.dirty)

    def test_event_location_controls_map_and_missing_map_is_explicit(self):
        Image.new("RGBA", (160, 160), "#336699").save(self.root / "Maps_Town.png")
        Image.new("RGBA", (192, 128), "#ddbb88").save(self.root / "Maps_Beach.png")
        page = self.load()
        self.assertEqual(page.actors.canvas.map_size, (10, 10))
        page.fields["location"].setText("Beach")
        self.assertEqual(page.actors.canvas.map_size, (12, 8))
        self.assertEqual(page.actors.canvas.background.pixelColor(0, 0).name(), "#ddbb88")
        page.fields["location"].setText("Forest")
        self.assertTrue(page.actors.canvas.background.isNull())
        self.assertIn("map unavailable", page.actors.canvas.preview_note.lower())
        self.assertIn("Forest", page.actors.source_note.text())
        self.assertEqual(page.records[0]["location"], "Forest")

    def test_missing_trigger_location_uses_chosen_preview_without_changing_trigger(self):
        self.event["location"] = ""
        Image.new("RGBA", (160, 160), "#336699").save(self.root / "Maps_Town.png")
        Image.new("RGBA", (192, 128), "#ddbb88").save(self.root / "Maps_Beach.png")
        page = self.load()
        self.assertFalse(page.actors.preview_location_panel.isHidden())
        self.assertEqual(page.actors.canvas.map_size, (10, 10))
        before = self.window.project_snapshot()
        page.actors.preview_location.setText("Beach")
        self.assertEqual(page.actors.canvas.map_size, (12, 8))
        self.assertEqual(page.records[0]["location"], "")
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertFalse(self.window.dirty)

    def test_close_reopen_grid_and_farmer_profile_are_preview_only(self):
        page = self.load()
        before = self.window.project_snapshot()
        self.assertFalse(page.actors.preview_panel.isHidden())
        page.actors.toggle_preview()
        self.assertTrue(page.actors.preview_panel.isHidden())
        page.actors.farmer.setCurrentIndex(1)
        self.assertTrue(page.actors.preview_panel.isHidden())
        self.assertEqual(page.actors.canvas.farmer_gender, "male")
        self.assertIn("sir Farmer", page.rehearsal_text.text())
        page.farmer_name.setText("Taylor")
        self.assertIn("sir Taylor", page.rehearsal_text.text())
        page.rehearsal_gender.setCurrentIndex(0)
        self.assertIn("ma'am Taylor", page.rehearsal_text.text())
        self.assertEqual(page.actors.farmer.currentData(), "female")
        page.actors.toggle_preview()
        page.actors.grid_toggle.setChecked(True)
        page.actors.canvas.zoom_by(1.25)
        self.assertFalse(page.actors.preview_panel.isHidden())
        self.assertTrue(page.actors.canvas.grid_visible)
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertFalse(self.window.dirty)

    def test_choice_question_answers_and_responses_use_the_same_farmer_profile(self):
        choice = new_beat("choice")
        choice["text"] = "Ready, ${sir^ma'am}$ @?"
        choice["choices"][0].update(label="${His^Her}$ answer", text="See you, ${sir^ma'am}$ @.$h")
        choice["choices"][1].update(label="${His^Her}$ other answer", text="Later, ${sir^ma'am}$ @.$s")
        self.event["story"]["beats"] = [choice]
        page = self.load()
        before = deepcopy(page.records)
        page.farmer_name.setText("Taylor")
        page.actors.farmer.setCurrentIndex(1)
        self.assertIn("sir Taylor", page.rehearsal_text.text())
        self.assertEqual(page.rehearsal_choice.itemText(0), "His answer")
        self.assertIn("sir Taylor", page.choice_result.text())
        self.assertNotIn("$h", page.choice_result.text())
        page.rehearsal_choice.setCurrentIndex(1)
        page.rehearsal_gender.setCurrentIndex(0)
        self.assertEqual(page.rehearsal_choice.currentIndex(), 1)
        self.assertIn("Later, ma'am Taylor", page.choice_result.text())
        self.assertEqual(page.records, before)

    def test_scene_layout_has_no_horizontal_scroll_at_minimum_window(self):
        page = self.load()
        self.window.resize(1020, 700)
        self.window.show()
        self.window.open_section("story")
        self.window.story.open_event(self.event["id"])
        page.phases.setCurrentIndex(page.SCENE)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(page.phases.currentWidget().horizontalScrollBar().maximum(), 0)
        self.assertEqual(page.phases.currentWidget().verticalScrollBar().maximum(), 0)
        self.assertTrue(page.scene_inspector.isVisible())
        viewport = page.phases.currentWidget().viewport()
        for index in range(len(page.actors.records)):
            point = page.actors.canvas.actor_rect(index).center().toPoint()
            self.assertTrue(viewport.rect().contains(page.actors.canvas.mapTo(viewport, point)))
        self.assertTrue(self.window.title_label.isHidden())
        page.phases.setCurrentIndex(page.TRIGGER)
        self.assertTrue(self.window.title_label.isHidden())

    def test_preloaded_scenes_can_be_removed_and_restored_without_legacy_editors(self):
        from pixelheart_core.romance import new_romance_events
        document = new_project()
        document["character"]["events"] = new_romance_events(document["character"])
        self.window.load_document(document)
        before = self.window.project_snapshot()
        self.assertEqual(len(self.window.events.records), 6)
        self.assertEqual(self.window.story.dump()["storyline"]["chapters"], [])
        self.window.events.remove()
        self.assertEqual(len(self.window.events.records), 5)
        self.window.project_history.undo()
        self.assertEqual(self.window.project_snapshot(), before)
