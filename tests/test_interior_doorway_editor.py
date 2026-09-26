"""Doorway gestures edit real map geometry, never an arrival-point overlay."""
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
from pixelheart_core.interiors import doorway_exit, new_interior
from tests.qt_support import QtTestCase


class DoorwayEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.original = new_interior()
        self.before = deepcopy(self.original)
        self.editor = InteriorEditor(self.root / "character.json", self.original)
        self.editor.show()
        self.app.processEvents()
        self.editor.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.editor.reject()
        self.editor.deleteLater()
        self.app.processEvents()

    def point(self, x, y):
        cell = self.editor.canvas.scale * 16
        return QPoint(x * cell + cell // 2, y * cell + cell // 2)

    def move(self, x, y, *, held=False):
        point = QPointF(self.point(x, y))
        QApplication.sendEvent(self.editor.canvas, QMouseEvent(
            QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier))

    def test_old_design_gets_staged_opening_without_moving_arrival_or_source(self):
        self.assertIn("doorway", self.editor.draft.data)
        self.assertEqual(self.editor.draft.data["entry"], self.before["entry"])
        self.assertEqual(self.original, self.before)
        self.editor.reject()
        self.assertIsNone(self.editor.result_design)
        self.assertEqual(self.original, self.before)

    def test_dragging_actual_passage_moves_door_and_arrival_in_one_undo(self):
        canvas = self.editor.canvas
        before = self.editor.draft.snapshot()
        start_x, start_y = doorway_exit(before)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=self.point(start_x, start_y))
        self.move(8, start_y, held=True)
        self.assertTrue(canvas.preview_valid, canvas.preview_message)
        self.assertEqual(canvas._room_candidate_data["doorway"], [8, start_y-1])
        self.assertEqual(self.editor.draft.snapshot(), before)
        self.assertFalse(self.editor.draft._undo)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=self.point(8, start_y))
        self.assertEqual(self.editor.draft.data["doorway"], [8, start_y-1])
        self.assertEqual(self.editor.draft.data["entry"], [8, start_y-2])
        self.assertEqual(len(self.editor.draft._undo), 1)
        self.assertIsNone(canvas._doorway_preview)
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before)
        self.editor.redo()
        self.assertEqual(doorway_exit(self.editor.draft.data), (8, start_y))

    def test_invalid_door_drag_does_not_edit_room_or_arrival(self):
        before = self.editor.draft.snapshot()
        x, y = doorway_exit(before)
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(x, y))
        self.move(7, 7, held=True)
        self.assertFalse(self.editor.canvas.preview_valid)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 7))
        self.assertEqual(self.editor.draft.snapshot(), before)
        self.assertFalse(self.editor.draft._undo)
        self.assertIsNone(self.editor.canvas._doorway_preview)

    def test_place_tool_previews_real_opening_and_escape_clears_it(self):
        before = self.editor.draft.snapshot()
        self.editor.set_tool("entry")
        self.move(8, 12)
        self.assertTrue(self.editor.canvas.preview_valid)
        self.assertEqual(self.editor.canvas._room_candidate_data["doorway"], [8, 12])
        self.assertFalse(self.editor.canvas._room_candidate_image.isNull())
        QTest.keyClick(self.editor.canvas, Qt.Key.Key_Escape)
        self.assertIsNone(self.editor.canvas._doorway_preview)
        self.assertTrue(self.editor.canvas._room_candidate_image.isNull())
        self.assertEqual(self.editor.canvas.tool, "room-select")
        self.assertEqual(self.editor.draft.snapshot(), before)

    def test_click_place_then_save_uses_same_doorway_as_preview(self):
        self.editor.set_tool("entry")
        self.move(8, 12)
        preview = self.editor.canvas._room_candidate_data
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(8, 12))
        self.assertEqual(self.editor.draft.data["doorway"], preview["doorway"])
        self.assertEqual(self.editor.canvas.tool, "room-select")
        self.editor.save_design()
        self.assertEqual(doorway_exit(self.editor.result_design), (8, 13))
        self.assertEqual(self.editor.result_design["entry"], [8, 11])

    def test_arrival_tile_shows_only_floor_art_in_normal_view(self):
        self.editor.tabs.setCurrentIndex(0)
        canvas = self.editor.canvas
        x, y = self.editor.draft.data["entry"]
        actual = canvas.grab().toImage()
        expected = canvas.image.toImage()
        # Inspect the complete tile: an IN label or tinted debug square would
        # alter these pixels even if its center happened to match the floor.
        for py in range(16):
            for px in range(16):
                self.assertEqual(actual.pixelColor((x*16+px)*canvas.scale, (y*16+py)*canvas.scale),
                                 expected.pixelColor(x*16+px, y*16+py))


if __name__ == "__main__":
    unittest.main()
