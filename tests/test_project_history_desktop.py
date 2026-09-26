"""Project undo follows authoring across pages and ends only after a successful save."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart_core.projects import ProjectError, new_project
from tests.qt_support import QtTestCase


class ProjectHistoryDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.window = MainWindow(auto_download_icons=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))
        self.controller = self.window.project_history
        self.history = self.controller.history
        self.path = self.root / "project" / "character.json"
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_chronological_history_across_editors_preserves_current_page(self):
        window = self.window
        original = window.project_snapshot()
        window.identity.fields["name"].setText("Mira")
        window.open_section("dialogue")
        window.dialogue.fields["text"].setPlainText("Hello, @.")
        window.open_section("gifts")
        window.gifts.assign_items(["(O)74"], "love")
        final = window.project_snapshot()
        self.assertEqual(self.history.undo_count, 3)
        self.controller.undo()
        self.assertEqual(window.stack.currentIndex(), SECTION_INDEX["gifts"])
        self.assertEqual(window.document["character"]["gifts"], original["character"]["gifts"])
        self.controller.undo()
        self.assertEqual(window.dialogue.dump(), original["character"]["dialogues"])
        self.controller.undo()
        self.assertEqual(window.project_snapshot(), original)
        self.assertFalse(window.dirty)
        for _ in range(3):
            self.controller.redo()
        self.assertEqual(window.project_snapshot(), final)

    def test_typing_coalesces_and_shortcut_undo_is_project_wide(self):
        name = self.window.identity.fields["name"]
        name.setFocus()
        name.selectAll()
        original = name.text()
        QTest.keyClicks(name, "Mira")
        self.assertEqual(self.history.undo_count, 1)
        self.window.open_section("dialogue")
        text = self.window.dialogue.fields["text"]
        text.setFocus()
        self.app.processEvents()
        QTest.keySequence(text, QKeySequence(QKeySequence.StandardKey.Undo))
        self.assertEqual(name.text(), original)
        self.assertFalse(self.history.can_undo)
        QTest.keySequence(text, QKeySequence(QKeySequence.StandardKey.Redo))
        self.assertEqual(name.text(), "Mira")

    def test_selection_survives_restore_and_new_edit_invalidates_redo(self):
        window = self.window
        window.open_section("dialogue")
        window.dialogue.add()
        identity = window.dialogue.records[-1]["id"]
        window.dialogue.fields["text"].setPlainText("A second conversation")
        self.controller.undo()
        self.assertEqual(window.dialogue.records[window.dialogue.current]["id"], identity)
        window.dialogue.fields["text"].setPlainText("A different conversation")
        self.assertFalse(self.history.can_redo)

    def test_save_resets_history_and_native_text_undo(self):
        name = self.window.identity.fields["name"]
        name.setFocus()
        name.selectAll()
        QTest.keyClicks(name, "Mira")
        self.assertTrue(name.isUndoAvailable())
        self.assertTrue(self.window.save_to(self.path))
        self.assertFalse(self.history.can_undo)
        self.assertFalse(self.history.can_redo)
        self.assertFalse(name.isUndoAvailable())
        self.assertFalse(self.window.dirty)
        saved = json.loads(self.path.read_text())
        self.assertNotIn("history", saved)
        self.assertNotIn("project_history", saved)
        name.setText("Rin")
        self.controller.undo()
        self.assertEqual(name.text(), "Mira")
        self.assertFalse(self.window.dirty)

    def test_failed_or_cancelled_save_preserves_both_stacks(self):
        name = self.window.identity.fields["name"]
        name.setText("Mira")
        name.setText("Rin")
        self.controller.undo()
        current = self.window.project_snapshot()
        with patch("pixelheart.app.save_project", side_effect=ProjectError("Disk full")):
            self.assertFalse(self.window.save_to(self.path))
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=("", "")):
            self.assertFalse(self.window.save())
        self.assertEqual(self.window.project_snapshot(), current)
        self.assertEqual((self.history.undo_count, self.history.redo_count), (1, 1))
        self.controller.redo()
        self.assertEqual(name.text(), "Rin")

    def test_loading_another_project_clears_history_and_unknown_metadata_survives(self):
        document = new_project()
        document["extension"] = {"nested": [1, {"x": 2}]}
        self.window.load_document(document)
        self.window.identity.fields["name"].setText("Mira")
        self.controller.undo()
        self.assertEqual(self.window.document["extension"], document["extension"])
        self.window.load_document(new_project())
        self.assertFalse(self.history.can_undo)
        self.assertFalse(self.history.can_redo)

    def test_removal_controls_stay_hidden_and_edit_menu_is_single_pair(self):
        window = self.window
        window.open_section("story")
        window.events.add()
        window.events.remove()
        self.assertTrue(window.events.undo_button.isHidden())
        self.controller.undo()
        self.assertTrue(window.events.undo_button.isHidden())
        edit = next(action.menu() for action in window.menuBar().actions() if action.text() == "&Edit")
        self.assertEqual([action.text() for action in edit.actions()], ["&Undo", "&Redo"])
        file = next(action.menu() for action in window.menuBar().actions() if action.text() == "&File")
        self.assertFalse(any("as…" in action.text() for action in file.actions()))

    def test_undo_refreshes_visible_review_results(self):
        window = self.window
        window.identity.fields["internal_name"].setText("Invalid name!")
        window.open_section("export")
        self.assertTrue(any(issue["field"] == "internal_name" and issue["level"] == "error"
                            for issue in window.export_page.issues))
        self.controller.undo()
        self.assertFalse(any(issue["field"] == "internal_name" and issue["level"] == "error"
                             for issue in window.export_page.issues))
        self.assertEqual(window.stack.currentIndex(), SECTION_INDEX["export"])
        self.controller.redo()
        self.assertTrue(any(issue["field"] == "internal_name" and issue["level"] == "error"
                            for issue in window.export_page.issues))

    def test_live_staging_dialog_refreshes_after_undo(self):
        from pixelheart.stage_canvas import StageCanvas
        window = self.window
        window.open_section("story")
        window.events.add()
        actors = window.events.actors
        actors.add()
        original = deepcopy(actors.records)

        def exercise(dialog):
            dialog.show()
            canvas = dialog.findChild(StageCanvas)
            actors.move_actor(0, 12, 15)
            canvas.dragging = True
            actors.canvas.dragging = True
            self.controller.undo()
            self.assertEqual(actors.records, original)
            self.assertEqual(canvas.actors, original)
            self.assertFalse(canvas.dragging)
            self.assertFalse(actors.canvas.dragging)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", exercise):
            actors.open_staging()
