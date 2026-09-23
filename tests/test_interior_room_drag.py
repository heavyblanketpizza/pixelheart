"""Room mouse gestures preview the exact edit and commit only accepted drops."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pixelheart.interior_canvas import InteriorCanvas, ROOM_MIME
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import InteriorDraft, new_interior, room_edit_candidate


class InteriorRoomDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        data = new_interior()
        data["catalog"] = [validate_definition({"id": "(F)Test.Chair", "kind": "chair",
                                                 "footprint": [1, 1]})]
        self.draft = InteriorDraft(data)
        self.canvas = InteriorCanvas(self.draft)
        self.canvas.tool = "room-select"
        self.canvas.room_candidate = self.candidate
        self.allow_rebase = False
        self.canvas.room_moved.connect(self.move_room)
        self.source = QWidget()
        self.canvas.room_catalogue_source = self.source
        self.canvas.room_dimensions = lambda: (4, 4)
        self.canvas.place_room_drop = self.add_room
        self.moves = []
        self.additions = []
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        for widget in (self.canvas, self.source):
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def candidate(self, identity, x, y, width, height):
        return room_edit_candidate(self.draft.data, room_id=identity, x=x, y=y,
                                   width=width, height=height, allow_rebase=self.allow_rebase)

    def move_room(self, identity, x, y):
        self.moves.append((identity, x, y))
        self.draft.apply(room_edit_candidate(self.draft.data, room_id=identity, x=x, y=y))

    def add_room(self, x, y, width, height):
        self.additions.append((x, y, width, height))
        return self.draft.apply(self.candidate(None, x, y, width, height))

    def state(self):
        return self.draft.snapshot(), deepcopy(self.draft._undo), deepcopy(self.draft._redo)

    def point(self, x, y):
        cell = self.canvas.scale * 16
        return QPoint(x * cell + 5, y * cell + 5)

    def move_pointer(self, point):
        self.app.sendEvent(self.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def room(self):
        return self.draft.add_room("Study", 12, 5, 4, 4)

    def mime(self, payload=b"new-room"):
        mime = QMimeData()
        mime.setData(ROOM_MIME, payload)
        return mime

    def dispatch(self, event_type, x, y, *, source=None, mime=None):
        actual_source = self.source if source is None else source
        mime = mime or self.mime()

        class SourcedEvent(event_type):
            def source(self):
                return actual_source

        # Tests specify the proposed top-left; the user holds a preset at its
        # center, including when snapping onto an adjacent room's edge.
        width, height = self.canvas.room_dimensions()
        point = self.point(x + width // 2, y + height // 2)
        if event_type is QDropEvent:
            point = QPointF(point)
        event = SourcedEvent(point, Qt.DropAction.CopyAction, mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        event._test_mime = mime  # QDropEvent keeps a non-owning MIME pointer.
        self.app.sendEvent(self.canvas, event)
        return event

    def test_click_and_subthreshold_movement_select_without_editing(self):
        identity = self.room()
        selected = []
        self.canvas.room_selected.connect(selected.append)
        before = self.state()
        start = self.point(14, 7)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move_pointer(start + QPoint(1, 0))
        self.assertIsNone(self.canvas._room_drag)
        self.assertIsNone(self.canvas._room_preview)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(1, 0))
        self.assertEqual(selected, [identity])
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_existing_room_drag_preserves_grab_offset_and_carries_furniture_once(self):
        identity = self.room()
        furniture = self.draft.place_furniture("(F)Test.Chair", 14, 7)
        before = self.state()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 7))
        self.move_pointer(self.point(14, 12))
        self.assertEqual(self.canvas.selected_room_id, identity)
        self.assertEqual(self.canvas.selected_id, "")
        self.assertIsNone(self.canvas.drag)
        self.assertEqual(self.canvas._room_preview, (12, 10, 4, 4))
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 12))
        self.assertEqual(self.moves, [(identity, 12, 10)])
        placed = next(item for item in self.draft.data["furniture"] if item["id"] == furniture)
        self.assertEqual((placed["x"], placed["y"]), (14, 12))
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
        self.assertIsNone(self.canvas._room_preview)
        self.draft.undo()
        self.assertEqual(self.draft.snapshot(), before[0])

    def test_snapped_preview_matches_committed_room(self):
        identity = self.room()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 7))
        self.move_pointer(self.point(15, 11))
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.canvas._room_preview, (12, 9, 4, 4))
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(15, 11))
        self.assertEqual(self.moves, [(identity, 13, 9)])
        room = next(room for room in self.draft.data["rooms"] if room["id"] == identity)
        self.assertEqual((room["x"], room["y"]), (12, 9))

    def test_invalid_room_drag_never_emits_an_edit_or_changes_history(self):
        self.room()
        self.draft.place_furniture("(F)Test.Chair", 14, 7)
        self.draft.undo()
        before = self.state()
        for destination in ((11, 10), (23, 17), (14, 1)):
            with self.subTest(destination=destination):
                QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 7))
                self.move_pointer(self.point(*destination))
                self.assertFalse(self.canvas.preview_valid)
                self.assertTrue(self.canvas.preview_message)
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(*destination))
                self.assertEqual(self.moves, [])
                self.assertEqual(self.state(), before)
                self.assertIsNone(self.canvas._room_preview)

    def test_escape_right_click_and_mode_switch_cancel_room_drag(self):
        self.room()
        before = self.state()
        for cancellation in ("escape", "right-click", "mode"):
            with self.subTest(cancellation=cancellation):
                self.canvas.tool = "room-select"
                QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 7))
                self.move_pointer(self.point(14, 12))
                self.assertIsNotNone(self.canvas._room_preview)
                if cancellation == "escape":
                    QTest.keyClick(self.canvas, Qt.Key.Key_Escape)
                elif cancellation == "right-click":
                    QTest.mouseClick(self.canvas, Qt.MouseButton.RightButton, pos=self.point(14, 12))
                else:
                    self.canvas.clear_room_interaction()
                    self.canvas.tool = "select"
                QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 12))
                self.assertEqual(self.moves, [])
                self.assertEqual(self.state(), before)
                self.assertIsNone(self.canvas._room_preview)

    def test_native_room_drop_previews_snap_and_commits_once(self):
        before = self.state()
        self.assertTrue(self.dispatch(QDragEnterEvent, 13, 5).isAccepted())
        self.assertTrue(self.dispatch(QDragMoveEvent, 13, 5).isAccepted())
        self.assertEqual(self.canvas._room_preview, (12, 5, 4, 4))
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.dispatch(QDropEvent, 13, 5).isAccepted())
        self.assertEqual(self.additions, [(13, 5, 4, 4)])
        self.assertEqual(len(self.draft.data["rooms"]), 2)
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)
        self.assertIsNone(self.canvas._room_preview)
        self.assertIsNone(self.canvas.room_catalogue_drag)
        self.draft.undo()
        self.assertEqual(self.draft.snapshot(), before[0])

    def test_native_medium_and_large_rooms_can_reach_left_edge_and_preview_rebase(self):
        self.allow_rebase = True
        self.canvas.project_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for width in (6, 8):
            with self.subTest(width=width):
                self.canvas.room_dimensions = lambda: (width, 6)
                before = self.state()
                # The pointer is on canvas tile zero. The wider preset's
                # actual center would otherwise be outside the canvas.
                proposed_x = -width // 2
                self.assertTrue(self.dispatch(QDragEnterEvent, proposed_x, 5).isAccepted())
                self.assertTrue(self.canvas.preview_valid)
                self.assertFalse(self.canvas._room_candidate_image.isNull())
                shown = self.canvas._room_candidate_data
                self.assertEqual(shown["rooms"][-1]["x"], 1)
                self.assertEqual(shown["rooms"][0]["x"], width + 1)
                self.assertEqual(self.state(), before)
                self.assertTrue(self.dispatch(QDropEvent, proposed_x, 5).isAccepted())
                self.assertEqual(self.draft.data["rooms"][0]["x"], width + 1)
                self.draft.undo()
                self.assertEqual(self.draft.snapshot(), before[0])

    def test_native_drop_after_switching_mode_cannot_add_a_room(self):
        before = self.state()
        self.assertTrue(self.dispatch(QDragEnterEvent, 12, 5).isAccepted())
        self.canvas.clear_room_interaction()
        self.canvas.tool = "select"
        self.assertFalse(self.dispatch(QDropEvent, 12, 5).isAccepted())
        self.assertEqual(self.additions, [])
        self.assertEqual(self.state(), before)

    def test_drawn_room_uses_the_same_snapped_preview_and_commit(self):
        self.canvas.tool = "room"
        self.canvas.room_drawn.connect(self.add_room)
        before = self.state()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(13, 5))
        self.move_pointer(self.point(15, 8))
        self.assertEqual(self.canvas._room_preview, (12, 5, 3, 4))
        # Repeated pointer events within a tile retain the snapped outline.
        self.move_pointer(self.point(15, 8) + QPoint(1, 1))
        self.assertEqual(self.canvas._room_preview, (12, 5, 3, 4))
        self.assertTrue(self.canvas.preview_valid)
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(15, 8))
        room = self.draft.data["rooms"][-1]
        self.assertEqual(tuple(room[key] for key in ("x", "y", "width", "height")), (12, 5, 3, 4))
        self.assertEqual(len(self.draft._undo), len(before[1]) + 1)

    def test_invalid_or_canceled_native_drop_preserves_history(self):
        before = self.state()
        self.assertTrue(self.dispatch(QDragEnterEvent, 12, 5).isAccepted())
        self.assertFalse(self.dispatch(QDragMoveEvent, 5, 7).isAccepted())
        self.assertFalse(self.canvas.preview_valid)
        self.assertFalse(self.dispatch(QDropEvent, 5, 7).isAccepted())
        self.assertEqual(self.additions, [])
        self.assertEqual(self.state(), before)
        self.assertTrue(self.dispatch(QDragEnterEvent, 12, 5).isAccepted())
        self.app.sendEvent(self.canvas, QDragLeaveEvent())
        self.assertIsNone(self.canvas._room_preview)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.dispatch(QDragEnterEvent, 12, 5).isAccepted())
        self.canvas.clear_room_catalogue_drag()
        self.assertIsNone(self.canvas._room_preview)
        self.assertEqual(self.state(), before)

    def test_external_sources_and_malformed_payloads_cannot_add_rooms(self):
        before = self.state()
        other = QWidget()
        try:
            for source, payload in ((other, b"new-room"), (self.source, b""),
                                    (self.source, b"other-room"), (self.source, b"\xff")):
                for event_type in (QDragEnterEvent, QDragMoveEvent, QDropEvent):
                    event = self.dispatch(event_type, 12, 5, source=source, mime=self.mime(payload))
                    self.assertFalse(event.isAccepted())
                self.assertIsNone(self.canvas._room_preview)
            self.assertEqual(self.additions, [])
            self.assertEqual(self.state(), before)
        finally:
            other.deleteLater()

    def test_release_revalidates_when_the_layout_changed_during_drag(self):
        self.room()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 7))
        self.move_pointer(self.point(14, 12))
        self.assertTrue(self.canvas.preview_valid)
        self.draft.add_room("Other room", 12, 10, 4, 4)
        before = self.state()
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(14, 12))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)

    def test_spouse_room_cannot_be_dragged_to_change_its_fixed_layout(self):
        self.draft = InteriorDraft(new_interior("spouse"))
        self.canvas.draft = self.draft
        self.canvas.refresh_size()
        before = self.state()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(2, 5))
        self.move_pointer(self.point(3, 6))
        self.assertFalse(self.canvas.preview_valid)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.point(3, 6))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
