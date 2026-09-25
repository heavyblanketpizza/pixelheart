"""Moving furnished tables keeps their native tabletop contents visible and intact."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import InteriorDraft, new_interior, render_interior


class InteriorTabletopCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with Image.new("RGBA", (32, 64), "#d03020") as image:
            image.paste("#2040f0", (0, 48, 16, 64))
            image.save(self.root / "furniture.png")
        table = validate_definition({
            "id": "(F)Test.Table", "name": "Table", "kind": "table",
            "footprint": [2, 2], "sprite_size": [2, 3], "rotations": 1,
            "held_item_offsets": {"0": [8, -4]},
            "preview_asset": "furniture.png",
            "frames": [{"rotation": 0, "rect": [0, 0, 32, 48], "duration_ms": 100}],
        })
        decor = validate_definition({
            "id": "(F)Test.Goblet", "name": "Goblet", "kind": "decor",
            "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1,
            "preview_asset": "furniture.png",
            "frames": [{"rotation": 0, "rect": [0, 48, 16, 16], "duration_ms": 100}],
        })
        chair = validate_definition({
            "id": "(F)Test.Chair", "name": "Chair", "kind": "chair",
            "footprint": [1, 1], "rotations": 1,
        })
        design = new_interior()
        design["catalog"] = [table, decor, chair]
        design["furniture"] = [
            {"id": "table", "item_id": table["id"], "x": 6, "y": 7, "rotation": 0,
             "held_item": {"item_id": decor["id"], "mod_data": {"Test/Variant": "silver"}}},
            {"id": "chair", "item_id": chair["id"], "x": 3, "y": 6, "rotation": 0},
        ]
        self.draft = InteriorDraft(design)
        self.canvas = InteriorCanvas(self.draft, root=self.root)
        self.canvas.moved.connect(self.draft.move_furniture)
        with render_interior(self.draft.data, self.root) as image:
            self.canvas.set_image(image)
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def point(self, x, y):
        cell = self.canvas.scale * 16
        return QPoint(x * cell + 5, y * cell + 5)

    def move(self, x, y):
        position = QPointF(self.point(x, y))
        self.app.sendEvent(self.canvas, QMouseEvent(
            QEvent.Type.MouseMove, position, position, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.app.processEvents()

    def test_drag_moves_table_and_goblet_together_as_one_undoable_edit(self):
        before = self.draft.snapshot()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(8, 9)
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        self.assertEqual(self.draft.snapshot(), before)

        # Native 2×2 tables center a one-tile decoration at (8, -4) pixels.
        # Check the actual painted canvas, not just the ghost's bookkeeping.
        scale = self.canvas.scale
        color = self.canvas.grab().toImage().pixelColor((8 * 16 + 16) * scale,
                                                       (9 * 16 + 4) * scale)
        self.assertGreater(color.blue(), color.red() + 80)
        self.assertGreater(color.blue(), color.green() + 50)
        old_color = self.canvas._drag_image.toImage().pixelColor(6 * 16 + 16, 7 * 16 + 4)
        self.assertLess(abs(old_color.blue() - old_color.red()), 80)

        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(8, 9))
        table = next(item for item in self.draft.data["furniture"] if item["id"] == "table")
        self.assertEqual((table["x"], table["y"]), (8, 9))
        self.assertEqual(table["held_item"], before["furniture"][0]["held_item"])
        self.assertTrue(self.draft.undo())
        self.assertEqual(self.draft.snapshot(), before)
        self.assertFalse(self.draft.undo())

    def test_other_furniture_can_move_in_a_room_with_a_decorated_table(self):
        held_before = self.draft.snapshot()["furniture"][0]["held_item"]
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 6))
        self.move(4, 7)
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(4, 7))
        chair = next(item for item in self.draft.data["furniture"] if item["id"] == "chair")
        self.assertEqual((chair["x"], chair["y"]), (4, 7))
        self.assertEqual(self.draft.data["furniture"][0]["held_item"], held_before)

    def test_canceling_blocked_table_drag_preserves_contents_and_history(self):
        before = self.draft.snapshot()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(4, 10)
        self.assertFalse(self.canvas.preview_valid)
        self.assertIn("entry", self.canvas.preview_message)
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(4, 10))
        self.assertEqual(self.draft.snapshot(), before)
        self.assertFalse(self.draft.undo())


if __name__ == "__main__":
    unittest.main()
