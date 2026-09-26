"""Project undo takes precedence over native authored-text undo, not search."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QLineEdit, QMainWindow, QMenu, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from pixelheart.location_picker import MapSelector
from pixelheart.project_history import ProjectHistoryController
from pixelheart.stage_canvas import StageCanvas
from tests.qt_support import QtTestCase


class ProjectWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.loading = self.dirty = self._external_dirty = False
        self.record_id = "first-record"
        self.controller = ProjectHistoryController(self)
        self.controller.install_actions(self.menuBar().addMenu("Edit"))
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        self.name = QLineEdit()
        self.notes = QPlainTextEdit()
        self.search = QLineEdit()
        self.location = MapSelector()
        for widget in (self.name, self.notes, self.search, self.location):
            layout.addWidget(widget)
        self.gifts = SimpleNamespace(undo_preset_button=QPushButton("Undo preset"))
        layout.addWidget(self.gifts.undo_preset_button)
        self.name.textChanged.connect(self.changed)
        self.notes.textChanged.connect(self.changed)
        self.location.changed.connect(self.changed)
        self.controller.reset(self.project_snapshot())

    def project_snapshot(self):
        return {"name": self.name.text(), "notes": self.notes.toPlainText(), "map": self.location.value()}

    def restore_project_snapshot(self, snapshot):
        self.loading = True
        self.name.setText(snapshot["name"])
        self.notes.setPlainText(snapshot["notes"])
        self.location.set_value(snapshot["map"])
        self.loading = False

    def project_history_context(self):
        return (self.record_id,)

    def changed(self):
        if not self.loading:
            self.controller.record_current()

    def update_title(self):
        pass


class ProjectHistoryControlTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = ProjectWindow()
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.controller = self.window.controller

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def focus(self, widget):
        widget.setFocus()
        self.app.processEvents()
        self.assertIs(self.app.focusWidget(), widget)

    def shortcut(self, widget, standard):
        QTest.keySequence(widget, QKeySequence(standard))

    def test_paint_and_lifecycle_events_do_not_inspect_widget_ownership(self):
        with patch.object(self.controller, "_belongs_to_project", side_effect=AssertionError("unneeded widget traversal")):
            for kind in (QEvent.Type.Paint, QEvent.Type.DeferredDelete, QEvent.Type.ParentChange,
                         QEvent.Type.ChildRemoved, QEvent.Type.Polish, QEvent.Type.Resize):
                with self.subTest(kind=kind):
                    self.assertFalse(self.controller.eventFilter(self.window, QEvent(kind)))

    def test_typing_groups_and_keyboard_undo_follow_project_order(self):
        self.focus(self.window.name)
        QTest.keyClicks(self.window.name, "Mira")
        self.assertEqual(self.controller.history.undo_count, 1)
        self.focus(self.window.notes)
        QTest.keyClicks(self.window.notes, "Hello")
        self.assertEqual(self.controller.history.undo_count, 2)
        self.focus(self.window.name)
        self.shortcut(self.window.name, QKeySequence.StandardKey.Undo)
        self.assertEqual(self.window.name.text(), "Mira")
        self.assertEqual(self.window.notes.toPlainText(), "")
        self.shortcut(self.window.name, QKeySequence.StandardKey.Undo)
        self.assertEqual(self.window.name.text(), "")
        self.assertFalse(self.window.dirty)
        self.shortcut(self.window.name, QKeySequence.StandardKey.Redo)
        self.assertEqual(self.window.name.text(), "Mira")
        self.assertTrue(self.window.dirty)

    def test_reset_blocks_native_authored_text_undo(self):
        self.focus(self.window.name)
        QTest.keyClicks(self.window.name, "Saved")
        self.controller.reset(self.window.project_snapshot())
        self.assertTrue(self.window.name.isUndoAvailable())
        self.shortcut(self.window.name, QKeySequence.StandardKey.Undo)
        self.assertEqual(self.window.name.text(), "Saved")
        self.assertFalse(self.controller.undo_action.isEnabled())
        self.assertFalse(self.window.dirty)

    def test_same_field_on_different_records_and_cursor_moves_start_new_groups(self):
        self.focus(self.window.name)
        QTest.keyClicks(self.window.name, "a")
        self.window.record_id = "second-record"
        QTest.keyClicks(self.window.name, "b")
        QTest.keyClick(self.window.name, Qt.Key.Key_Left)
        QTest.keyClicks(self.window.name, "c")
        self.assertEqual(self.controller.history.undo_count, 3)
        self.controller.undo()
        self.assertEqual(self.window.name.text(), "ab")
        self.controller.undo()
        self.assertEqual(self.window.name.text(), "a")

    def test_search_keeps_native_undo_without_changing_project_history(self):
        self.window.name.setText("Project change")
        self.focus(self.window.search)
        QTest.keyClicks(self.window.search, "query")
        self.shortcut(self.window.search, QKeySequence.StandardKey.Undo)
        self.assertEqual(self.window.search.text(), "")
        self.assertEqual(self.window.name.text(), "Project change")
        self.assertEqual(self.controller.history.undo_count, 1)
        self.assertTrue(self.controller._transient_text(self.window.location.combo.lineEdit()))
        self.assertFalse(self.controller._transient_text(self.window.location.custom))

    def test_independent_dialog_keeps_native_undo_but_live_dialog_routes_project(self):
        self.window.name.setText("Project change")
        dialog = QDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        editor = QLineEdit(dialog)
        QVBoxLayout(dialog).addWidget(editor)
        dialog.show()
        dialog.activateWindow()
        self.focus(editor)
        QTest.keyClicks(editor, "draft")
        self.shortcut(editor, QKeySequence.StandardKey.Undo)
        self.assertEqual(editor.text(), "")
        self.assertEqual(self.window.name.text(), "Project change")
        dialog.setProperty("projectHistoryLive", True)
        self.shortcut(editor, QKeySequence.StandardKey.Undo)
        self.assertEqual(self.window.name.text(), "")
        dialog.close()

    def test_text_context_menu_uses_project_undo_and_preserves_copy_paste(self):
        self.window.name.setText("First edit")
        self.window.notes.setPlainText("Latest edit")
        self.focus(self.window.name)
        observed = []

        def choose_undo():
            menu = self.app.activePopupWidget()
            if not isinstance(menu, QMenu):
                return
            actions = menu.actions()
            observed.extend(action.text() for action in actions)
            actions[0].trigger()
            menu.close()

        QTimer.singleShot(0, choose_undo)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(2, 2), QPoint(2, 2))
        QApplication.sendEvent(self.window.name, event)
        self.assertTrue(any("Copy" in label for label in observed))
        self.assertTrue(any("Paste" in label for label in observed))
        self.assertEqual(self.window.name.text(), "First edit")
        self.assertEqual(self.window.notes.toPlainText(), "")

    def test_sync_preserves_external_dirty_and_suppresses_only_owned_legacy_controls(self):
        independent = QPushButton("Undo preset", self.window)
        self.addCleanup(independent.deleteLater)
        independent.show()
        self.window.gifts.undo_preset_button.show()
        self.window._external_dirty = True
        self.controller.sync()
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.gifts.undo_preset_button.isHidden())
        self.assertFalse(independent.isHidden())

    def test_active_gesture_groups_updates_across_pauses_and_ends_before_next_edit(self):
        with patch("pixelheart.project_history.time.monotonic", side_effect=(0, 5, 6)):
            self.controller.begin_gesture(("cast", "actor-1"))
            self.window.name.setText("first tile")
            self.window.name.setText("last tile")
            self.controller.end_gesture()
            self.window.name.setText("next edit")
        self.assertEqual(self.controller.history.undo_count, 2)
        self.controller.undo()
        self.assertEqual(self.window.name.text(), "last tile")
        self.controller.undo()
        self.assertEqual(self.window.name.text(), "")

    def test_cast_drag_records_one_step_and_focus_loss_finishes_remaining_gesture(self):
        canvas = StageCanvas()
        self.window.centralWidget().layout().addWidget(canvas)
        canvas.load([{"id": "actor", "name": "$npc", "x": 2, "y": 2, "facing": 2}])
        phases = []
        canvas.gestureStarted.connect(lambda: phases.append("start"))
        canvas.gestureFinished.connect(lambda: phases.append("finish"))
        canvas.gestureStarted.connect(lambda: self.controller.begin_gesture(("cast", id(canvas))))
        canvas.gestureFinished.connect(self.controller.end_gesture)
        canvas.actorMoved.connect(lambda index, x, y: self.window.name.setText(f"{x},{y}"))
        self.window.name.setText("2,2")
        self.controller.reset(self.window.project_snapshot())
        self.app.processEvents()

        def move(x, y):
            point = canvas.tile_point(x, y)
            event = QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(canvas, event)

        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=canvas.tile_point(2, 2).toPoint())
        move(3, 3)
        move(4, 4)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=canvas.tile_point(4, 4).toPoint())
        self.assertEqual(phases, ["start", "finish"])
        self.assertEqual(self.controller.history.undo_count, 1)
        self.assertEqual(self.window.name.text(), "4,4")
        self.controller.undo()
        self.assertEqual(self.window.name.text(), "2,2")
        self.controller.redo()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=canvas.tile_point(4, 4).toPoint())
        move(5, 5)
        self.focus(self.window.name)
        self.assertFalse(canvas.dragging)
        self.assertEqual(phases, ["start", "finish", "start", "finish"])
        self.assertEqual(self.window.name.text(), "5,5")
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=canvas.tile_point(5, 5).toPoint())
        self.assertEqual(phases.count("finish"), 2)
