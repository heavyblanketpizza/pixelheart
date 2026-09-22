"""The room canvas previews edits without committing or bypassing core rules."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import InteriorDraft, new_interior, render_interior


class InteriorCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.definition = validate_definition({"id": "(F)Test.Chair", "name": "Chair", "kind": "chair",
                                               "footprint": [1, 2], "rotations": 2,
                                               "rotation_footprints": {"1": [2, 1]}})
        design = new_interior()
        design["catalog"] = [self.definition]
        self.draft = InteriorDraft(design)
        self.canvas = InteriorCanvas(self.draft, root=self.root)
        with render_interior(self.draft.data, self.root) as image:
            self.canvas.set_image(image)

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.processEvents()

    def point(self, x, y):
        cell = self.canvas.scale * 16
        return QPoint(x * cell + 5, y * cell + 5)

    def move(self, x, y):
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(self.point(x, y)),
                            QPointF(self.point(x, y)), Qt.MouseButton.NoButton,
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.canvas, event)

    def test_held_item_shows_valid_and_blocked_tiles_without_editing_draft(self):
        before = self.draft.snapshot()
        self.canvas.set_placement(self.definition, root=self.root)
        self.move(6, 7)
        self.assertTrue(self.canvas.ghost["valid"])
        self.assertEqual((self.canvas.ghost["x"], self.canvas.ghost["y"]), (6, 7))
        self.move(4, 10)
        self.assertFalse(self.canvas.ghost["valid"])
        self.assertIn("entry", self.canvas.preview_message)
        self.move(20, 7)
        self.assertFalse(self.canvas.preview_valid)
        self.assertIn("floor", self.canvas.preview_message)
        self.assertEqual(self.draft.snapshot(), before)
        self.assertFalse(self.draft.undo())

    def test_rotation_preview_uses_explicit_footprint(self):
        self.canvas.set_placement(self.definition, 1)
        self.move(6, 7)
        self.assertEqual((self.canvas.ghost["width"], self.canvas.ghost["height"]), (2, 1))
        self.assertEqual(self.draft.data["furniture"], [])

    def test_occupied_position_is_blocked_but_rug_can_underlap(self):
        self.draft.place_furniture(self.definition["id"], 6, 7)
        self.canvas.set_placement(self.definition)
        self.move(6, 7)
        self.assertFalse(self.canvas.preview_valid)
        rug = validate_definition({"id": "(F)Test.Rug", "kind": "rug", "footprint": [2, 2]})
        self.canvas.set_placement(rug)
        self.assertTrue(self.canvas.preview_valid)

    def test_placement_remains_a_proposal_for_the_editor(self):
        edits = []
        self.canvas.tile_clicked.connect(lambda x, y: edits.append((x, y)))
        self.canvas.set_placement(self.definition)
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(20, 7))
        self.assertEqual(edits, [(6, 7), (20, 7)])
        self.assertEqual(self.draft.data["furniture"], [])

    def test_drag_uses_grabbed_tile_offset_and_is_a_single_edit(self):
        identity = self.draft.place_furniture(self.definition["id"], 6, 7)
        before = self.draft.snapshot()
        self.canvas.moved.connect(self.draft.move_furniture)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 8))
        self.move(8, 10)
        self.assertEqual((self.canvas.ghost["x"], self.canvas.ghost["y"]), (8, 9))
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.draft.snapshot(), before)
        self.assertFalse(self.canvas._drag_image.isNull())
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(8, 10))
        placed = self.draft.data["furniture"][0]
        self.assertEqual((placed["id"], placed["x"], placed["y"]), (identity, 8, 9))
        self.assertIsNone(self.canvas.ghost)
        self.draft.undo()
        self.assertEqual(self.draft.snapshot(), before)

    def test_dragging_outside_floor_shows_rejection_without_mutation(self):
        identity = self.draft.place_furniture(self.definition["id"], 6, 7)
        before = self.draft.snapshot()
        attempts = []
        self.canvas.moved.connect(lambda *args: attempts.append(args))
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(20, 19)
        self.assertFalse(self.canvas.preview_valid)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(20, 19))
        self.assertEqual(attempts, [(identity, 20, 19)])
        self.assertEqual(self.draft.snapshot(), before)

    def test_right_click_rotates_held_item_and_escape_cancels(self):
        rotated, canceled = [], []
        self.canvas.rotate_requested.connect(lambda: rotated.append(True))
        self.canvas.canceled.connect(lambda: canceled.append(True))
        self.canvas.set_placement(self.definition)
        self.move(6, 7)
        QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=self.point(6, 7))
        self.assertEqual(rotated, [True])
        self.assertEqual(canceled, [])
        self.assertIsNotNone(self.canvas.ghost)
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        self.assertEqual(canceled, [True])
        self.assertEqual(self.canvas.tool, "select")
        self.assertIsNone(self.canvas.ghost)

    def test_escape_during_drag_cancels_without_closing_editor(self):
        self.draft.place_furniture(self.definition["id"], 6, 7)
        before = self.draft.snapshot()
        dialog = QDialog()
        QVBoxLayout(dialog).addWidget(self.canvas)
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        self.canvas.moved.connect(self.draft.move_furniture)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(8, 9)
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(8, 9))
        self.assertEqual(self.draft.snapshot(), before)
        self.assertEqual(rejected, [])
        self.canvas.setParent(None)
        dialog.deleteLater()

    def test_draw_room_backwards_previews_connection_and_normalizes_rectangle(self):
        rectangles = []
        self.canvas.room_drawn.connect(lambda *args: rectangles.append(args))
        before = self.draft.snapshot()
        self.canvas.tool = "room"
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(15, 8))
        self.move(12, 5)
        self.assertEqual(self.canvas._room_preview, (12, 5, 4, 4))
        self.assertTrue(self.canvas.preview_valid)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(12, 5))
        self.assertEqual(rectangles, [(12, 5, 4, 4)])
        self.assertIsNone(self.canvas._room_preview)
        self.assertEqual(self.draft.snapshot(), before)

    def test_invalid_room_still_proposes_edit_for_editor_to_explain(self):
        rectangles = []
        self.canvas.room_drawn.connect(lambda *args: rectangles.append(args))
        self.canvas.tool = "room"
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(7, 8)
        self.assertFalse(self.canvas.preview_valid)
        self.assertIn("overlap", self.canvas.preview_message)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.assertEqual(rectangles, [(6, 7, 2, 2)])
        self.assertEqual(len(self.draft.data["rooms"]), 1)

    def test_right_click_abandons_room_drawing(self):
        rectangles = []
        self.canvas.room_drawn.connect(lambda *args: rectangles.append(args))
        self.canvas.tool = "room"
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(12, 5))
        self.move(15, 8)
        QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=self.point(15, 8))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(15, 8))
        self.assertEqual(rectangles, [])
        self.assertIsNone(self.canvas._room_preview)

    def test_room_selection_works_on_floor_and_explicit_room_tool(self):
        rooms = []
        self.canvas.room_selected.connect(rooms.append)
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(rooms, ["main"])
        self.draft.place_furniture(self.definition["id"], 6, 7)
        self.canvas.tool = "room-select"
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(rooms, ["main", "main"])
        self.assertEqual(self.canvas.selected_id, "")

    def test_topmost_furniture_on_rug_is_selected_first(self):
        data = self.draft.snapshot()
        rug = validate_definition({"id": "(F)Test.Rug", "kind": "rug", "footprint": [2, 3]})
        data["catalog"].append(rug)
        self.draft.apply(data)
        chair = self.draft.place_furniture(self.definition["id"], 6, 7)
        self.draft.place_furniture(rug["id"], 6, 7)
        self.assertEqual(self.canvas._at(6, 7)["id"], chair)

    def test_real_sprite_is_ghosted_and_tall_visible_top_is_selectable(self):
        with Image.new("RGBA", (16, 48), "#ff0000") as image:
            image.save(self.root / "chair.png")
        definition = dict(self.definition, preview_asset="chair.png",
                          frames=[{"rotation": 0, "rect": [0, 0, 16, 48], "duration_ms": 100}])
        data = self.draft.snapshot()
        data["catalog"] = [definition]
        self.draft.apply(data)
        identity = self.draft.place_furniture(definition["id"], 6, 7)
        self.assertEqual(self.canvas._at(6, 6)["id"], identity)
        self.canvas.set_placement(definition)
        self.move(9, 7)
        image = self.canvas.grab().toImage()
        # The top of the actual sprite is above its footprint, so this pixel
        # cannot come from the green placement rectangle.
        sample = image.pixelColor(self.point(9, 6))
        self.assertGreater(sample.red(), sample.green() + 80)

    def test_zoom_keeps_integral_pixels_and_tile_selection(self):
        self.canvas.set_scale(3)
        self.assertEqual(self.canvas.width(), self.draft.data["width"] * 48)
        edits = []
        self.canvas.tile_clicked.connect(lambda *args: edits.append(args))
        self.canvas.tool = "entry"
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(edits, [(6, 7)])
        with self.assertRaises(ValueError):
            self.canvas.set_scale(2.5)

    def test_leaving_canvas_clears_unplaced_ghost(self):
        self.canvas.set_placement(self.definition)
        self.move(6, 7)
        QApplication.sendEvent(self.canvas, QEvent(QEvent.Type.Leave))
        self.assertIsNone(self.canvas.ghost)
        self.assertEqual(self.draft.data["furniture"], [])


if __name__ == "__main__":
    unittest.main()
