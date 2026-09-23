"""Room gestures use the editor's real preview, commit, and undo paths."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pixelheart.interior_editor import InteriorEditor
from tests.test_interior_decorating_flow import make_library


class RoomEditorDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.enterContext(patch('pixelheart.interior_editor.game_import_settings', return_value=settings))
        self.dialog = InteriorEditor(self.root / 'project' / 'character.json', allow_rebase=True)
        self.dialog.show()
        self.app.processEvents()
        self.dialog.tabs.setCurrentIndex(2)

    def tearDown(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        self.app.processEvents()

    def point(self, x, y):
        cell = 16 * self.dialog.canvas.scale
        return QPoint(x * cell + 5, y * cell + 5)

    def mouse_move(self, widget, point):
        QApplication.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def native_drag(self, x, y, *, drop=True):
        owner = self
        class Drag:
            def __init__(self, source):
                self.source = source
            def setMimeData(self, mime): self.mime = mime
            def setPixmap(self, pixmap): pass
            def setHotSpot(self, point): pass
            def exec(self, action):
                source = self.source
                class Enter(QDragEnterEvent):
                    def source(self): return source
                class Drop(QDropEvent):
                    def source(self): return source
                point = owner.point(x, y)
                enter = Enter(point, Qt.DropAction.CopyAction, self.mime,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(owner.dialog.canvas, enter)
                owner.assertTrue(enter.isAccepted())
                owner.assertTrue(owner.dialog.canvas.preview_valid)
                owner.assertEqual(owner.dialog.draft.snapshot(), owner.before)
                owner.assertEqual(owner.dialog.draft._undo, owner.history)
                if not drop:
                    return Qt.DropAction.IgnoreAction
                event = Drop(QPointF(point), Qt.DropAction.CopyAction, self.mime,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(owner.dialog.canvas, event)
                owner.assertTrue(event.isAccepted())
                return Qt.DropAction.CopyAction
        self.before = self.dialog.draft.snapshot()
        self.history = deepcopy(self.dialog.draft._undo)
        preset = self.dialog.room_preset
        with patch('pixelheart.interior_editor.QDrag', Drag):
            start = preset.rect().center()
            QTest.mousePress(preset, Qt.MouseButton.LeftButton, pos=start)
            self.mouse_move(preset, start + QPoint(QApplication.startDragDistance() + 5, 0))
            QTest.mouseRelease(preset, Qt.MouseButton.LeftButton, pos=start)

    def test_new_room_is_dragged_from_panel_and_drop_is_one_undo(self):
        self.assertEqual(self.dialog.canvas.tool, 'room-select')
        self.assertTrue(self.dialog.load_catalog(make_library(self.root / 'library')))
        self.assertEqual(self.dialog.canvas.room_candidate(None, 12, 5, 6, 6)['catalog'], [])
        captions = {control.text() for control in self.dialog.findChildren(QPushButton)}
        self.assertFalse(captions & {'← Left', 'Right →', '↓ Below'})
        self.native_drag(15, 8)  # Center of a 6x6 room at (12, 5).
        room = self.dialog.draft.data['rooms'][-1]
        self.assertEqual((room['x'], room['y'], room['width'], room['height']), (12, 5, 6, 6))
        self.assertEqual(len(self.dialog.draft._undo), len(self.history) + 1)
        self.assertEqual(len(self.dialog.draft.data['catalog']), 1)
        self.assertEqual(self.dialog.canvas.tool, 'room-select')
        self.assertIsNone(self.dialog.canvas._room_preview)
        self.dialog.undo()
        self.assertEqual(self.dialog.draft.snapshot(), self.before)
        self.dialog.redo()
        self.assertEqual(len(self.dialog.draft.data['rooms']), 2)

    def test_native_room_drag_cancel_never_creates_a_room(self):
        self.native_drag(15, 8, drop=False)
        self.assertEqual(self.dialog.draft.snapshot(), self.before)
        self.assertEqual(self.dialog.draft._undo, self.history)
        self.assertIsNone(self.dialog.canvas._room_preview)
        self.assertIsNone(self.dialog.canvas.room_catalogue_drag)
        self.assertEqual(self.dialog.canvas.tool, 'room-select')

    def test_drag_existing_furnished_room_carries_it_and_can_undo(self):
        dialog = self.dialog
        self.assertTrue(dialog.load_catalog(make_library(self.root / 'library')))
        self.assertTrue(dialog.place_room_drop(12, 5, 4, 4))
        room_id = dialog.draft.data['rooms'][-1]['id']
        item_id = dialog.draft.place_furniture(dialog.draft.data['catalog'][0]['id'], 13, 6)
        dialog.refresh()
        before = dialog.draft.snapshot()
        history = len(dialog.draft._undo)
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(13, 6))
        self.mouse_move(dialog.canvas, self.point(13, 10))
        self.assertTrue(dialog.canvas.preview_valid)
        self.assertEqual(dialog.draft.snapshot(), before)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(13, 10))
        room = next(room for room in dialog.draft.data['rooms'] if room['id'] == room_id)
        item = next(item for item in dialog.draft.data['furniture'] if item['id'] == item_id)
        self.assertEqual((room['x'], room['y']), (12, 9))
        self.assertEqual((item['x'], item['y']), (13, 10))
        self.assertEqual(len(dialog.draft._undo), history + 1)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), before)

    def test_switching_tabs_cancels_pending_room_move(self):
        dialog = self.dialog
        before = dialog.draft.snapshot()
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(5, 6))
        self.mouse_move(dialog.canvas, self.point(7, 8))
        self.assertIsNotNone(dialog.canvas._room_preview)
        dialog.tabs.setCurrentIndex(0)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertEqual(dialog.draft._undo, [])
        self.assertIsNone(dialog.canvas._room_preview)
