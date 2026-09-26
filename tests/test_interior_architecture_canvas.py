"""Static architectural pieces use independent, atomic canvas gestures."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interiors import InteriorDraft, new_interior, render_interior
from pixelheart_core.interior_architecture import architecture_candidate
from tests.qt_support import QtTestCase


def architecture_design(root):
    with Image.new("RGBA", (32, 16), "#746c5c") as atlas:
        for x in range(16, 32):
            for y in range(16):
                atlas.putpixel((x, y), (193, 81, 68, 255))
        atlas.save(root / "fixture.png")
    data = new_interior()
    data["atlas"] = {"asset": "fixture.png", "columns": 2, "tile_count": 2}
    data["architecture_catalog"] = [
        {"id": "fixture", "name": "Counter", "category": "Counters", "placement": "floor",
         "width": 2, "height": 2, "layers": {"Back": [None]*4, "Buildings": [None, None, 1, 1],
                                                "Front": [1, 1, None, None]}},
        {"id": "wall", "name": "Wall fixture", "category": "Wall details", "placement": "wall",
         "width": 2, "height": 2, "layers": {"Back": [None]*4, "Buildings": [None]*4, "Front": [1]*4}},
    ]
    return data


class ArchitectureCanvasTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.draft = InteriorDraft(architecture_design(self.root))
        self.canvas = InteriorCanvas(self.draft, root=self.root)
        self.canvas.tool = "architecture-select"
        self.canvas.architecture_candidate = self.candidate
        self.placements, self.moves = [], []
        self.canvas.architecture_placed.connect(self.place)
        self.canvas.architecture_moved.connect(self.move_piece)
        with render_interior(self.draft.data, self.root) as image:
            self.canvas.set_image(image)
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def candidate(self, piece_id, x, y, placement_id=None):
        return architecture_candidate(self.draft.data, piece_id, x, y, placement_id=placement_id)

    def place(self, piece_id, x, y):
        self.placements.append((piece_id, x, y))
        self.draft.apply(self.candidate(piece_id, x, y))

    def move_piece(self, identity, x, y):
        self.moves.append((identity, x, y))
        piece = next(piece for piece in self.draft.data["architecture"] if piece["id"] == identity)
        self.draft.apply(self.candidate(piece["piece_id"], x, y, identity))

    def point(self, x, y):
        cell = self.canvas.scale * 16
        return QPoint(x*cell + cell//2, y*cell + cell//2)

    def move(self, point, held=False):
        self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier))

    def state(self):
        return self.draft.snapshot(), deepcopy(self.draft._undo), deepcopy(self.draft._redo)

    def pick(self, piece_id="fixture"):
        self.canvas.set_architecture_placement(next(piece for piece in self.draft.data["architecture_catalog"] if piece["id"] == piece_id))

    def placed_piece(self, piece_id="fixture", x=6, y=7):
        self.draft.apply(self.candidate(piece_id, x, y))
        return self.draft.data["architecture"][-1]["id"]

    def test_pickup_preview_and_click_release_place_once_without_furniture(self):
        before = self.state()
        self.pick()
        self.move(self.point(6, 7))
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        self.assertEqual(self.canvas._architecture_preview, (6, 7, 2, 2))
        self.assertFalse(self.canvas._architecture_candidate_image.isNull())
        self.assertEqual(self.state(), before)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(self.placements, [("fixture", 6, 7)])
        self.assertEqual(len(self.draft.data["architecture"]), 1)
        self.assertEqual(self.draft.data["furniture"], [])
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
        self.assertEqual(self.placements, [("fixture", 6, 7), ("fixture", 9, 7)])

    def test_invalid_placement_shows_piece_ghost_and_preserves_history(self):
        self.pick()
        before = self.state()
        self.move(self.point(4, 9))
        self.assertFalse(self.canvas.preview_valid)
        self.assertTrue(self.canvas.preview_message)
        self.assertIsNone(self.canvas._architecture_candidate_data)
        self.assertFalse(self.canvas._architecture_ghost_image.isNull())
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(4, 9))
        self.assertEqual(self.placements, [])
        self.assertEqual(self.state(), before)

    def test_existing_piece_click_selects_and_subthreshold_motion_does_not_edit(self):
        identity = self.placed_piece()
        selected = []
        self.canvas.architecture_selected.connect(selected.append)
        before = self.state()
        point = self.point(7, 7)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=point)
        self.move(point + QPoint(2, 1), held=True)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(2, 1))
        self.assertEqual(selected, [identity])
        self.assertEqual(self.canvas.selected_architecture_id, identity)
        self.assertIsNone(self.canvas._architecture_preview)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_existing_piece_drag_preserves_grab_offset_and_commits_one_undo(self):
        identity = self.placed_piece()
        before = self.state()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.move(self.point(9, 8), held=True)
        self.assertEqual(self.canvas._architecture_preview, (8, 7, 2, 2))
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        self.assertFalse(self.canvas._architecture_candidate_image.isNull())
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 8))
        self.assertEqual(self.moves, [(identity, 8, 7)])
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
        self.assertIsNone(self.canvas._architecture_preview)
        self.draft.undo()
        self.assertEqual(self.draft.snapshot(), before[0])

    def test_wall_piece_can_be_selected_and_dragged_by_its_full_visible_face(self):
        identity = self.placed_piece("wall", 5, 2)
        self.assertEqual(self.canvas._architecture_at(6, 2)["id"], identity)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 2))
        self.move(self.point(8, 2), held=True)
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(8, 2))
        self.assertEqual(self.moves, [(identity, 7, 2)])

    def test_release_revalidates_changed_layout_and_rejects_overlap(self):
        self.placed_piece()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(self.point(9, 7), held=True)
        self.assertTrue(self.canvas.preview_valid)
        self.draft.apply(self.candidate("fixture", 9, 7))
        before = self.state()
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_release_rechecks_external_validator_even_when_data_id_is_unchanged(self):
        self.placed_piece()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(self.point(9, 7), held=True)
        self.assertTrue(self.canvas.preview_valid)
        def blocked(*args):
            raise ValueError("This destination is used by the story.")
        self.canvas.architecture_candidate = blocked
        before = self.state()
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_escape_right_click_and_mode_change_cancel_drag_and_late_release(self):
        self.placed_piece()
        for action in ("escape", "right", "mode"):
            self.canvas.tool = "architecture-select"
            before = self.state()
            QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
            self.move(self.point(9, 7), held=True)
            self.assertIsNotNone(self.canvas._architecture_drag)
            if action == "escape":
                QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
            elif action == "right":
                QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=self.point(9, 7))
            else:
                self.canvas.clear_room_interaction()
                self.canvas.tool = "select"
            QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
            self.assertIsNone(self.canvas._architecture_preview)
            self.assertIsNone(self.canvas._architecture_drag)
            self.assertEqual(self.moves, [])
            self.assertEqual(self.state(), before)

    def test_cancel_held_piece_between_press_and_release_never_places_it(self):
        before = self.state()
        self.pick()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(self.placements, [])
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.canvas._architecture_placement)

    def test_piece_from_disabled_room_is_not_selectable(self):
        data = self.draft.snapshot()
        data["rooms"].append({"id": "study", "name": "Study", "x": 12, "y": 5,
                              "width": 4, "height": 4, "enabled": True, "optional": True})
        self.draft.apply(data)
        self.draft.apply(self.candidate("fixture", 12, 5))
        data = self.draft.snapshot()
        data["rooms"][-1]["enabled"] = False
        self.draft.apply(data)
        self.assertIsNone(self.canvas._architecture_at(12, 5))

    def test_leaving_held_preview_clears_ghost_and_reentering_restores_it(self):
        self.pick()
        self.move(self.point(6, 7))
        self.assertIsNotNone(self.canvas._architecture_preview)
        self.app.sendEvent(self.canvas, QEvent(QEvent.Type.Leave))
        self.assertIsNone(self.canvas._architecture_preview)
        self.assertTrue(self.canvas._architecture_candidate_image.isNull())
        self.move(self.point(6, 7))
        self.assertTrue(self.canvas.preview_valid)
        self.assertIsNotNone(self.canvas._architecture_preview)


if __name__ == "__main__":
    unittest.main()
