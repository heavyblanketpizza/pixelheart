"""Floorplan handles preview edits without changing the saved draft."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interiors import InteriorDraft, new_interior, normalize_interior, place_doorway
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interior_layout import corridor_candidate, partition_candidate, opening_candidate


class FloorplanCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.draft = InteriorDraft(new_interior())
        self.canvas = InteriorCanvas(self.draft)
        self.canvas.tool = "room-select"
        self.canvas.selected_room_id = "main"
        self.canvas.room_resize_candidate = self.candidate
        self.canvas.room_resized.connect(self.resize)
        self.resizes = []
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def candidate(self, identity, x, y, width, height):
        candidate = self.draft.snapshot()
        room = next(room for room in candidate["rooms"] if room["id"] == identity)
        room.update(x=x, y=y, width=width, height=height)
        candidate["width"] = max(candidate["width"], x + width + 1)
        candidate["height"] = max(candidate["height"], y + height + 1)
        return normalize_interior(candidate)

    def resize(self, identity, *rectangle):
        self.resizes.append((identity, *rectangle))
        self.draft.apply(self.candidate(identity, *rectangle))

    def state(self):
        return self.draft.snapshot(), deepcopy(self.draft._undo), deepcopy(self.draft._redo)

    def point(self, x, y):
        return QPoint(x * self.canvas.scale * 16, y * self.canvas.scale * 16)

    def handle(self, name):
        return QPoint(*self.canvas._resize_handles()[name])

    def move(self, point, held=True):
        self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier))

    def test_all_eight_handles_resize_with_the_opposite_edges_anchored(self):
        cases = {"n": ((0, -1), (2, 4, 10, 9)), "s": ((0, 1), (2, 5, 10, 9)),
                 "e": ((1, 0), (2, 5, 11, 8)), "w": ((-1, 0), (1, 5, 11, 8)),
                 "nw": ((-1, -1), (1, 4, 11, 9)), "ne": ((1, -1), (2, 4, 11, 9)),
                 "sw": ((-1, 1), (1, 5, 11, 9)), "se": ((1, 1), (2, 5, 11, 9))}
        for handle, (delta, rectangle) in cases.items():
            with self.subTest(handle=handle):
                before = self.state()
                start = self.handle(handle)
                self.move(start, held=False)
                self.assertEqual(self.canvas.cursor().shape(), self.canvas._resize_cursor(handle))
                end = start + self.point(*delta)
                QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
                self.move(end)
                self.assertEqual(self.canvas._room_preview, rectangle)
                self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
                self.assertEqual(self.state(), before)
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
                self.assertEqual(self.resizes[-1], ("main", *rectangle))
                self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
                self.assertIsNone(self.canvas._room_resize)
                self.assertIsNone(self.canvas._room_preview)
                self.draft.undo()

    def test_pixel_grab_offset_does_not_change_the_opposite_edge(self):
        start = self.handle("e") + QPoint(6, -6)
        end = start + self.point(1, 0)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertEqual(self.canvas._room_preview, (2, 5, 11, 8))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.resizes, [("main", 2, 5, 11, 8)])

    def test_click_small_motion_and_drag_back_to_origin_do_not_edit(self):
        before = self.state()
        start = self.handle("e")
        for delta in (QPoint(0, 0), QPoint(2, 1)):
            QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
            self.move(start + delta)
            QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=start + delta)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(start + self.point(1, 0))
        self.move(start)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(self.resizes, [])
        self.assertEqual(self.state(), before)

    def test_invalid_shrink_preserves_furniture_and_history(self):
        data = self.draft.snapshot()
        data["catalog"] = [validate_definition({"id": "(F)Chair", "kind": "chair", "footprint": [1, 1]})]
        self.draft.apply(data)
        self.draft.place_furniture("(F)Chair", 11, 8)
        before = self.state()
        start = self.handle("e")
        end = start - self.point(2, 0)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertFalse(self.canvas.preview_valid)
        self.assertTrue(self.canvas.preview_message)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.resizes, [])
        self.assertEqual(self.state(), before)

    def test_resize_release_revalidates_after_draft_changes(self):
        start = self.handle("e")
        end = start - self.point(1, 0)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertTrue(self.canvas.preview_valid)
        data = self.draft.snapshot()
        data["catalog"] = [validate_definition({"id": "(F)Chair", "kind": "chair", "footprint": [1, 1]})]
        self.draft.apply(data)
        self.draft.place_furniture("(F)Chair", 11, 8)
        before = self.state()
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(self.resizes, [])
        self.assertEqual(self.state(), before)

    def test_escape_right_click_and_editor_cancellation_clear_resize_and_late_release(self):
        for action in ("escape", "right", "editor"):
            with self.subTest(action=action):
                self.canvas.tool = "room-select"
                before = self.state()
                start = self.handle("e")
                end = start + self.point(1, 0)
                QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
                self.move(end)
                if action == "escape":
                    QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
                elif action == "right":
                    QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=end)
                else:
                    self.canvas.clear_room_interaction()
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
                self.assertEqual(self.resizes, [])
                self.assertEqual(self.state(), before)
                self.assertIsNone(self.canvas._room_resize)
                self.assertIsNone(self.canvas._pending_room_resize)
                self.assertIsNone(self.canvas._room_preview)

    def test_doorway_wins_when_it_overlaps_a_resize_handle(self):
        self.draft.apply(place_doorway(self.draft.data, 7, 12))
        start = self.handle("s")
        self.move(start, held=False)
        self.assertEqual(self.canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.assertIsNotNone(self.canvas._pending_doorway_move)
        self.assertIsNone(self.canvas._pending_room_resize)
        self.canvas.clear_room_interaction()

    def test_spouse_and_furniture_modes_do_not_show_or_hit_room_handles(self):
        self.canvas.tool = "select"
        self.assertEqual(self.canvas._resize_handles(), {})
        self.canvas.tool = "room-select"
        self.canvas.draft = InteriorDraft(new_interior("spouse"))
        self.assertEqual(self.canvas._resize_handles(), {})

    def test_resize_uses_its_own_callback_and_rejects_implicit_snap(self):
        calls = []
        self.canvas.room_candidate = lambda *args: calls.append(args)
        def shifted(identity, x, y, width, height):
            return self.candidate(identity, x + 1, y, width, height)
        self.canvas.room_resize_candidate = shifted
        start = self.handle("e")
        end = start + self.point(1, 0)
        before = self.state()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertFalse(self.canvas.preview_valid)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(calls, [])
        self.assertEqual(self.resizes, [])
        self.assertEqual(self.state(), before)

    def test_valid_resize_renders_candidate_without_mutating_draft(self):
        self.canvas.project_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        before = self.state()
        start = self.handle("e")
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(start + self.point(1, 0))
        self.assertFalse(self.canvas._room_candidate_image.isNull())
        self.assertEqual(self.canvas._room_candidate_data["rooms"][0]["width"], 11)
        self.assertEqual(self.state(), before)

    def test_one_tile_hallway_drawn_backwards_previews_and_commits_once(self):
        self.canvas.tool = "corridor"
        self.canvas.corridor_candidate = lambda *rectangle: corridor_candidate(self.draft.data, *rectangle)
        additions = []
        def add(*rectangle):
            additions.append(rectangle)
            self.draft.apply(self.canvas.corridor_candidate(*rectangle))
        self.canvas.corridor_drawn.connect(add)
        before = self.state()
        start, end = self.point(15, 7) + QPoint(5, 5), self.point(12, 7) + QPoint(5, 5)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertEqual(self.canvas._room_preview, (12, 7, 4, 1))
        self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(additions, [(12, 7, 4, 1)])
        self.assertEqual(self.draft.data["rooms"][-1]["kind"], "hallway")
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
        self.assertIsNone(self.canvas._room_preview)

    def test_partition_drawing_chooses_major_axis_and_normalizes_backwards_segment(self):
        self.canvas.project_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.canvas.tool = "partition"
        self.canvas.partition_candidate = lambda *segment: partition_candidate(self.draft.data, "main", *segment)
        edits = []
        self.canvas.partition_drawn.connect(lambda *segment: edits.append(segment))
        cases = (((10, 8), (3, 7), ("horizontal", 3, 8, 8), (3, 6, 8, 3)),
                 ((5, 11), (6, 6), ("vertical", 5, 6, 6), (5, 6, 1, 6)))
        for start, end, segment, rectangle in cases:
            with self.subTest(segment=segment):
                before = self.state()
                QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*start) + QPoint(5, 5))
                self.move(self.point(*end) + QPoint(5, 5))
                self.assertTrue(self.canvas.preview_valid, self.canvas.preview_message)
                self.assertEqual(self.canvas._room_preview, rectangle)
                self.assertFalse(self.canvas._room_candidate_image.isNull())
                self.assertEqual(self.state(), before)
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*end) + QPoint(5, 5))
                self.assertEqual(edits[-1], segment)

    def test_hallway_preview_uses_core_snap_but_emits_the_authored_rectangle(self):
        self.canvas.tool = "corridor"
        self.canvas.corridor_candidate = lambda *rectangle: corridor_candidate(self.draft.data, *rectangle)
        edits = []
        self.canvas.corridor_drawn.connect(lambda *rectangle: edits.append(rectangle))
        start, end = self.point(13, 7) + QPoint(5, 5), self.point(16, 7) + QPoint(5, 5)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move(end)
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.canvas._room_preview, (12, 7, 4, 1))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(edits, [(13, 7, 4, 1)])

    def test_invalid_corridor_and_partition_releases_do_not_propose_edits(self):
        self.canvas.corridor_candidate = lambda *rectangle: corridor_candidate(self.draft.data, *rectangle)
        self.canvas.partition_candidate = lambda *segment: partition_candidate(self.draft.data, "main", *segment)
        edits = []
        self.canvas.corridor_drawn.connect(lambda *args: edits.append(args))
        self.canvas.partition_drawn.connect(lambda *args: edits.append(args))
        for kind, start, end in (("corridor", (3, 6), (5, 6)), ("partition", (0, 8), (5, 8))):
            self.canvas.tool = kind
            before = self.state()
            QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*start) + QPoint(5, 5))
            self.move(self.point(*end) + QPoint(5, 5))
            self.assertFalse(self.canvas.preview_valid)
            self.assertTrue(self.canvas.preview_message)
            QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*end) + QPoint(5, 5))
            self.assertEqual(edits, [])
            self.assertEqual(self.state(), before)

    def test_floorplan_draw_cancellation_clears_preview_and_late_release(self):
        self.canvas.partition_candidate = lambda *segment: partition_candidate(self.draft.data, "main", *segment)
        edits = []
        self.canvas.partition_drawn.connect(lambda *segment: edits.append(segment))
        for action in ("escape", "right", "editor"):
            self.canvas.tool = "partition"
            before = self.state()
            start, end = self.point(3, 8) + QPoint(5, 5), self.point(9, 8) + QPoint(5, 5)
            QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
            self.move(end)
            self.assertTrue(self.canvas.preview_valid)
            if action == "escape":
                QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
            elif action == "right":
                QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=end)
            else:
                self.canvas.clear_room_interaction()
            QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(edits, [])
            self.assertEqual(self.state(), before)
            self.assertIsNone(self.canvas._structure_start)
            self.assertIsNone(self.canvas._room_preview)

    def test_wall_and_its_opening_select_partition_without_starting_room_move(self):
        self.draft.apply(partition_candidate(self.draft.data, "main", "horizontal", 2, 8, 10))
        identity = self.draft.data["partitions"][0]["id"]
        selections = []
        self.canvas.partition_selected.connect(selections.append)
        before = self.state()
        for x in (5, 6):
            point = self.point(x, 8) + QPoint(5, 5)
            self.move(point, held=False)
            self.assertEqual(self.canvas.cursor().shape(), Qt.CursorShape.PointingHandCursor)
            QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=point)
            self.assertIsNone(self.canvas._pending_room_move)
            self.assertEqual(self.canvas.selected_partition_id, identity)
        self.assertEqual(selections, [identity, identity])
        self.assertEqual(self.state(), before)

    def test_resize_handle_has_priority_over_partition_base(self):
        self.draft.apply(partition_candidate(self.draft.data, "main", "horizontal", 10, 9, 2, opening_width=0))
        selected = []
        self.canvas.partition_selected.connect(selected.append)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.handle("e") - QPoint(5, 0))
        self.assertIsNotNone(self.canvas._pending_room_resize)
        self.assertEqual(selected, [])

    def test_opening_tool_previews_full_width_and_reacts_to_context_changes(self):
        self.draft.apply(partition_candidate(self.draft.data, "main", "horizontal", 2, 8, 10))
        wall = self.draft.data["partitions"][0]
        self.canvas.selected_partition_id = wall["id"]
        self.canvas.tool = "opening"
        self.canvas.project_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        width = [2]
        self.canvas.opening_candidate = lambda x, y: opening_candidate(self.draft.data, wall["id"], x-wall["x"], width[0])
        point = self.point(5, 8) + QPoint(5, 5)
        before = self.state()
        self.move(point, held=False)
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.canvas._room_preview, (5, 6, 2, 3))
        self.assertFalse(self.canvas._room_candidate_image.isNull())
        width[0] = 3
        self.move(point, held=False)
        self.assertEqual(self.canvas._room_preview, (5, 6, 3, 3))
        clicks = []
        self.canvas.tile_clicked.connect(lambda x, y: clicks.append((x, y)))
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(clicks, [(5, 8)])
        self.assertEqual(self.state(), before)
        QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
        self.assertIsNone(self.canvas._room_preview)
        self.assertTrue(self.canvas._room_candidate_image.isNull())


if __name__ == "__main__":
    unittest.main()
