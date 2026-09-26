"""Life rules share dialogue/schedule navigation without losing authored data."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart_core.life import COLLECTIONS, new_life_record, normalize_life
from pixelheart_core.projects import load_project
from tests.qt_support import QtTestCase


class LifeEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_life_rules_live_with_dialogue_and_schedule_and_roundtrip(self):
        window = self.window
        document = deepcopy(window.document)
        document["character"]["life"] = normalize_life()
        document["character"]["life"]["author_notes"] = {"keep": "A full life after the wedding."}
        for kind in COLLECTIONS:
            record = new_life_record(kind, document["character"])
            record["author_note"] = "Keep this detail."
            document["character"]["life"][kind] = [record]
        window.load_document(document)
        before = window.life.dump()
        destinations = {"dialogues": window.dialogue_tabs, "spouse_dialogue": window.dialogue_tabs, "routines": window.schedule_tabs}
        for kind, tabs in destinations.items():
            with self.subTest(kind=kind):
                editor = window.life.editors[kind]
                window.life.open_issue(f"life.{kind}.0.name")
                self.assertIs(tabs.currentWidget(), editor)
                self.assertEqual(editor.current, 0)
        self.assertEqual(window.life.dump(), before)
        self.assertFalse(window.dirty)
        for kind, editor in window.life.editors.items():
            editor.fields["name"].setText(f"Their {kind}")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "character.json"
            self.assertTrue(window.save_to(path))
            saved = load_project(path)["character"]["life"]
        self.assertEqual(saved, window.life.dump())
        self.assertEqual(saved["author_notes"], before["author_notes"])
        for kind in COLLECTIONS:
            self.assertEqual(saved[kind][0]["author_note"], "Keep this detail.")
