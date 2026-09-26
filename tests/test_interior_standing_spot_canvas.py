"""The spouse heart proposes one validated edit when a drag is released."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import InteriorDraft, new_interior, normalize_interior, validate_spouse_access


class StandingSpotCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.draft = InteriorDraft(new_interior("spouse"))
        self.canvas = InteriorCanvas(self.draft)
        self.moves, self.clicks = [], []
        self.canvas.spouse_stand_moved.connect(lambda x, y: self.moves.append((x, y)))
        self.canvas.tile_clicked.connect(lambda x, y: self.clicks.append((x, y)))
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def point(self, x, y):
        ox, oy = self.canvas.view_origin
        cell = self.canvas.scale * 16
        return QPoint((x + ox) * cell + cell // 2, (y + oy) * cell + cell // 2)

    def move(self, x, y, *, held=True):
        point = QPointF(self.point(x, y))
        self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, point, point,
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier))

    def state(self):
        return self.draft.snapshot(), deepcopy(self.draft._undo), deepcopy(self.draft._redo)

    def begin_drag(self, x=2, y=6):
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*self.draft.data["spouse_stand"]))
        self.move(x, y)

    def test_drag_works_in_each_select_mode_with_context_offsets_at_every_zoom(self):
        before = self.state()
        for mode in ("select", "room-select", "spouse_stand"):
            for scale in (1, 3, 8):
                with self.subTest(mode=mode, scale=scale):
                    self.canvas.tool = mode
                    self.canvas.set_scale(scale)
                    self.move(3, 5, held=False)
                    self.assertEqual(self.canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
                    self.begin_drag()
                    self.assertEqual(self.canvas.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
                    self.assertEqual(self.canvas._spouse_stand_preview, (2, 6))
                    self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
                    self.assertEqual(self.state(), before)
                    QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 6))
                    self.assertEqual(self.moves[-1], (2, 6))
                    self.assertEqual(self.state(), before)
                    self.assertIsNone(self.canvas._spouse_stand_preview)
                    self.assertIsNone(self.canvas._spouse_stand_drag)
        self.assertEqual(len(self.moves), 9)
        self.assertEqual(self.clicks, [])

    def test_click_and_small_motion_do_not_move_the_heart(self):
        before = self.state()
        start = self.point(3, 5)
        for mode in ("select", "room-select", "spouse_stand"):
            self.canvas.tool = mode
            QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
            point = QPointF(start + QPoint(1, 1))
            self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, point, point,
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier))
            QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(1, 1))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.clicks, [(3, 5)])
        self.assertEqual(self.state(), before)

    def test_drag_back_to_origin_leaves_history_untouched(self):
        before = self.state()
        self.begin_drag()
        self.move(3, 5)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 5))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_blocked_wall_and_surrounding_destinations_show_red_and_never_emit_edits(self):
        data = self.draft.snapshot()
        data["catalog"] = [validate_definition({"id": "(F)Chair", "kind": "chair", "footprint": [1, 1]})]
        self.draft.apply(data)
        self.draft.place_furniture("(F)Chair", 2, 6)
        before = self.state()
        for destination in ((2, 6), (2, 2), (-1, 6), (6, 6), (2, 9)):
            with self.subTest(destination=destination):
                self.begin_drag(*destination)
                self.assertEqual(self.canvas._spouse_stand_preview, destination)
                self.assertFalse(self.canvas.preview_valid)
                self.assertTrue(self.canvas.preview_message)
                color = self.canvas.grab().toImage().pixelColor(self.point(*destination) + QPoint(5, 5))
                self.assertGreater(color.red(), color.green())
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*destination))
                self.assertEqual(self.moves, [])
                self.assertEqual(self.state(), before)
                self.assertFalse(self.canvas.preview_valid)
                self.assertTrue(self.canvas.preview_message)

    def test_click_tool_previews_valid_green_floor_and_still_emits_click(self):
        self.canvas.tool = "spouse_stand"
        before = self.state()
        self.move(2, 6, held=False)
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.canvas._spouse_stand_preview, (2, 6))
        color = self.canvas.grab().toImage().pixelColor(self.point(2, 6) + QPoint(5, 5))
        self.assertGreater(color.green(), color.red())
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 6))
        self.assertEqual(self.clicks, [(2, 6)])
        self.assertEqual(self.state(), before)
        self.app.sendEvent(self.canvas, QEvent(QEvent.Type.Leave))
        self.assertIsNone(self.canvas._spouse_stand_preview)

    def test_release_revalidates_candidate_even_when_target_is_unchanged(self):
        calls = []
        blocked = [False]
        def candidate(x, y):
            calls.append((x, y))
            if blocked[0]:
                raise ValueError("The target became blocked.")
            data = self.draft.snapshot()
            data["spouse_stand"] = [x, y]
            return normalize_interior(data)
        self.canvas.spouse_stand_candidate = candidate
        before = self.state()
        self.begin_drag()
        self.assertTrue(self.canvas.preview_valid)
        blocked[0] = True
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 6))
        self.assertEqual(calls, [(2, 6), (2, 6)])
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_editor_receives_single_edit_with_undo_and_redo(self):
        def candidate(x, y):
            data = self.draft.snapshot()
            data["spouse_stand"] = [x, y]
            data = normalize_interior(data)
            validate_spouse_access(data, before=self.draft.data)
            return data
        self.canvas.spouse_stand_candidate = candidate
        self.canvas.spouse_stand_moved.connect(lambda x, y: self.draft.apply(candidate(x, y)))
        before = self.state()
        self.begin_drag(2, 6)
        self.move(1, 6)
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 7))
        self.assertEqual(self.moves, [(2, 7)])
        self.assertEqual(self.draft.data["spouse_stand"], [2, 7])
        self.assertEqual(len(self.draft._undo), 1)
        self.draft.undo()
        self.assertEqual(self.draft.data["spouse_stand"], [3, 5])
        self.draft.redo()
        self.assertEqual(self.draft.data["spouse_stand"], [2, 7])

    def test_cancellation_and_tool_changes_discard_preview_and_ignore_late_release(self):
        for action in ("escape", "right", "editor", "place", "direct-tool-change"):
            with self.subTest(action=action):
                self.canvas.tool = "select"
                before = self.state()
                self.begin_drag()
                if action == "escape":
                    QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
                elif action == "right":
                    QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=self.point(2, 6))
                elif action == "editor":
                    self.canvas.clear_room_interaction()
                elif action == "place":
                    self.canvas.set_placement(validate_definition({"id": "(F)Rug", "kind": "rug", "footprint": [1, 1]}))
                else:
                    self.canvas.tool = "surface"
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 6))
                self.assertEqual(self.moves, [])
                self.assertEqual(self.state(), before)
                self.assertIsNone(self.canvas._spouse_stand_preview)
                self.assertIsNone(self.canvas._spouse_stand_drag)
                self.assertIsNone(self.canvas._pending_spouse_stand_move)

    def test_heart_does_not_intercept_placement_or_surface_tools(self):
        before = self.state()
        for mode in ("place", "surface"):
            self.canvas.tool = mode
            QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 5))
            self.assertIsNone(self.canvas._pending_spouse_stand_move)
        self.assertEqual(self.clicks, [(3, 5), (3, 5)])
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_residence_never_exposes_hidden_spouse_marker_as_a_drag_target(self):
        self.canvas.draft = InteriorDraft(new_interior())
        self.canvas.refresh_size()
        self.canvas.tool = "room-select"
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 5))
        self.assertIsNone(self.canvas._pending_spouse_stand_move)
        self.assertIsNotNone(self.canvas._pending_room_move)


if __name__ == "__main__":
    unittest.main()
