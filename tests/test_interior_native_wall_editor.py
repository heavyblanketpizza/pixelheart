"""Room walls share their physical bounds with gestures and atomic edits."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interiors import new_interior
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interior_layout import partition_candidate


class NativeWallEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.editor = None

    def tearDown(self):
        if self.editor is not None:
            self.editor.reject()
            self.editor.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def design(self):
        data = new_interior()
        data["rooms"][0]["height"] = 14
        data["height"] = 20
        data["entry"] = [4, 16]
        return data

    def open_editor(self, data=None):
        self.editor = InteriorEditor(self.root / "character.json", data or self.design())
        self.editor.show()
        self.app.processEvents()
        self.editor.tabs.setCurrentIndex(2)
        self.editor.layout_mode.setCurrentIndex(1)
        return self.editor

    def test_new_vertical_room_wall_covers_both_columns_in_hit_testing(self):
        editor = self.open_editor()
        self.assertEqual(editor.wall_type.currentData(), "room")
        self.assertTrue(editor.draw_partition("vertical", 6, 5, 14), editor.status.text())
        wall = editor.draft.data["partitions"][0]
        self.assertEqual(wall["thickness"], 2)
        for point in ((6, 5), (7, 18), (7, 11)):
            self.assertEqual(editor.canvas._partition_at(*point)["id"], wall["id"])
        self.assertIsNone(editor.canvas._partition_at(8, 8))
        self.assertIsNone(editor.canvas._partition_at(5, 8))
        editor.select_partition_on_canvas("")
        cell = editor.canvas.scale * 16
        QTest.mouseClick(editor.canvas, Qt.MouseButton.LeftButton,
                         pos=QPoint(7 * cell + cell // 2, 7 * cell + cell // 2))
        self.assertEqual(editor.selected_partition, wall["id"])
        self.assertIsNone(editor.canvas._pending_room_move)

    def test_horizontal_preview_uses_all_five_rows_without_mutating_the_draft(self):
        editor = self.open_editor()
        before = editor.draft.snapshot()
        self.assertTrue(editor.canvas._preview_structure_edit("partition", "horizontal", 2, 9, 10),
                        editor.canvas.preview_message)
        self.assertEqual(editor.canvas._room_preview, (2, 9, 10, 5))
        self.assertEqual(editor.draft.snapshot(), before)
        self.assertTrue(editor.draw_partition("horizontal", 2, 9, 10), editor.status.text())
        wall = editor.draft.data["partitions"][0]
        self.assertEqual(wall["thickness"], 5)
        self.assertEqual(editor.canvas._partition_at(3, 13)["id"], wall["id"])
        self.assertIsNone(editor.canvas._partition_at(3, 8))
        self.assertIsNone(editor.canvas._partition_at(3, 14))

    def test_opening_preview_spans_full_room_wall_thickness(self):
        for axis, wall_args, point, expected in (
                ("vertical", (6, 5, 14), (7, 15), (6, 14, 2, 2)),
                ("horizontal", (2, 9, 10), (9, 12), (8, 9, 2, 5))):
            with self.subTest(axis=axis):
                if self.editor is not None:
                    self.editor.reject()
                    self.editor.deleteLater()
                    self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.app.processEvents()
                editor = self.open_editor()
                self.assertTrue(editor.draw_partition(axis, *wall_args), editor.status.text())
                editor.opening_width.setCurrentIndex(editor.opening_width.findData(2))
                editor.start_opening()
                self.assertTrue(editor.canvas._preview_structure_edit("opening", *point),
                                editor.canvas.preview_message)
                self.assertEqual(editor.canvas._room_preview, expected)

    def test_selected_legacy_divider_changes_type_in_one_undo_preserving_identity(self):
        source = partition_candidate(self.design(), "main", "vertical", 6, 5, 14)
        editor = self.open_editor(source)
        original = editor.draft.snapshot()
        wall = original["partitions"][0]
        editor.select_partition_on_canvas(wall["id"])
        self.assertEqual(editor.wall_type.currentData(), "slim")
        editor.wall_type.setCurrentIndex(editor.wall_type.findData("room"))
        changed = editor.draft.data["partitions"][0]
        self.assertEqual(changed["thickness"], 2)
        self.assertEqual(changed["id"], wall["id"])
        self.assertEqual(changed["openings"], wall["openings"])
        self.assertEqual(len(editor.draft._undo), 1)
        editor.undo()
        self.assertEqual(editor.draft.snapshot(), original)
        self.assertEqual(editor.wall_type.currentData(), "slim")
        editor.redo()
        self.assertEqual(editor.wall_type.currentData(), "room")

    def test_type_change_rejects_adjacent_furniture_and_restores_selector(self):
        source = partition_candidate(self.design(), "main", "vertical", 6, 5, 14)
        source["catalog"] = [validate_definition({"id": "(F)Chair", "kind": "chair", "footprint": [1, 1]})]
        source["furniture"] = [{"id": "chair", "item_id": "(F)Chair", "x": 7, "y": 7,
                                "rotation": 0, "mod_data": {}}]
        editor = self.open_editor(source)
        editor.select_partition_on_canvas(source["partitions"][0]["id"])
        before, undo = editor.draft.snapshot(), deepcopy(editor.draft._undo)
        editor.wall_type.setCurrentIndex(editor.wall_type.findData("room"))
        self.assertEqual(editor.draft.snapshot(), before)
        self.assertEqual(editor.draft._undo, undo)
        self.assertEqual(editor.wall_type.currentData(), "slim")
        self.assertIn("furniture", editor.status.text().lower())

    def test_new_wall_rejects_second_column_outside_its_owner(self):
        editor = self.open_editor()
        before = editor.draft.snapshot()
        self.assertFalse(editor.draw_partition("vertical", 11, 7, 4))
        self.assertEqual(editor.draft.snapshot(), before)
        self.assertIn("inside one room", editor.status.text())
        editor.wall_type.setCurrentIndex(editor.wall_type.findData("slim"))
        editor.opening_width.setCurrentIndex(editor.opening_width.findData(0))
        self.assertTrue(editor.draw_partition("vertical", 11, 7, 4), editor.status.text())


if __name__ == "__main__":
    unittest.main()
