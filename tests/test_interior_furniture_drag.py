"""Mouse furniture drags reach the room through Qt's native drop dispatch."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from tests.test_interior_decorating_flow import make_library


CHAIR = "(F)Test.Chair"
RUG = "(F)Test.Rug"


class InteriorFurnitureDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="pixelheart-furniture-drag-"))
        self.root = Path(self.directory)
        self.library = make_library(self.root / "library")
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.dialogs = []

    def tearDown(self):
        for dialog in reversed(self.dialogs):
            dialog.reject()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def editor(self, kind="residence"):
        dialog = InteriorEditor(self.root / f"project-{len(self.dialogs)}" / "character.json", kind=kind,
                                resident_name="Test resident", allow_rebase=True)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        if not dialog.catalog_list.count():
            with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[self.library]), \
                    patch("pixelheart.interior_editor.QDialog.exec", return_value=0):
                QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
        self.assertEqual(dialog.catalog_list.count(), 1)
        return dialog

    @staticmethod
    def state(dialog):
        return dialog.draft.snapshot(), deepcopy(dialog.draft._undo), deepcopy(dialog.draft._redo)

    @staticmethod
    def point(dialog, x, y):
        cell = dialog.canvas.scale * 16
        ox, oy = dialog.canvas.view_origin
        return QPoint((x + ox) * cell + 5, (y + oy) * cell + 5)

    def choose(self, dialog, identity=CHAIR):
        item = next(dialog.catalog_list.item(row) for row in range(dialog.catalog_list.count())
                    if dialog.catalog_list.item(row).data(Qt.ItemDataRole.UserRole) == identity)
        QTest.mouseClick(dialog.catalog_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.catalog_list.visualItemRect(item).center())
        self.assertEqual(dialog.canvas.tool, "place")

    def move_pointer(self, dialog, point):
        position = QPointF(point)
        self.app.sendEvent(dialog.canvas, QMouseEvent(QEvent.Type.MouseMove, position, position,
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def dispatch(self, event_type, dialog, source, mime, x, y):
        # Qt owns source() during a platform drag. Keep real Qt dispatch while
        # supplying that one field for a deterministic headless test.
        class SourcedEvent(event_type):
            def source(self):
                return source

        point = self.point(dialog, x, y)
        if event_type is QDropEvent:
            point = QPointF(point)
        event = SourcedEvent(point, Qt.DropAction.CopyAction, mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(dialog.canvas, event)
        return event

    def gesture(self, dialog, during_drag, *, expected_started=1):
        """Drive a genuine mouse threshold; substitute only the platform drag."""
        captured = {}

        class Drag:
            def __init__(self, source):
                captured["source"] = source

            def setMimeData(self, mime):
                captured["mime"] = mime

            def setPixmap(self, pixmap):
                captured["pixmap"] = pixmap

            def setHotSpot(self, point):
                captured["hotspot"] = point

            def exec(self, *args):
                captured["started"] = captured.get("started", 0) + 1
                return during_drag(captured["source"], captured["mime"])

        library = dialog.catalog_list
        start = library.visualItemRect(library.item(0)).center()
        destination = start + QPoint(QApplication.startDragDistance() + 12, 0)
        with patch("pixelheart.interior_editor.QDrag", Drag):
            QTest.mousePress(library.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(library.viewport(), start + QPoint(1, 0))
            self.assertNotIn("started", captured)
            QTest.mouseMove(library.viewport(), destination)
            QTest.mouseMove(library.viewport(), destination + QPoint(2, 0))
            QTest.mouseRelease(library.viewport(), Qt.MouseButton.LeftButton, pos=destination)
        self.app.processEvents()
        self.assertEqual(captured.get("started", 0), expected_started)
        if expected_started:
            self.assertIs(captured["source"], library)
            self.assertFalse(captured["pixmap"].isNull())
        return captured

    def native_drop(self, dialog, source, mime, x, y):
        self.assertTrue(dialog.canvas.acceptDrops())
        enter = self.dispatch(QDragEnterEvent, dialog, source, mime, x, y)
        self.assertTrue(enter.isAccepted())
        move = self.dispatch(QDragMoveEvent, dialog, source, mime, x, y)
        self.assertTrue(move.isAccepted())
        self.assertTrue(dialog.canvas.ghost["valid"])
        self.assertEqual((dialog.canvas.ghost["x"], dialog.canvas.ghost["y"]), (x, y))
        event = self.dispatch(QDropEvent, dialog, source, mime, x, y)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        return event.dropAction()

    def test_mouse_drag_cancellation_clears_preview_and_preserves_history(self):
        dialog = self.editor()
        before = self.state(dialog)

        def cancel(source, mime):
            enter = self.dispatch(QDragEnterEvent, dialog, source, mime, 6, 7)
            self.assertTrue(enter.isAccepted())
            self.assertIsNotNone(dialog.canvas.ghost)
            self.assertEqual(self.state(dialog), before)
            return Qt.DropAction.IgnoreAction

        self.gesture(dialog, cancel)
        self.assertEqual(self.state(dialog), before)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertIsNone(dialog.canvas._placement)
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertEqual(dialog.catalog_list.count(), 1)

    def test_drop_places_one_piece_and_immediately_allows_mouse_movement(self):
        for kind, start, end in (("residence", (6, 7), (8, 9)), ("spouse", (0, 6), (1, 6))):
            with self.subTest(kind=kind):
                dialog = self.editor(kind)
                before = dialog.draft.snapshot()
                undo_count = len(dialog.draft._undo)
                self.gesture(dialog, lambda source, mime: self.native_drop(dialog, source, mime, *start))
                self.assertEqual(len(dialog.draft.data["furniture"]), 1)
                placed = deepcopy(dialog.draft.data["furniture"][0])
                self.assertEqual((placed["item_id"], placed["x"], placed["y"]), (CHAIR, *start))
                self.assertEqual(len(dialog.draft._undo), undo_count + 1)
                self.assertEqual(dialog.canvas.tool, "select")
                self.assertEqual(dialog.tool.currentData(), "select")
                self.assertIsNone(dialog.canvas.ghost)
                self.assertIsNone(dialog.canvas._placement)
                self.assertIsNone(dialog.canvas.drag)
                self.assertEqual(dialog.selected_furniture, placed["id"])

                # Grab the lower half of the chair to retain its grab offset.
                QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton,
                                 pos=self.point(dialog, start[0], start[1] + 1))
                point = QPointF(self.point(dialog, end[0], end[1] + 1))
                self.app.sendEvent(dialog.canvas, QMouseEvent(QEvent.Type.MouseMove, point, point,
                                   Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                self.assertEqual(dialog.draft.data["furniture"], [placed])
                self.assertTrue(dialog.canvas.ghost["valid"])
                QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton,
                                   pos=self.point(dialog, end[0], end[1] + 1))
                self.assertEqual(len(dialog.draft.data["furniture"]), 1)
                moved = dialog.draft.data["furniture"][0]
                self.assertEqual((moved["id"], moved["x"], moved["y"]), (placed["id"], *end))
                self.assertEqual(len(dialog.draft._undo), undo_count + 2)
                dialog.undo()
                self.assertEqual(dialog.draft.data["furniture"], [placed])
                dialog.undo()
                self.assertEqual(dialog.draft.snapshot(), before)

    def test_drop_release_click_cannot_pick_up_another_copy_without_a_fresh_press(self):
        dialog = self.editor()
        before = self.state(dialog)

        def drop_and_emit_click(source, mime):
            action = self.native_drop(dialog, source, mime, 6, 7)
            # Some native event sequences deliver itemClicked before exec exits.
            source.itemClicked.emit(source.currentItem())
            self.assertEqual(dialog.canvas.tool, "select")
            self.assertIsNone(dialog.canvas._placement)
            return action

        self.gesture(dialog, drop_and_emit_click)
        # A delayed release signal must also remain part of the old gesture.
        dialog.catalog_list.itemClicked.emit(dialog.catalog_list.currentItem())
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertIsNone(dialog.canvas.drag)
        self.assertEqual(len(dialog.draft.data["furniture"]), 1)
        self.assertEqual(len(dialog.draft._undo), len(before[1]) + 1)
        after = self.state(dialog)
        self.move_pointer(dialog, self.point(dialog, 9, 8))
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 9, 8))
        self.assertEqual(self.state(dialog), after)
        self.assertIsNone(dialog.canvas.ghost)

        self.choose(dialog)
        self.move_pointer(dialog, self.point(dialog, 9, 8))
        self.assertIsNotNone(dialog.canvas.ghost)
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 9, 8))
        self.assertEqual(len(dialog.draft.data["furniture"]), 2)
        self.assertEqual(len(dialog.draft._undo), len(before[1]) + 2)
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)

    def test_invalid_drops_preserve_layout_and_undo_redo_history(self):
        for kind in ("residence", "spouse"):
            dialog = self.editor(kind)
            valid = (6, 7) if kind == "residence" else (0, 6)
            self.gesture(dialog, lambda source, mime: self.native_drop(dialog, source, mime, *valid))
            # Retain both history stacks so a failed drop cannot silently erase redo.
            second = (9, 7) if kind == "residence" else (5, 6)
            dialog.draft.place_furniture(CHAIR, *second)
            dialog.undo()
            before = self.state(dialog)
            anchor = tuple(dialog.draft.data["entry" if kind == "residence" else "spouse_stand"])
            for position in (valid, anchor, (-1, 6)):
                with self.subTest(kind=kind, position=position):
                    def invalid(source, mime):
                        enter = self.dispatch(QDragEnterEvent, dialog, source, mime, *valid)
                        self.assertTrue(enter.isAccepted())
                        self.dispatch(QDragMoveEvent, dialog, source, mime, *position)
                        self.assertIsNotNone(dialog.canvas.ghost)
                        self.assertFalse(dialog.canvas.ghost["valid"])
                        drop = self.dispatch(QDropEvent, dialog, source, mime, *position)
                        self.assertFalse(drop.isAccepted())
                        return Qt.DropAction.IgnoreAction

                    self.gesture(dialog, invalid)
                    self.assertEqual(self.state(dialog), before)
                    self.assertIsNone(dialog.canvas.ghost)

    def test_leaving_room_clears_preview_and_reenter_allows_one_drop(self):
        dialog = self.editor()
        before = self.state(dialog)

        def leave(source, mime):
            self.dispatch(QDragEnterEvent, dialog, source, mime, 6, 7)
            self.assertIsNotNone(dialog.canvas.ghost)
            self.app.sendEvent(dialog.canvas, QDragLeaveEvent())
            self.assertIsNone(dialog.canvas.ghost)
            self.assertEqual(self.state(dialog), before)
            return self.native_drop(dialog, source, mime, 6, 7)

        self.gesture(dialog, leave)
        self.assertEqual(len(dialog.draft.data["furniture"]), 1)
        self.assertEqual(len(dialog.draft._undo), len(before[1]) + 1)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), before[0])

    def test_other_editors_and_external_sources_cannot_place_furniture(self):
        dialog = self.editor()
        other = self.editor()
        before = self.state(dialog), self.state(other)

        def reject(source, mime):
            for target, drag_source in ((other, source), (dialog, None)):
                for event_type in (QDragEnterEvent, QDragMoveEvent, QDropEvent):
                    event = self.dispatch(event_type, target, drag_source, mime, 6, 7)
                    self.assertFalse(event.isAccepted())
            return Qt.DropAction.IgnoreAction

        self.gesture(dialog, reject)
        self.assertEqual((self.state(dialog), self.state(other)), before)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertIsNone(other.canvas.ghost)

    def test_malformed_and_unknown_item_payloads_cannot_place_furniture(self):
        dialog = self.editor()
        before = self.state(dialog)

        def reject(source, mime):
            for payload in (b"\xff", b"", b"(F)Missing"):
                with self.subTest(payload=payload):
                    altered = QMimeData()
                    for format_name in mime.formats():
                        altered.setData(format_name, payload)
                    for event_type in (QDragEnterEvent, QDragMoveEvent, QDropEvent):
                        event = self.dispatch(event_type, dialog, source, altered, 6, 7)
                        self.assertFalse(event.isAccepted())
                    self.assertIsNone(dialog.canvas.ghost)
                    self.assertEqual(self.state(dialog), before)
            return Qt.DropAction.IgnoreAction

        self.gesture(dialog, reject)
        self.assertEqual(self.state(dialog), before)

    def test_item_without_a_footprint_does_not_start_drag(self):
        dialog = self.editor()
        candidate = dialog.draft.snapshot()
        candidate["catalog"][0].pop("footprint")
        candidate["catalog"][0].pop("rotation_footprints", None)
        dialog.draft.apply(candidate)
        dialog.refresh()
        before = self.state(dialog)
        self.gesture(dialog, lambda *_: self.fail("An item without a known size must not start dragging"),
                     expected_started=0)
        self.assertEqual(self.state(dialog), before)
        self.assertIsNone(dialog.canvas.ghost)

    def test_core_placement_failure_rejects_drop_and_preserves_history(self):
        dialog = self.editor()
        before = self.state(dialog)

        def fail_at_commit(source, mime):
            self.dispatch(QDragEnterEvent, dialog, source, mime, 6, 7)
            move = self.dispatch(QDragMoveEvent, dialog, source, mime, 6, 7)
            self.assertTrue(move.isAccepted())
            self.assertTrue(dialog.canvas.ghost["valid"])
            with patch("pixelheart.interior_editor.InteriorDraft.place_furniture", side_effect=ValueError("Placement changed while dragging")):
                drop = self.dispatch(QDropEvent, dialog, source, mime, 6, 7)
            self.assertFalse(drop.isAccepted())
            self.assertEqual(self.state(dialog), before)
            return Qt.DropAction.IgnoreAction

        self.gesture(dialog, fail_at_commit)
        self.assertEqual(self.state(dialog), before)
        self.assertIsNone(dialog.canvas.ghost)

    def test_dragging_a_placed_piece_dismisses_an_explicitly_held_piece(self):
        dialog = self.editor()
        self.choose(dialog)
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 6, 7))
        self.choose(dialog)
        self.assertEqual(dialog.canvas.tool, "place")
        before = self.state(dialog)
        original = deepcopy(dialog.draft.data["furniture"][0])

        start = self.point(dialog, 6, 8)
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move_pointer(dialog, start + QPoint(1, 0))
        self.assertEqual(self.state(dialog), before)
        self.assertIsNone(dialog.canvas.drag)
        self.assertEqual(dialog.canvas.tool, "place")
        end = self.point(dialog, 8, 10)
        self.move_pointer(dialog, end)
        self.assertEqual(self.state(dialog), before)
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertEqual(dialog.tool.currentData(), "select")
        self.assertTrue(dialog.canvas.ghost["valid"])
        self.assertEqual((dialog.canvas.ghost["x"], dialog.canvas.ghost["y"]), (8, 9))
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)

        self.assertEqual(len(dialog.draft.data["furniture"]), 1)
        moved = dialog.draft.data["furniture"][0]
        self.assertEqual((moved["id"], moved["x"], moved["y"]), (original["id"], 8, 9))
        self.assertEqual(len(dialog.draft._undo), len(before[1]) + 1)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), before[0])

    def test_click_without_drag_still_places_furniture_above_or_below_a_rug(self):
        for first, second in ((RUG, CHAIR), (CHAIR, RUG)):
            with self.subTest(first=first, second=second):
                dialog = self.editor()
                candidate = dialog.draft.snapshot()
                rug = deepcopy(candidate["catalog"][0])
                rug.update(id=RUG, name="Test rug", kind="rug", footprint=[2, 2], rotations=1)
                rug.pop("rotation_footprints", None)
                rug["frames"] = [frame for frame in rug["frames"] if frame["rotation"] == 0]
                candidate["catalog"].append(rug)
                dialog.draft.apply(candidate)
                dialog.refresh()

                self.choose(dialog, first)
                point = self.point(dialog, 6, 7)
                QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
                self.choose(dialog, second)
                before = self.state(dialog)
                QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
                self.move_pointer(dialog, point + QPoint(1, 0))
                self.assertEqual(self.state(dialog), before)
                self.assertIsNone(dialog.canvas.drag)
                QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)

                self.assertEqual([item["item_id"] for item in dialog.draft.data["furniture"]], [first, second])
                self.assertTrue(all((item["x"], item["y"]) == (6, 7) for item in dialog.draft.data["furniture"]))
                self.assertEqual(dialog.canvas.tool, "select")
                self.assertIsNone(dialog.canvas._placement)
                self.assertIsNone(dialog.canvas.ghost)
                self.assertEqual(len(dialog.draft._undo), len(before[1]) + 1)
                dialog.undo()
                self.assertEqual(dialog.draft.snapshot(), before[0])

    def test_escape_cancels_pending_move_without_placing_or_changing_history(self):
        dialog = self.editor()
        self.choose(dialog)
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 6, 7))
        self.choose(dialog)
        before = self.state(dialog)
        start = self.point(dialog, 6, 8)
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move_pointer(dialog, start + QPoint(1, 0))
        self.assertEqual(self.state(dialog), before)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 8, 10))
        self.assertEqual(self.state(dialog), before)
        self.assertIsNone(dialog.canvas.drag)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertEqual(dialog.canvas.tool, "select")


if __name__ == "__main__":
    unittest.main()
