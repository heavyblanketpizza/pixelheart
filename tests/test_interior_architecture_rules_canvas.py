"""Architectural placement previews show the floor space their rules reserve."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interior_architecture import architecture_candidate
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interior_levels import raised_room_candidate
from pixelheart_core.interiors import InteriorDraft, render_interior
from tests.qt_support import QtTestCase

try:
    from .test_interior_architecture_canvas import architecture_design
except ImportError:
    from test_interior_architecture_canvas import architecture_design


def steps_design(root, *, raised=False):
    data = architecture_design(root)
    data["architecture_catalog"].append({
        "id": "steps", "name": "Wooden steps", "category": "Stairs", "placement": "floor",
        "rules": "steps_corridor", "width": 2, "height": 2,
        "layers": {"Back": [1]*4, "Buildings": [None]*4, "Front": [None]*4},
    })
    if raised:
        data["rooms"][0]["y"] = 10
        data["entry"] = [4, 15]
        return raised_room_candidate(data, 4, 4, 8, 2)
    return data


class ArchitectureClearanceCanvasTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.draft = InteriorDraft(steps_design(self.root, raised=True))
        self.canvas = InteriorCanvas(self.draft, root=self.root)
        self.canvas.tool = "architecture-select"
        self.placements = []
        self.canvas.architecture_placed.connect(lambda *values: self.placements.append(values))
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
        return QPoint(x*cell + cell//2, y*cell + cell//2)

    def move(self, x, y):
        point = self.point(x, y)
        self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def pick(self, identity="steps"):
        self.canvas.set_architecture_placement(next(item for item in self.draft.data["architecture_catalog"]
                                                   if item["id"] == identity))

    def outlines(self):
        with patch.object(self.canvas, "_draw_outline", wraps=self.canvas._draw_outline) as draw:
            self.canvas.grab()
        return [(call.args[1], call.args[2]) for call in draw.call_args_list]

    def test_valid_steps_show_both_full_width_landings_without_covering_tread_art(self):
        self.pick()
        before = self.draft.snapshot(), deepcopy(self.draft._undo)
        self.canvas._preview_architecture_edit("steps", 7, 7, self.draft.data["architecture"][0]["id"], force=True)
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        self.assertEqual(self.canvas._architecture_clearance, {(x, y) for x in (7, 8) for y in (5, 6, 9, 10)})
        self.assertFalse(self.canvas._architecture_candidate_image.isNull())
        outlines = self.outlines()
        for x, y in self.canvas._architecture_clearance:
            self.assertIn(((x, y, 1, 1), "#7bb8dc"), outlines)
        self.assertEqual((self.draft.snapshot(), self.draft._undo), before)

    def test_invalid_steps_keep_landing_guides_and_explain_rejection_without_edit(self):
        self.pick()
        before = self.draft.snapshot(), deepcopy(self.draft._undo)
        self.move(3, 12)
        self.assertFalse(self.canvas.preview_valid)
        self.assertTrue(self.canvas.preview_message)
        self.assertEqual(self.canvas._architecture_clearance, {(3, 11), (4, 11), (3, 14), (4, 14)})
        self.assertFalse(self.canvas._architecture_ghost_image.isNull())
        outlines = self.outlines()
        for x, y in self.canvas._architecture_clearance:
            self.assertIn(((x, y, 1, 1), "#f38a87"), outlines)
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 12))
        self.assertEqual(self.placements, [])
        self.assertEqual((self.draft.snapshot(), self.draft._undo), before)

    def test_cancel_leave_and_change_piece_clear_stale_landing_guides(self):
        self.pick()
        self.move(7, 7)
        self.assertTrue(self.canvas._architecture_clearance)
        self.app.sendEvent(self.canvas, QEvent(QEvent.Type.Leave))
        self.assertEqual(self.canvas._architecture_clearance, set())
        self.move(7, 7)
        self.assertTrue(self.canvas._architecture_clearance)
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        self.assertEqual(self.canvas._architecture_clearance, set())
        self.pick()
        self.move(7, 7)
        self.canvas.clear_room_interaction()
        self.assertEqual(self.canvas._architecture_clearance, set())
        self.pick("fixture")
        self.move(3, 7)
        self.assertEqual(self.canvas._architecture_clearance, set())

    def test_chair_and_rug_ghosts_cannot_cover_treads_or_either_landing(self):
        before = self.draft.snapshot(), deepcopy(self.draft._undo)
        for kind in ("chair", "rug"):
            with self.subTest(kind=kind):
                definition = validate_definition({"id": "(F)Test." + kind, "name": kind,
                                                  "kind": kind, "footprint": [1, 1], "rotations": 1})
                self.canvas.set_placement(definition)
                for x, y in ((7, 7), (8, 8), (7, 6), (8, 9)):
                    with self.subTest(position=(x, y)):
                        self.move(x, y)
                        self.assertFalse(self.canvas.ghost["valid"])
                        self.assertIn("Wooden steps", self.canvas.preview_message)
                self.move(3, 12)
                self.assertTrue(self.canvas.ghost["valid"], self.canvas.preview_message)
        self.assertEqual((self.draft.snapshot(), self.draft._undo), before)

    def test_counter_front_rejects_chair_but_allows_walkable_rug(self):
        data = self.draft.snapshot()
        data["architecture_catalog"][0]["rules"] = "wall_backed"
        self.draft.apply(architecture_candidate(data, "fixture", 3, 9))
        for kind, expected in (("chair", False), ("rug", True)):
            with self.subTest(kind=kind):
                definition = validate_definition({"id": "(F)Test." + kind, "name": kind,
                                                  "kind": kind, "footprint": [1, 1], "rotations": 1})
                self.canvas.set_placement(definition)
                self.move(3, 11)
                self.assertEqual(self.canvas.ghost["valid"], expected, self.canvas.preview_message)
                if not expected:
                    self.assertIn("Counter", self.canvas.preview_message)


if __name__ == "__main__":
    unittest.main()
