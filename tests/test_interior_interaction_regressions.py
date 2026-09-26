"""Mode boundaries, visible room targets and world-aware decorating commits."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from pixelheart.interior_editor import InteriorEditor
from pixelheart.world_page import WorldPage
from pixelheart_core.interiors import new_interior
from pixelheart_core.interior_furniture import validate_definition
from tests.test_interior_decorating_flow import make_library
from tests.qt_support import QtTestCase


class InteriorInteractionRegressionTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.dialogs = []

    def tearDown(self):
        for editor in self.dialogs:
            editor.reject()
            editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def editor(self, design=None, **kwargs):
        editor = InteriorEditor(self.root / "project" / "character.json", design, **kwargs)
        self.dialogs.append(editor)
        editor.show()
        self.app.processEvents()
        return editor

    @staticmethod
    def chair_design():
        design = new_interior()
        design["catalog"] = [validate_definition({"id": "(F)Test.Chair", "name": "Chair", "kind": "chair",
                                                  "footprint": [1, 2], "rotations": 2,
                                                  "rotation_footprints": {"1": [2, 1]}})]
        return design

    @staticmethod
    def protect(x, y):
        return lambda candidate, offsets: WorldPage._validate_room_points(
            candidate, [{"x": x, "y": y, "label": "the daily schedule at 900"}], [])

    @staticmethod
    def state(editor):
        return deepcopy((editor.draft.data, editor.draft._undo, editor.draft._redo,
                         editor._room_offsets, editor._offsets_undo, editor._offsets_redo))

    @staticmethod
    def point(editor, x, y):
        cell = editor.canvas.scale * 16
        ox, oy = editor.canvas.view_origin
        return QPoint((x + ox) * cell + 5, (y + oy) * cell + 5)

    def move_pointer(self, editor, point, held=False):
        self.app.sendEvent(editor.canvas, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(point), QPointF(point), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def test_walls_mode_floor_click_and_drag_never_switch_or_move_rooms(self):
        editor = self.editor()
        editor.tabs.setCurrentIndex(2)
        editor.layout_mode.setCurrentIndex(editor.layout_mode.findData("walls"))
        before = self.state(editor)
        start, end = self.point(editor, 8, 8), self.point(editor, 9, 8)
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mousePress(editor.canvas, Qt.MouseButton.LeftButton, pos=start)
        self.move_pointer(editor, end, held=True)
        QTest.mouseRelease(editor.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(editor.layout_mode.currentData(), "walls")
        self.assertEqual(editor.canvas.tool, "wall-select")
        self.assertTrue(editor.wall_controls.isVisible())
        self.assertEqual(self.state(editor), before)

    def test_wall_selection_and_delete_work_from_canvas_and_list(self):
        editor = self.editor()
        editor.tabs.setCurrentIndex(2)
        editor.layout_mode.setCurrentIndex(editor.layout_mode.findData("walls"))
        editor.wall_type.setCurrentIndex(editor.wall_type.findData("slim"))
        self.assertTrue(editor.draw_partition("horizontal", 2, 8, 10))
        for focus in (editor.canvas, editor.partition_list):
            with self.subTest(focus=focus):
                editor.partition_list.setCurrentRow(0)
                QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(editor, 2, 8))
                self.assertTrue(editor.selected_partition)
                focus.setFocus()
                QTest.keyClick(focus, Qt.Key.Key_Delete)
                self.assertEqual(editor.draft.data.get("partitions", []), [])
                editor.undo()
                self.assertEqual(len(editor.draft.data["partitions"]), 1)

    def test_finish_targets_visible_upper_floor_before_lower_wall_strip(self):
        design = new_interior()
        design["rooms"][0]["y"] = 11
        design["entry"] = [4, 16]
        design["rooms"].append({"id": "upper", "name": "Upper room", "x": 2, "y": 5,
                                "width": 10, "height": 6, "optional": False, "enabled": True})
        editor = self.editor(design)
        self.assertTrue(editor.load_catalog(make_library(self.root / "library")))
        editor.tabs.setCurrentIndex(1)
        editor.surface_kind.setCurrentIndex(editor.surface_kind.findData("floor"))
        editor.surface_list.setCurrentRow(0)
        before = editor.draft.snapshot()
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(editor, 6, 9))
        self.assertEqual(set(editor.draft.data["room_styles"]), {"upper"})
        editor.undo()
        self.assertEqual(editor.draft.snapshot(), before)
        self.assertEqual(editor.canvas.room_at(6, 3, include_walls=True)["id"], "upper")

    def test_protected_destination_rejects_preview_and_click_without_history(self):
        editor = self.editor(self.chair_design(), validate_layout=self.protect(8, 8))
        editor.begin_catalog_placement(editor.catalog_list.item(0))
        before = self.state(editor)
        point = self.point(editor, 8, 8)
        self.move_pointer(editor, point)
        self.assertFalse(editor.canvas.preview_valid)
        self.assertIn("daily schedule", editor.canvas.preview_message)
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.state(editor), before)
        self.assertIn("daily schedule", editor.status.text())
        self.assertTrue(editor.place_furniture_once("(F)Test.Chair", 6, 7, 0))
        self.assertEqual(len(editor.draft._undo), 1)
        editor.save_design()
        self.assertIsNotNone(editor.result_design)

    def test_protected_destination_rejects_move_rotation_and_duplicate_atomically(self):
        editor = self.editor(self.chair_design(), validate_layout=self.protect(7, 7))
        self.assertTrue(editor.place_furniture_once("(F)Test.Chair", 6, 7, 0))
        identity = editor.selected_furniture
        before = self.state(editor)
        editor.move_furniture(identity, 7, 7)
        self.assertEqual(self.state(editor), before)
        editor.rotate_selected()
        self.assertEqual(self.state(editor), before)
        self.assertIn("daily schedule", editor.status.text())
        editor.duplicate_selected()
        self.move_pointer(editor, self.point(editor, 7, 7))
        self.assertFalse(editor.canvas.preview_valid)
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(editor, 7, 7))
        self.assertEqual(self.state(editor), before)

    def test_refresh_uses_remembered_source_without_global_discovery(self):
        editor = self.editor(self.chair_design())
        source = self.root / "chosen" / "library.json"
        source.parent.mkdir()
        source.write_text("{}")
        self.settings.setValue("interiors/librarySource", str(source))
        with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[source]) as discover, \
                patch.object(editor, "load_catalog", return_value=True) as load:
            editor.connect_library()
        discover.assert_not_called()
        load.assert_called_once_with(source)

    def test_missing_remembered_source_requires_explicit_reconnection(self):
        editor = self.editor(self.chair_design())
        self.settings.setValue("interiors/librarySource", str(self.root / "missing.json"))
        with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[]) as discover, \
                patch.object(editor, "change_library") as change, patch.object(editor, "load_catalog") as load:
            editor.connect_library()
        discover.assert_not_called()
        load.assert_not_called()
        self.assertIn("unavailable", change.call_args.kwargs["message"])

    def test_change_library_opens_setup_without_reloading_existing_source(self):
        editor = self.editor(self.chair_design())
        with patch("pixelheart.interior_editor.QDialog.exec", return_value=0) as execute, \
                patch.object(editor, "load_catalog") as load:
            editor.change_library_button.click()
        execute.assert_called_once()
        load.assert_not_called()

    def test_import_remembers_exact_source_and_explains_project_draft(self):
        editor = self.editor()
        source = make_library(self.root / "library")
        self.assertTrue(editor.load_catalog(source))
        self.assertEqual(editor.remembered_library(), str(source.resolve()))
        self.assertEqual(editor.save_button.text(), "Apply home")
        self.assertTrue(any("Save project keeps your changes on disk" in item.text()
                            for item in editor.findChildren(QLabel)))

    def test_contextual_actions_hide_furniture_and_keep_doorway_reachable(self):
        editor = self.editor()
        editor.tabs.setCurrentIndex(1)
        self.assertFalse(editor.selection_bar.isVisible())
        editor.tabs.setCurrentIndex(2)
        editor.layout_mode.setCurrentIndex(editor.layout_mode.findData("architecture"))
        self.assertTrue(editor.entrance_button.isVisible())
        editor.entrance_button.click()
        self.assertEqual(editor.canvas.tool, "entry")
        self.assertFalse(editor.selection_bar.isVisible())
        spouse = self.editor(kind="spouse")
        spouse.tabs.setCurrentIndex(2)
        self.assertFalse(spouse.selection_bar.isVisible())
        spouse.entrance_button.click()
        self.assertEqual(spouse.canvas.tool, "spouse_stand")
        self.assertFalse(spouse.selection_bar.isVisible())

    def test_minimum_window_fits_after_hidden_import_without_horizontal_room_scroll(self):
        from pixelheart.theme import STYLESHEET
        editor = InteriorEditor(self.root / "project" / "character.json")
        self.dialogs.append(editor)
        editor.setStyleSheet(STYLESHEET)
        self.assertTrue(editor.load_catalog(make_library(self.root / "library")))
        editor.resize(1000, 700)
        editor.tabs.setCurrentIndex(2)
        editor.show()
        QTest.qWait(30)
        rooms = editor.tabs.widget(2)
        self.assertEqual(rooms.horizontalScrollBar().maximum(), 0)
        self.assertGreaterEqual(editor.canvas.scale, 2)
        self.assertEqual(editor.zoom.currentData(), editor.canvas.scale)
        self.assertFalse(editor._fit_timer.isActive())
        doorway_position = editor.entrance_button.mapTo(rooms.viewport(), QPoint(0, 0))
        self.assertTrue(rooms.viewport().rect().contains(doorway_position))
        editor.tabs.setCurrentIndex(1)
        QTest.qWait(20)
        self.assertGreaterEqual(editor.canvas.scale, 2)

        editor.zoom.setCurrentIndex(editor.zoom.findData(1))
        editor.resize(1200, 800)
        editor.tabs.setCurrentIndex(2)
        QTest.qWait(20)
        self.assertFalse(editor._auto_fit)
        self.assertEqual(editor.canvas.scale, 1)


if __name__ == "__main__":
    unittest.main()
