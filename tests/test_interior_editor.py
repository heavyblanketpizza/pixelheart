"""Interior authoring keeps edits and imported artwork transactional."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QTableWidgetItem

from pixelheart.interior_editor import InteriorEditor, FurnitureDetails
from pixelheart_core.interiors import new_interior
from pixelheart_core.interior_furniture import validate_definition, attach_texture
from tests.qt_support import QtTestCase


class InteriorEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.project = self.root / "project" / "character.json"
        self.sheet = self.root / "sheet.png"
        image = Image.new("RGBA", (64, 16), "#556677")
        image.paste("#aabbcc", (16, 0, 32, 16))
        image.save(self.sheet)
        image.close()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            # Hidden dialogs do not finish on close(); reject releases staging.
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self, design=None, kind="residence"):
        result = InteriorEditor(self.project, design, kind)
        self.dialogs.append(result)
        return result

    def furniture_design(self):
        design = new_interior()
        design["catalog"] = [validate_definition({"id": "(F)Author.Chair", "name": "Blue chair", "kind": "chair",
                                                   "footprint": [1, 2], "rotations": 4})]
        return design

    def test_cancel_discards_edits_and_does_not_commit_imported_assets(self):
        original = new_interior()
        before = deepcopy(original)
        dialog = self.dialog(original)
        dialog.load_atlas(self.sheet)
        self.assertTrue(dialog.draft.data["atlas"]["asset"])
        self.assertTrue(any(dialog.stage_root.rglob("*.png")))
        dialog.apply_tile(1)
        dialog.reject()
        self.assertEqual(original, before)
        self.assertIsNone(dialog.result_design)
        self.assertFalse(self.project.parent.exists())
        self.assertFalse(dialog.stage_root.exists())

    def test_accept_copies_only_referenced_assets_and_returns_detached_design(self):
        original = new_interior()
        dialog = self.dialog(original)
        dialog.load_atlas(self.sheet)
        dialog.apply_tile(1)
        unrelated = dialog.stage_root / "unused.png"
        unrelated.write_bytes(b"unused staged file")
        dialog.save_design()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.result_design["style"]["floor"], 1)
        reference = dialog.result_design["atlas"]["asset"]
        self.assertEqual((self.project.parent / reference).read_bytes(), self.sheet.read_bytes())
        self.assertFalse((self.project.parent / "unused.png").exists())
        self.assertEqual(original["atlas"]["asset"], "")

    def test_spouse_mode_has_fixed_room_and_standing_tool(self):
        dialog = self.dialog(kind="spouse")
        self.assertEqual((dialog.draft.data["width"], dialog.draft.data["height"]), (6, 9))
        self.assertTrue(dialog.room_controls.isHidden())
        self.assertTrue(dialog.canvas_size_controls.isHidden())
        self.assertEqual(dialog.tool.findData("entry"), -1)
        self.assertGreaterEqual(dialog.tool.findData("spouse_stand"), 0)
        dialog.tool.setCurrentIndex(dialog.tool.findData("spouse_stand"))
        dialog.click_tile(2, 6)
        self.assertEqual(dialog.draft.data["spouse_stand"], [2, 6])
        dialog.undo()
        self.assertEqual(dialog.draft.data["spouse_stand"], [3, 5])

    def test_missing_preview_prevents_partial_asset_commit(self):
        design = self.furniture_design()
        design["catalog"][0]["preview_asset"] = "world_assets/interiors/textures/missing.png"
        dialog = self.dialog(design)
        dialog.load_atlas(self.sheet)
        dialog.save_design()
        self.assertIsNone(dialog.result_design)
        self.assertFalse(self.project.parent.exists())
        self.assertIn("missing", dialog.status.text())

    def test_furniture_click_place_drag_rotation_remove_and_history(self):
        dialog = self.dialog(self.furniture_design())
        dialog.catalog_list.setCurrentRow(0)
        dialog.tool.setCurrentIndex(dialog.tool.findData("place"))
        dialog.click_tile(6, 7)
        self.assertEqual(len(dialog.draft.data["furniture"]), 1)
        identity = dialog.selected_furniture
        dialog.rotate_selected()
        self.assertEqual(dialog.draft.data["furniture"][0]["rotation"], 1)
        dialog.tool.setCurrentIndex(dialog.tool.findData("select"))
        cell = 16 * dialog.canvas.scale
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(6 * cell + 4, 7 * cell + 4))
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(8 * cell + 4, 9 * cell + 4))
        placed = dialog.draft.data["furniture"][0]
        self.assertEqual((placed["x"], placed["y"]), (8, 9))
        self.assertEqual(placed["id"], identity)
        dialog.remove_selected()
        self.assertEqual(dialog.draft.data["furniture"], [])
        dialog.undo()
        self.assertEqual(dialog.draft.data["furniture"][0]["id"], identity)
        dialog.redo()
        self.assertEqual(dialog.draft.data["furniture"], [])

    def test_invalid_move_keeps_placement_and_shows_error(self):
        dialog = self.dialog(self.furniture_design())
        dialog.catalog_list.setCurrentRow(0)
        dialog.tool.setCurrentIndex(dialog.tool.findData("place"))
        dialog.click_tile(6, 7)
        before = dialog.draft.snapshot()
        dialog.move_furniture(dialog.selected_furniture, 23, 19)
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertIn("floor", dialog.status.text())

    def test_room_add_remove_uses_core_validation(self):
        dialog = self.dialog()
        dialog.add_room()
        self.assertEqual(len(dialog.draft.data["rooms"]), 2)
        dialog.room_list.setCurrentRow(0)
        before = dialog.draft.snapshot()
        dialog.remove_room()
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertIn("entry", dialog.status.text().casefold())
        dialog.room_list.setCurrentRow(1)
        dialog.remove_room()
        self.assertEqual(len(dialog.draft.data["rooms"]), 1)

    def test_enlarging_canvas_keeps_furniture_and_rooms_and_can_be_undone(self):
        dialog = self.dialog(self.furniture_design())
        dialog.catalog_list.setCurrentRow(0)
        dialog.tool.setCurrentIndex(dialog.tool.findData("place"))
        dialog.click_tile(6, 7)
        before = dialog.draft.snapshot()
        dialog.canvas_width.setValue(48)
        dialog.canvas_height.setValue(32)
        dialog.resize_canvas()
        self.assertEqual((dialog.draft.data["width"], dialog.draft.data["height"]), (48, 32))
        self.assertEqual(dialog.draft.data["rooms"], before["rooms"])
        self.assertEqual(dialog.draft.data["furniture"], before["furniture"])
        self.assertEqual(dialog.canvas.width(), 48 * 16 * dialog.canvas.scale)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertEqual((dialog.canvas_width.value(), dialog.canvas_height.value()), (24, 20))

    def test_shrinking_canvas_cannot_crop_existing_rooms(self):
        dialog = self.dialog()
        before = dialog.draft.snapshot()
        dialog.canvas_width.setValue(10)
        dialog.resize_canvas()
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertIn("inside the canvas", dialog.status.text())
        self.assertEqual(dialog.canvas.width(), 24 * 16 * dialog.canvas.scale)

    def test_tile_animation_plays_pauses_and_undo_restores_definition(self):
        dialog = self.dialog()
        dialog.load_atlas(self.sheet)
        dialog.animation_tile.setValue(0)
        dialog.animation_frames.setText("0, 1")
        dialog.animation_duration.setValue(100)
        dialog.apply_animation()
        self.assertEqual(len(dialog.draft.data["animations"]), 1)
        with patch("pixelheart.interior_editor.time.monotonic", side_effect=[10.0, 10.125]):
            dialog.play.setChecked(True)
            dialog.advance_animation()
        self.assertEqual(dialog.elapsed_ms, 125)
        self.assertTrue(dialog.timer.isActive())
        dialog.play.setChecked(False)
        self.assertFalse(dialog.timer.isActive())
        self.assertEqual(dialog.elapsed_ms, 125)
        dialog.undo()
        self.assertEqual(dialog.draft.data["animations"], [])

    def test_native_catalog_import_and_search_do_not_create_placements(self):
        source = self.root / "Data_Furniture.json"
        source.write_text(json.dumps({"0": "Oak chair/chair/1 2/1 1/4/350/2/Oak chair"}))
        dialog = self.dialog()
        dialog.load_catalog(source)
        self.assertEqual(len(dialog.draft.data["catalog"]), 1)
        self.assertEqual(dialog.draft.data["furniture"], [])
        self.assertEqual(dialog.catalog_list.count(), 1)
        dialog.search.setText("does not exist")
        self.assertEqual(dialog.catalog_list.count(), 0)
        dialog.search.setText("oak")
        self.assertEqual(dialog.catalog_list.count(), 1)

    def test_existing_preview_assets_stage_for_reopening_and_preserve_source(self):
        design = self.furniture_design()
        design["catalog"][0] = attach_texture(design["catalog"][0], self.sheet, self.project.parent)
        reference = design["catalog"][0]["preview_asset"]
        before = (self.project.parent / reference).read_bytes()
        dialog = self.dialog(design)
        self.assertEqual((dialog.stage_root / reference).read_bytes(), before)
        dialog.reject()
        self.assertEqual((self.project.parent / reference).read_bytes(), before)

    def test_manual_details_records_explicit_frame_rectangles(self):
        dialog = FurnitureDetails()
        self.dialogs.append(dialog)
        dialog.item_id.setText("Author.Lamp")
        dialog.name.setText("Animated lamp")
        dialog.kind.setText("lamp")
        dialog.rotations.setCurrentIndex(dialog.rotations.findData(2))
        dialog.rotation_bounds.setItem(1, 1, QTableWidgetItem("2"))
        dialog.rotation_bounds.setItem(1, 2, QTableWidgetItem("1"))
        dialog.add_frame([0, 0, 0, 16, 32, 180])
        dialog.add_frame([0, 16, 0, 16, 32, 220])
        dialog.save_details()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.result_definition["id"], "(F)Author.Lamp")
        self.assertEqual(dialog.result_definition["rotation_footprints"], {"1": [2, 1]})
        self.assertEqual([frame["duration_ms"] for frame in dialog.result_definition["frames"]], [180, 220])


if __name__ == "__main__":
    unittest.main()
