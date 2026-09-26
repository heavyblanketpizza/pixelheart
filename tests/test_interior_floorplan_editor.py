"""Direct floorplan gestures preserve content and the editor's undo contract."""
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
from pixelheart_core.interiors import new_interior, reachable_tiles
from pixelheart_core.interior_furniture import validate_definition
from tests.qt_support import QtTestCase


class FloorplanEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.source = new_interior()
        self.source["catalog"] = [validate_definition({"id": "(F)Chair", "kind": "chair", "footprint": [1, 1]})]
        self.source["furniture"] = [{"id": "chair", "item_id": "(F)Chair", "x": 8, "y": 10,
                                     "rotation": 0, "mod_data": {"custom": "keep"}}]
        self.editor = InteriorEditor(self.root / "character.json", self.source)
        self.editor.show()
        self.app.processEvents()
        self.editor.tabs.setCurrentIndex(2)
        self.editor.select_room_on_canvas("main")
        self.editor.wall_type.setCurrentIndex(self.editor.wall_type.findData("slim"))

    def tearDown(self):
        self.editor.reject()
        self.editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def point(self, x, y):
        cell = self.editor.canvas.scale * 16
        return QPoint(x * cell + cell // 2, y * cell + cell // 2)

    def move(self, point, held=True):
        self.app.sendEvent(self.editor.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier))

    def gesture(self, start, end, *, release=True):
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        if release:
            QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()

    def state(self):
        return (self.editor.draft.snapshot(), deepcopy(self.editor.draft._undo),
                deepcopy(self.editor.draft._redo), deepcopy(self.editor._room_offsets),
                deepcopy(self.editor._offsets_undo), deepcopy(self.editor._offsets_redo))

    def resize_handle(self, handle, dx, dy, *, release=True):
        self.editor.select_room_on_canvas("main")
        start = QPoint(*self.editor.canvas._resize_handles()[handle])
        cell = self.editor.canvas.scale * 16
        end = start + QPoint(dx * cell, dy * cell)
        self.gesture(start, end, release=release)
        return end

    def wall(self):
        self.editor.layout_mode.setCurrentIndex(1)
        self.editor.set_tool("partition")
        self.gesture(self.point(2, 8), self.point(11, 8))
        self.assertEqual(len(self.editor.draft.data.get("partitions", [])), 1, self.editor.status.text())
        return self.editor.draft.data["partitions"][0]

    def test_mouse_resize_keeps_furniture_and_supports_one_undo_and_redo(self):
        before = self.editor.draft.snapshot()
        end = self.resize_handle("w", -1, 0, release=False)
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.canvas._room_preview, (1, 5, 11, 8))
        self.assertEqual(self.editor.draft.snapshot(), before)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=end)
        after = self.editor.draft.snapshot()
        self.assertEqual(after["furniture"], before["furniture"])
        self.assertEqual(after["entry"], before["entry"])
        self.assertEqual(after["doorway"], before["doorway"])
        self.assertEqual(len(self.editor.draft._undo), 1)
        self.assertEqual(self.editor._room_offsets, {})
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before)
        self.editor.redo()
        self.assertEqual(self.editor.draft.snapshot(), after)
        self.assertEqual(self.source["rooms"][0]["x"], 2)

    def test_draw_hallway_builds_one_tile_wide_permanent_floor_and_undo_removes_it(self):
        before = self.editor.draft.snapshot()
        self.editor.set_tool("corridor")
        self.gesture(self.point(15, 7), self.point(12, 7))
        hallway = self.editor.draft.data["rooms"][-1]
        self.assertEqual(tuple(hallway[key] for key in ("x", "y", "width", "height")), (12, 7, 4, 1))
        self.assertEqual(hallway["kind"], "hallway")
        self.assertFalse(hallway["optional"])
        self.assertEqual(self.editor.canvas.tool, "room-select")
        self.assertEqual(len(self.editor.draft._undo), 1)
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before)

    def test_wall_width_and_opening_placement_are_real_undoable_edits(self):
        before = self.editor.draft.snapshot()
        wall = self.wall()
        identity = wall["id"]
        self.assertEqual(self.editor.selected_partition, identity)
        self.assertEqual(self.editor.canvas.selected_partition_id, identity)
        self.assertEqual(self.editor.layout_mode.currentData(), "walls")
        self.assertEqual(wall["openings"], [{"offset": 4, "width": 1}])
        self.editor.opening_width.setCurrentIndex(self.editor.opening_width.findData(2))
        self.assertEqual(self.editor.draft.data["partitions"][0]["openings"], [{"offset": 3, "width": 2}])
        self.editor.opening_button.click()
        self.move(self.point(9, 8), held=False)
        self.assertEqual(self.editor.canvas._room_preview, (8, 6, 2, 3))
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 8))
        self.assertEqual(self.editor.draft.data["partitions"][0]["openings"], [{"offset": 6, "width": 2}])
        self.assertEqual(self.editor.canvas.tool, "wall-select")
        self.assertEqual(len(self.editor.draft._undo), 3)
        self.editor.undo()
        self.assertEqual(self.editor.draft.data["partitions"][0]["openings"], [{"offset": 3, "width": 2}])
        self.editor.undo()
        self.assertEqual(self.editor.draft.data["partitions"][0]["openings"], [{"offset": 4, "width": 1}])
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before)

    def test_solid_wall_that_separates_floor_is_rejected_and_selector_restored(self):
        self.wall()
        before = self.state()
        self.editor.opening_width.setCurrentIndex(self.editor.opening_width.findData(0))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.editor.opening_width.currentData(), 1)
        self.assertIn("connected", self.editor.status.text().lower())

    def test_external_destination_validator_rejects_resize_in_preview_and_commit(self):
        calls = []
        def protected(candidate, offsets):
            calls.append(deepcopy(offsets))
            dx, dy = offsets.get("main", (0, 0))
            if (11 + dx, 8 + dy) not in reachable_tiles(candidate):
                raise ValueError("Keep the authored schedule stop reachable.")
        self.editor.validate_layout = protected
        before = self.state()
        end = self.resize_handle("e", -2, 0, release=False)
        self.assertFalse(self.editor.canvas.preview_valid)
        self.assertIn("authored schedule", self.editor.canvas.preview_message)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.editor.resize_room("main", 2, 5, 8, 8))
        self.assertIn("authored schedule", self.editor.status.text())
        self.assertEqual(self.state(), before)
        self.assertTrue(calls)
        self.assertTrue(all(offsets == {} for offsets in calls))

    def test_external_destination_validator_rejects_wall_without_changing_history(self):
        def protected(candidate, offsets):
            if (7, 7) not in reachable_tiles(candidate):
                raise ValueError("Keep the authored scene position clear.")
        self.editor.validate_layout = protected
        before = self.state()
        self.editor.set_tool("partition")
        self.gesture(self.point(3, 7), self.point(10, 7), release=False)
        self.assertFalse(self.editor.canvas.preview_valid)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(10, 7))
        self.assertEqual(self.state(), before)
        self.assertFalse(self.editor.draw_partition("horizontal", 3, 7, 8))
        self.assertIn("authored scene", self.editor.status.text())
        self.assertEqual(self.state(), before)

    def test_move_resize_undo_redo_save_reports_only_actual_translation(self):
        original = self.editor.draft.snapshot()
        self.gesture(self.point(6, 6), self.point(8, 6))
        self.assertEqual(self.editor._room_offsets, {"main": [2, 0]})
        moved = self.editor.draft.snapshot()
        self.resize_handle("w", -1, 0)
        resized = self.editor.draft.snapshot()
        self.assertEqual(resized["rooms"][0]["x"], 3)
        self.assertEqual(resized["rooms"][0]["width"], 11)
        self.assertEqual(resized["furniture"], moved["furniture"])
        self.assertEqual(self.editor._room_offsets, {"main": [2, 0]})
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), moved)
        self.assertEqual(self.editor._room_offsets, {"main": [2, 0]})
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), original)
        self.assertEqual(self.editor._room_offsets, {})
        self.editor.redo()
        self.editor.redo()
        self.assertEqual(self.editor.draft.snapshot(), resized)
        self.editor.save_design()
        self.assertEqual(self.editor.result_room_translations, {"main": [2, 0]})
        self.assertEqual(self.editor.result_design, resized)

    def test_ordinary_room_selection_keeps_room_controls_and_clears_wall_selection(self):
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 6))
        self.assertEqual(self.editor.layout_mode.currentData(), "rooms")
        wall = self.wall()
        self.assertEqual(self.editor.selected_partition, wall["id"])
        self.editor.layout_mode.setCurrentIndex(0)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 11))
        self.assertEqual(self.editor.layout_mode.currentData(), "rooms")
        self.assertEqual(self.editor.selected_partition, "")
        self.assertEqual(self.editor.canvas.selected_partition_id, "")
        self.assertEqual(self.editor.canvas.selected_room_id, "main")

    def test_upper_wall_face_selects_wall_and_accepts_opening_placement(self):
        wall = self.wall()
        identity = wall["id"]
        self.editor.layout_mode.setCurrentIndex(0)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(5, 6))
        self.assertEqual(self.editor.selected_partition, identity)
        self.assertEqual(self.editor.layout_mode.currentData(), "walls")
        self.editor.opening_width.setCurrentIndex(self.editor.opening_width.findData(2))
        self.editor.opening_button.click()
        before = self.editor.draft.snapshot()
        self.move(self.point(9, 6), held=False)
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.canvas._room_preview, (8, 6, 2, 3))
        self.assertEqual(self.editor.draft.snapshot(), before)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 6))
        self.assertEqual(self.editor.draft.data["partitions"][0]["openings"], [{"offset": 6, "width": 2}])

    def test_opening_past_wall_end_is_rejected_without_history_change(self):
        self.wall()
        self.editor.opening_width.setCurrentIndex(self.editor.opening_width.findData(2))
        self.editor.opening_button.click()
        before = self.state()
        self.move(self.point(2, 6), held=False)
        self.assertFalse(self.editor.canvas.preview_valid)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 6))
        self.assertEqual(self.state(), before)
        self.assertTrue(self.editor.status.text())

    def test_mode_switch_cancels_resize_before_late_mouse_release(self):
        before = self.state()
        end = self.resize_handle("e", 1, 0, release=False)
        self.assertIsNotNone(self.editor.canvas._room_resize)
        self.editor.tabs.setCurrentIndex(0)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.editor.canvas._room_resize)
        self.assertIsNone(self.editor.canvas._room_preview)


if __name__ == "__main__":
    unittest.main()
