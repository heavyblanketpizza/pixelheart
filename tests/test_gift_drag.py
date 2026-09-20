"""Gift gestures and native event dispatch reach the assignment editor."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from pixelheart.gifts_page import GIFT_MIME, GiftsPage
from pixelheart.item_icons import ItemIconStore


TULIP = "(O)591"


class GiftDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pixelheart-gift-drag-")
        root = Path(self.temp.name)
        store = ItemIconStore(root / "textures", root / "wiki-icons")
        self.icon_patch = patch("pixelheart.gifts_page.ItemIconStore", return_value=store)
        self.icon_patch.start()
        self.pages = []
        self.page = self.make_page()

    def tearDown(self):
        for page in reversed(self.pages):
            page.close()
            page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.icon_patch.stop()
        self.temp.cleanup()

    def make_page(self):
        page = GiftsPage()
        self.pages.append(page)
        page.resize(1300, 900)
        page.show()
        self.app.processEvents()
        return page

    @staticmethod
    def mime(values):
        mime = QMimeData()
        mime.setData(GIFT_MIME, json.dumps(values).encode("utf-8"))
        return mime

    def dispatch(self, event_type, receiver, source, mime):
        # Qt only supplies source() for an active platform drag. Keep the real
        # event and widget dispatcher, supplying only that platform-owned field.
        class SourcedEvent(event_type):
            def source(self):
                return source

        point = QPointF(12, 12) if event_type is QDropEvent else QPoint(12, 12)
        event = SourcedEvent(point, Qt.DropAction.MoveAction, mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(receiver, event)
        return event

    def assert_highlight(self, target, expected):
        self.assertEqual(bool(target.property("dropTarget")), expected)
        self.assertEqual(bool(target.drop_area.property("dropTarget")), expected)

    def drop(self, source, target, *, panel):
        receiver = target.drop_area if panel else target.viewport()
        self.assertTrue(receiver.acceptDrops())
        mime = self.mime([TULIP])
        enter = self.dispatch(QDragEnterEvent, receiver, source, mime)
        self.assertTrue(enter.isAccepted())
        self.assert_highlight(target, True)
        move = self.dispatch(QDragMoveEvent, receiver, source, mime)
        self.assertTrue(move.isAccepted())
        event = self.dispatch(QDropEvent, receiver, source, mime)
        self.assertTrue(event.isAccepted())
        self.assertEqual(event.dropAction(), Qt.DropAction.MoveAction)
        self.assert_highlight(target, False)

    def test_mouse_gesture_starts_tulip_drag_and_cancel_preserves_assignments(self):
        self.page.search.setText("Tulip")
        self.app.processEvents()
        library = self.page.library
        tulip = next(library.item(row) for row in range(library.count())
                     if library.item(row).data(Qt.ItemDataRole.UserRole) == TULIP)
        before = self.page.dump()
        changes = QSignalSpy(self.page.changed)
        captured = {}
        page = self.page

        class Drag:
            def __init__(self, source):
                captured["source"] = source

            def setMimeData(self, mime):
                captured["values"] = json.loads(bytes(mime.data(GIFT_MIME)))

            def setPixmap(self, pixmap):
                captured["pixmap"] = pixmap

            def setHotSpot(self, point):
                pass

            def exec(self, *args):
                captured["started"] = captured.get("started", 0) + 1
                # Cancellation must also remove feedback left by a drag target.
                page.lists["love"].drop_feedback(True)
                return Qt.DropAction.IgnoreAction

        point = library.visualItemRect(tulip).center()
        destination = point + QPoint(QApplication.startDragDistance() + 12, 0)
        with patch("pixelheart.gifts_page.QDrag", Drag):
            QTest.mousePress(library.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseMove(library.viewport(), destination)
            QTest.mouseMove(library.viewport(), destination + QPoint(2, 0))
            QTest.mouseRelease(library.viewport(), Qt.MouseButton.LeftButton, pos=destination)
        self.assertEqual(captured.get("started"), 1)
        self.assertIs(captured["source"], library)
        self.assertEqual(captured["values"], [TULIP])
        self.assertFalse(captured["pixmap"].isNull())
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(changes.count(), 0)
        for target in [library, *self.page.lists.values()]:
            self.assert_highlight(target, False)

    def test_native_drops_move_tulip_between_tastes_and_restore_default(self):
        for panel in (False, True):
            with self.subTest(surface="panel" if panel else "viewport"):
                changes = QSignalSpy(self.page.changed)
                self.drop(self.page.library, self.page.lists["love"], panel=panel)
                self.assertEqual(self.page.dump()["love"], [TULIP])
                self.drop(self.page.lists["love"], self.page.lists["hate"], panel=panel)
                self.assertEqual(self.page.dump()["love"], [])
                self.assertEqual(self.page.dump()["hate"], [TULIP])
                self.drop(self.page.lists["hate"], self.page.library, panel=panel)
                self.assertTrue(all(not values for values in self.page.dump().values()))
                self.assertEqual(changes.count(), 3)

    def test_native_drag_leave_clears_feedback_without_assigning(self):
        before = self.page.dump()
        for panel in (False, True):
            with self.subTest(surface="panel" if panel else "viewport"):
                target = self.page.lists["love"]
                receiver = target.drop_area if panel else target.viewport()
                mime = self.mime([TULIP])
                enter = self.dispatch(QDragEnterEvent, receiver, self.page.library, mime)
                self.assertTrue(enter.isAccepted())
                self.assert_highlight(target, True)
                self.app.sendEvent(receiver, QDragLeaveEvent())
                self.assert_highlight(target, False)
                self.assertEqual(self.page.dump(), before)

    def test_native_drag_from_another_editor_is_rejected(self):
        other = self.make_page()
        self.page.assign_items([TULIP], "like")
        before = self.page.dump()
        for panel in (False, True):
            with self.subTest(surface="panel" if panel else "viewport"):
                target = self.page.lists["love"]
                receiver = target.drop_area if panel else target.viewport()
                mime = self.mime([TULIP])
                for event_type in (QDragEnterEvent, QDragMoveEvent, QDropEvent):
                    event = self.dispatch(event_type, receiver, other.library, mime)
                    self.assertFalse(event.isAccepted())
                self.assert_highlight(target, False)
                self.assertEqual(self.page.dump(), before)


if __name__ == "__main__":
    unittest.main()
