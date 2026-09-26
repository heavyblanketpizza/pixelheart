"""New placement rules leave older loose pieces editable and repairable."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from tests.qt_support import QtTestCase

try:
    from .test_interior_architecture_rules_canvas import steps_design
except ImportError:
    from test_interior_architecture_rules_canvas import steps_design


class ArchitectureRulesEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.source = steps_design(self.root)
        self.project = self.root / "character.json"
        self.editors = []

    def tearDown(self):
        self.app.processEvents()
        for editor in self.editors:
            editor.reject()
            editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def open(self, data=None, *, allow_rebase=False):
        editor = InteriorEditor(self.project, self.source if data is None else data, allow_rebase=allow_rebase)
        self.editors.append(editor)
        self.editor = editor
        editor.show()
        editor.tabs.setCurrentIndex(2)
        editor.layout_mode.setCurrentIndex(editor.layout_mode.findData("architecture"))
        self.app.processEvents()
        return editor

    def point(self, x, y):
        cell = self.editor.canvas.scale * 16
        return QPoint(x*cell + cell//2, y*cell + cell//2)

    def drag(self, start, end):
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(*start))
        point = self.point(*end)
        self.app.sendEvent(self.editor.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=point)

    def legacy(self):
        data = deepcopy(self.source)
        definition = data["architecture_catalog"][-1]
        definition["id"] = "stardew.stairs"
        definition.pop("rules")
        data["architecture"] = [{"id": "old-steps", "piece_id": "stardew.stairs", "x": 3, "y": 7,
                                 "room_id": "main"}]
        return data

    def test_valid_steps_drag_with_their_connection_with_undo_redo(self):
        editor = self.open(steps_design(self.root, raised=True))
        history = len(editor.draft._undo)
        placed = editor.draft.snapshot()
        identity = placed["architecture"][0]["id"]
        self.drag((8, 8), (9, 8))
        moved = editor.draft.data["architecture"][0]
        self.assertEqual((moved["id"], moved["x"], moved["y"]), (identity, 8, 7))
        connector = next(room for room in editor.draft.data["rooms"] if room.get("kind") == "stairway")
        self.assertEqual(connector["x"], 8)
        self.assertEqual(len(editor.draft._undo), history + 1)
        editor.undo()
        self.assertEqual(editor.draft.snapshot(), placed)
        editor.redo()
        self.assertEqual(editor.draft.data["architecture"][0]["x"], 8)
        moved = editor.draft.snapshot()
        self.drag((8, 7), (8, 8))
        self.assertEqual(editor.draft.snapshot(), moved)
        editor.save_design()
        self.assertIsNotNone(editor.result_design, editor.status.text())

    def test_legacy_floating_steps_reopen_reject_save_then_replace_with_real_connection(self):
        source = self.legacy()
        before = deepcopy(source)
        editor = self.open(source, allow_rebase=True)
        self.assertFalse(editor.canvas.image.isNull())
        self.assertEqual(editor.draft.data["architecture"], source["architecture"])
        editor.save_design()
        self.assertIsNone(editor.result_design)
        self.assertIn("steps", editor.status.text().lower())
        self.assertTrue(editor.isVisible())
        self.assertEqual(source, before)
        old = editor.draft.snapshot()
        self.drag((3, 7), (4, 7))
        self.assertEqual(editor.draft.snapshot(), old)
        self.drag((3, 7), (7, 7))
        self.assertEqual(editor.draft.snapshot(), old)
        editor.select_architecture("old-steps")
        editor.remove_button.click()
        editor.layout_mode.setCurrentIndex(editor.layout_mode.findData("rooms"))
        editor.room_type.setCurrentIndex(editor.room_type.findData("raised"))
        self.assertTrue(editor.place_room_drop(4, -1, 6, 2), editor.status.text())
        repaired = editor.draft.snapshot()
        self.assertEqual(sum(room.get("level") == 1 for room in repaired["rooms"]), 1)
        editor.undo()
        editor.undo()
        self.assertEqual(editor.draft.snapshot(), old)
        editor.redo()
        editor.redo()
        self.assertEqual(editor.draft.snapshot(), repaired)
        editor.save_design()
        self.assertIsNotNone(editor.result_design, editor.status.text())
        reopened = self.open(editor.result_design)
        self.assertEqual(reopened.draft.data["architecture"], repaired["architecture"])
        self.assertEqual(source, before)

    def test_legacy_invalid_piece_can_be_selected_removed_and_saved(self):
        editor = self.open(self.legacy())
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 7))
        self.assertEqual(editor.selected_architecture, "old-steps")
        self.assertTrue(editor.remove_button.isEnabled())
        editor.remove_button.click()
        self.assertEqual(editor.draft.data["architecture"], [])
        editor.save_design()
        self.assertIsNotNone(editor.result_design, editor.status.text())


if __name__ == "__main__":
    unittest.main()
