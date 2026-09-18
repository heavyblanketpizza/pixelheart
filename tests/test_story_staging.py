"""Verify interactive staging and both authored choice outcomes persist."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pixelheart.app import MainWindow
from pixelheart_core.projects import load_project
from pixelheart_core.story import new_event, new_beat
from pixelheart_core.world import new_companion, new_location, cast_actor_id


class StagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        document = deepcopy(self.window.document)
        event = new_event(document["character"], "first_meeting")
        event["story"]["stage"] = "scene"
        event["story"]["beats"][0]["text"] = "It was good to meet you.$h"
        choice = new_beat("choice")
        choice["text"] = "Will you come back tomorrow?"
        choice["choices"][0].update(label="I'd like that.", text="Then I will save you a seat.$h", friendship=25)
        choice["choices"][1].update(label="I need some time.", text="Of course. There is no hurry.", friendship=0)
        event["story"]["beats"].append(choice)
        document["character"]["events"] = [event]
        self.window.load_document(document)

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_grid_keyboard_moves_actor_and_survives_project_roundtrip(self):
        page = self.window.events
        page.actors.canvas.select_actor(0)
        original = deepcopy(page.actors.records[0])
        QTest.keyClick(page.actors.canvas, Qt.Key.Key_Right)
        self.assertEqual(page.actors.records[0]["x"], original["x"] + 1)
        self.assertEqual(page.actors.table.cellWidget(0, 1).value(), original["x"] + 1)
        self.assertEqual(self.window.document["character"]["events"][0]["story"]["actors"][0]["x"], original["x"] + 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            self.assertTrue(self.window.save_to(path))
            self.assertEqual(load_project(path)["character"]["events"][0]["story"]["actors"], page.actors.records)

    def test_choice_edit_rehearsal_and_fork_preview_cover_both_answers(self):
        page = self.window.events
        page.beats.list.setCurrentRow(len(page.beats.records) - 1)
        page.beats.choice_fields[0]["label"].setText("Tomorrow, then.")
        page.beats.choice_fields[1]["text"].setPlainText("Take as long as you need.")
        page.rehearsal_position = len(page.beats.records) - 1
        page.rehearse()
        self.assertIn("save you a seat", page.choice_result.text())
        page.rehearsal_choice.setCurrentIndex(1)
        self.assertIn("Take as long as you need", page.choice_result.text())
        self.assertIn("+0", page.choice_result.text())
        self.assertEqual(page.dump()[0]["story"]["beats"][-1]["choices"][0]["label"], "Tomorrow, then.")
        self.assertTrue(page.mark_ready())
        page.show_script.setChecked(True)
        entries = json.loads(page.script_preview.toPlainText())[0]["Entries"]
        self.assertEqual(len(entries), 2)
        self.assertTrue(any("question fork0" in value for value in entries.values()))

    def test_choice_valid_boundary_label_is_preserved_when_editing_response(self):
        page = self.window.events
        records = page.dump()
        records[0]["story"]["beats"][-1]["choices"][0]["label"] = "A" * 200
        page.load(records)
        page.beats.list.setCurrentRow(len(page.beats.records) - 1)
        page.beats.choice_fields[1]["text"].setPlainText("Another response.")
        self.assertEqual(page.dump()[0]["story"]["beats"][-1]["choices"][0]["label"], "A" * 200)

    def test_relationship_and_repeat_controls_persist_without_promotion(self):
        page = self.window.events
        page.story_fields["relationship"].setCurrentIndex(page.story_fields["relationship"].findData("married"))
        page.story_fields["min_house_upgrade"].setCurrentIndex(2)
        page.story_fields["repeat"].setCurrentIndex(1)
        story = page.dump()[0]["story"]
        self.assertEqual((story["relationship"], story["min_house_upgrade"], story["repeat"]), ("married", 2, "daily"))
        self.assertEqual(story["stage"], "scene")

    def test_project_places_are_available_by_name_and_store_real_map_alias(self):
        document = deepcopy(self.window.document)
        location = new_location()
        location.update(name="The quiet refuge", internal_name="Refuge")
        document["world"]["locations"] = [location]
        self.window.load_document(document)
        picker = self.window.events.fields["location"]
        index = picker.combo.findData("Refuge")
        self.assertGreaterEqual(index, 0)
        self.assertIn("quiet refuge", picker.combo.itemText(index))
        picker.combo.setCurrentIndex(index)
        self.assertEqual(self.window.document["character"]["events"][0]["location"], "Refuge")

    def test_supporting_character_picker_stores_stable_identity(self):
        document = deepcopy(self.window.document)
        companion = new_companion("Pip")
        document["world"]["characters"] = [companion]
        self.window.load_document(document)
        page = self.window.events
        page.actors.add()
        row = len(page.actors.records) - 1
        picker = page.actors.table.cellWidget(row, 0)
        index = picker.combo.findData(cast_actor_id(companion))
        self.assertGreaterEqual(index, 0)
        self.assertIn("Pip", picker.combo.itemText(index))
        picker.combo.setCurrentIndex(index)
        self.assertEqual(page.actors.records[row]["name"], cast_actor_id(companion))
