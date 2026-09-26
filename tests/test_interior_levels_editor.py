"""Raised rooms are a single room-and-stairway edit in the existing room tools."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart.interior_canvas import ROOM_MIME
from tests.qt_support import QtTestCase

try:
    from .test_interior_architecture_canvas import architecture_design
except ImportError:
    from test_interior_architecture_canvas import architecture_design


def level_design(root):
    data = architecture_design(root)
    data["height"] = 28
    data["rooms"][0]["y"] = 13
    data["entry"] = [4, 18]
    data["architecture_catalog"].append({
        "id": "stardew.stairs", "name": "Wooden steps", "category": "Stairs", "placement": "floor",
        "rules": "steps_corridor", "width": 2, "height": 2,
        "layers": {"Back": [1]*4, "Buildings": [None]*4, "Front": [None]*4},
    })
    return data


class InteriorLevelsEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.source = level_design(self.root)
        self.editor = InteriorEditor(self.root / "character.json", self.source, allow_rebase=True)
        self.editors = [self.editor]
        self.editor.show()
        self.app.processEvents()
        self.editor.tabs.setCurrentIndex(2)
        self.editor.room_type.setCurrentIndex(self.editor.room_type.findData("raised"))
        self.editor.room_size.setCurrentIndex(0)
        self.editor.room_name.setText("Reading room")

    def tearDown(self):
        self.app.processEvents()
        for editor in self.editors:
            editor.reject()
            editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def point(self, x, y):
        cell = 16*self.editor.canvas.scale
        return QPoint(x*cell + cell//2, y*cell + cell//2)

    def move(self, point, widget=None):
        self.app.sendEvent(widget or self.editor.canvas,
                           QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier))

    def state(self):
        return (self.editor.draft.snapshot(), deepcopy(self.editor.draft._undo), deepcopy(self.editor._room_offsets),
                deepcopy(self.editor._offsets_undo), deepcopy(self.editor._offsets_redo))

    def pair(self):
        rooms = self.editor.draft.data["rooms"]
        upper = next(room for room in rooms if room.get("level") == 1)
        connector = next(room for room in rooms if room.get("kind") == "stairway")
        return upper, connector

    def add(self):
        self.assertTrue(self.editor.place_room_drop(4, 5, 4, 4), self.editor.status.text())
        return self.pair()

    def assert_pair(self):
        upper, connector = self.pair()
        self.assertEqual(upper["name"], "Reading room")
        self.assertEqual((upper["x"], upper["y"], upper["width"], upper["height"]), (4, 5, 4, 4))
        self.assertEqual((connector["width"], connector["height"]), (2, 4))
        self.assertEqual(connector["upper_room_id"], upper["id"])
        self.assertEqual(connector["lower_room_id"], "main")
        self.assertEqual(self.editor.draft.data["architecture"][0]["room_id"], connector["id"])
        self.assertEqual(self.editor.room_list.count(), 2)
        self.assertEqual(self.editor.canvas.selected_room_id, upper["id"])
        self.assertTrue(self.editor.design_summary.text().startswith("2 rooms"))

    def test_draw_preview_adds_floor_level_and_steps_atomically_and_can_undo_redo(self):
        before = self.state()
        self.editor.set_tool("room")
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(4, 5))
        self.move(self.point(7, 8))
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.canvas._room_preview, (4, 5, 4, 4))
        self.assertEqual(len(self.editor.canvas._room_candidate_data["rooms"]), 3)
        self.assertEqual(self.state(), before)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.assert_pair()
        self.assertEqual(len(self.editor.draft._undo), len(before[1])+1)
        after = self.editor.draft.snapshot()
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before[0])
        self.editor.redo()
        self.assertEqual(self.editor.draft.snapshot(), after)

    def test_preset_drag_uses_raised_room_choice_and_upper_room_preview(self):
        owner = self
        before = self.state()
        class Drag:
            def __init__(self, source): self.source = source
            def setMimeData(self, mime): self.mime = mime
            def setPixmap(self, pixmap): pass
            def setHotSpot(self, point): pass
            def exec(self, action):
                source = self.source
                class Enter(QDragEnterEvent):
                    def source(self): return source
                class Drop(QDropEvent):
                    def source(self): return source
                point = owner.point(6, 7)
                enter = Enter(point, Qt.DropAction.CopyAction, self.mime,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                owner.app.sendEvent(owner.editor.canvas, enter)
                owner.assertTrue(enter.isAccepted())
                owner.assertTrue(owner.editor.canvas.preview_valid, owner.editor.canvas.preview_message)
                owner.assertEqual(owner.editor.canvas._room_preview, (4, 5, 4, 4))
                owner.assertEqual(owner.state(), before)
                drop = Drop(QPointF(point), Qt.DropAction.CopyAction, self.mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                owner.app.sendEvent(owner.editor.canvas, drop)
                owner.assertTrue(drop.isAccepted())
                return Qt.DropAction.CopyAction
        preset = self.editor.room_preset
        with patch("pixelheart.interior_editor.QDrag", Drag):
            point = preset.rect().center()
            QTest.mousePress(preset, Qt.MouseButton.LeftButton, pos=point)
            self.move(point + QPoint(QApplication.startDragDistance()+5, 0), preset)
            QTest.mouseRelease(preset, Qt.MouseButton.LeftButton, pos=point)
        self.assert_pair()
        self.assertEqual(len(self.editor.draft._undo), len(before[1])+1)

    def test_exact_add_honors_room_type_and_optional_state_is_linked(self):
        self.editor.room_optional.setChecked(True)
        for key, value in dict(x=4, y=5, width=4, height=4).items():
            self.editor.room_fields[key].setValue(value)
        self.assertTrue(self.editor.add_room(), self.editor.status.text())
        self.assert_pair()
        upper, connector = self.pair()
        self.assertTrue(upper["optional"])
        self.assertEqual(connector["optional"], upper["optional"])
        self.assertEqual(connector["enabled"], upper["enabled"])

    def test_changing_room_type_cancels_pending_draw_without_creating_any_part(self):
        before = self.state()
        self.editor.set_tool("room")
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(4, 5))
        self.move(self.point(7, 8))
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.editor.room_type.setCurrentIndex(self.editor.room_type.findData("ordinary"))
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.editor.canvas._room_preview)
        self.assertEqual(self.editor.canvas.tool, "room-select")

    def test_missing_native_steps_and_wrong_attachment_reject_whole_edit(self):
        before = self.state()
        self.assertFalse(self.editor.place_room_drop(12, 13, 4, 4))
        self.assertEqual(self.state(), before)
        data = self.editor.draft.snapshot()
        data["architecture_catalog"] = [piece for piece in data["architecture_catalog"] if piece["id"] != "stardew.stairs"]
        self.editor.draft.apply(data)
        before = self.state()
        self.assertFalse(self.editor.place_room_drop(4, 5, 4, 4))
        self.assertEqual(self.state(), before)
        self.assertIn("library", self.editor.status.text().lower())

    def test_connector_click_selects_upper_room_and_has_no_separate_resize_handles(self):
        upper, connector = self.add()
        self.editor.canvas.selected_room_id = connector["id"]
        self.assertEqual(self.editor.canvas._resize_handles(), {})
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton,
                         pos=self.point(connector["x"], connector["y"]+1))
        self.assertEqual(self.editor.canvas.selected_room_id, upper["id"])
        self.assertEqual(self.editor._selected_room()["id"], upper["id"])
        self.assertEqual(self.editor.room_list.count(), 2)

    def test_connected_horizontal_move_and_top_resize_preserve_compound_with_history(self):
        upper, connector = self.add()
        before = self.editor.draft.snapshot()
        self.assertTrue(self.editor.move_room(upper["id"], upper["x"]+1, upper["y"]), self.editor.status.text())
        moved_upper, moved_connector = self.pair()
        self.assertEqual(moved_connector["x"], connector["x"]+1)
        self.assertEqual(self.editor.draft.data["architecture"][0]["x"], before["architecture"][0]["x"]+1)
        self.assertTrue(self.editor.resize_room(moved_upper["id"], moved_upper["x"], moved_upper["y"]-1,
                                                moved_upper["width"], moved_upper["height"]+1), self.editor.status.text())
        resized = self.editor.draft.snapshot()
        upper, _ = self.pair()
        self.assertFalse(self.editor.resize_room(upper["id"], upper["x"], upper["y"], upper["width"], upper["height"]+1))
        self.assertEqual(self.editor.draft.snapshot(), resized)
        self.editor.undo()
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), before)

    def test_save_and_reopen_preserve_compound_level_and_single_visible_room_entry(self):
        self.add()
        before = self.editor.draft.snapshot()
        self.editor.save_design()
        self.assertIsNotNone(self.editor.result_design, self.editor.status.text())
        reopened = InteriorEditor(self.root / "character.json", self.editor.result_design)
        self.editors.append(reopened)
        self.assertEqual(reopened.draft.snapshot(), before)
        self.assertEqual(reopened.room_list.count(), 2)
        self.assertEqual(reopened.tabs.count(), 3)

    def test_step_catalogue_pick_opens_compound_room_creation_without_a_dead_end_stamp(self):
        before = self.state()
        self.editor.layout_mode.setCurrentIndex(self.editor.layout_mode.findData("architecture"))
        catalog = self.editor.architecture_panel.catalog
        item = next(catalog.item(row) for row in range(catalog.count())
                    if catalog.item(row).data(Qt.ItemDataRole.UserRole) == "stardew.stairs")
        QTest.mouseClick(catalog.viewport(), Qt.MouseButton.LeftButton, pos=catalog.visualItemRect(item).center())
        self.assertEqual(self.editor.layout_mode.currentData(), "rooms")
        self.assertEqual(self.editor.room_type.currentData(), "raised")
        self.assertEqual(self.editor.canvas.tool, "room-select")
        self.assertIsNone(self.editor.canvas._architecture_placement)
        self.assertIn("raised room", self.editor.status.text().lower())
        self.assertEqual(self.state(), before)

    def test_linked_steps_keep_room_level_actions_and_preview_outer_approaches(self):
        upper, connector = self.add()
        item = self.editor.draft.data["architecture"][0]
        self.editor.layout_mode.setCurrentIndex(self.editor.layout_mode.findData("architecture"))
        self.editor.select_architecture(item["id"])
        self.assertFalse(self.editor.duplicate_button.isEnabled())
        self.assertFalse(self.editor.remove_button.isEnabled())
        self.assertIn("raised room", self.editor.remove_button.toolTip().lower())
        before = self.state()
        self.editor.put_away()
        self.editor.duplicate_selected()
        self.assertEqual(self.state(), before)
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(item["x"], item["y"]))
        self.move(self.point(item["x"]+1, item["y"]))
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.canvas._architecture_clearance,
                         {(x, y) for x in (connector["x"]+1, connector["x"]+2)
                          for y in (connector["y"]-1, connector["y"], connector["y"]+3, connector["y"]+4)})
        QTest.keyClick(self.editor.canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(item["x"]+1, item["y"]))
        self.assertEqual(self.state(), before)

    def test_medium_raised_preset_can_attach_above_default_room_at_canvas_top_edge(self):
        data = deepcopy(self.source)
        data["height"] = 20
        data["rooms"][0]["y"] = 5
        data["entry"] = [4, 10]
        self.editor = InteriorEditor(self.root / "character.json", data, allow_rebase=True)
        self.editors.append(self.editor)
        self.editor.show()
        self.app.processEvents()
        self.editor.tabs.setCurrentIndex(2)
        self.editor.room_type.setCurrentIndex(self.editor.room_type.findData("raised"))
        source = self.editor.room_preset
        mime = QMimeData()
        mime.setData(ROOM_MIME, b"new-room")
        class Enter(QDragEnterEvent):
            def source(self): return source
        class Drop(QDropEvent):
            def source(self): return source
        before = self.state()
        point = self.point(6, 0)
        enter = Enter(point, Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.editor.canvas, enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.canvas._room_preview[2:], (6, 6))
        self.assertEqual(self.state(), before)
        drop = Drop(QPointF(point), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.editor.canvas, drop)
        self.assertTrue(drop.isAccepted())
        upper, connector = self.pair()
        self.assertEqual((upper["width"], upper["height"]), (6, 6))
        self.assertEqual(connector["y"], upper["y"]+upper["height"])
        self.assertGreater(self.editor._room_offsets["main"][1], 0)
        self.assertEqual(len(self.editor.draft._undo), len(before[1])+1)


if __name__ == "__main__":
    unittest.main()
