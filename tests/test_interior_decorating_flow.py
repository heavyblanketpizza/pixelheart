"""Beginner journeys through the decorating UI, with synthetic local artwork."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pixelheart.interior_editor import InteriorEditor
from tests.qt_support import QtTestCase


def make_library(root):
    """Create test fixtures, not substitute game art or shipped starter assets."""
    root.mkdir(parents=True, exist_ok=True)
    with Image.new("RGBA", (48, 32)) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 3, 12, 20), fill="#426675", outline="#233b43")
        draw.rectangle((1, 17, 14, 24), fill="#638d94", outline="#233b43")
        draw.rectangle((2, 24, 4, 30), fill="#5c4430")
        draw.rectangle((11, 24, 13, 30), fill="#5c4430")
        draw.rectangle((19, 4, 43, 10), fill="#638d94", outline="#233b43")
        draw.rectangle((20, 11, 22, 15), fill="#5c4430")
        draw.rectangle((40, 11, 42, 15), fill="#5c4430")
        image.save(root / "chair.png")
    with Image.new("RGBA", (16, 48), "#758987") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 15, 3), fill="#414f54")
        draw.line((0, 31, 15, 31), fill="#b4b6a4", width=2)
        draw.rectangle((0, 40, 15, 47), fill="#545d5c")
        image.save(root / "wall.png")
    with Image.new("RGBA", (32, 32), "#aa947a") as image:
        draw = ImageDraw.Draw(image)
        for y in (0, 8, 16, 24):
            draw.line((0, y, 31, y), fill="#796d5d")
        draw.line((8, 0, 8, 8), fill="#796d5d")
        draw.line((23, 8, 23, 16), fill="#796d5d")
        image.save(root / "floor.png")
    bundle = {"format": "pixelheart-interior-library", "version": 1,
              "definitions": [{"id": "(F)Test.Chair", "name": "Blue chair", "kind": "chair",
                               "footprint": [1, 2], "rotations": 2,
                               "rotation_footprints": {"1": [2, 1]},
                               "preview_asset": "chair.png",
                               "frames": [{"rotation": 0, "rect": [0, 0, 16, 32], "duration_ms": 100},
                                          {"rotation": 1, "rect": [16, 0, 32, 16], "duration_ms": 100}]}],
              "surfaces": [{"id": "(WP)Test.Blue", "name": "Blue wallpaper", "kind": "wall",
                            "texture": "Maps/walls_and_floors",
                            "preview_asset": "wall.png", "rect": [0, 0, 16, 48]},
                           {"id": "(FL)Test.Wood", "name": "Wood floor", "kind": "floor",
                            "texture": "Maps/walls_and_floors",
                            "preview_asset": "floor.png", "rect": [0, 0, 32, 32]}]}
    path = root / "library.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


class InteriorDecoratingFlowTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.library = make_library(self.root / "library")
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def editor(self, kind="residence", *, connected=True):
        dialog = InteriorEditor(self.root / "project" / "character.json", kind=kind, resident_name="Test resident", allow_rebase=True)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        if connected:
            with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[self.library]), \
                    patch("pixelheart.interior_editor.QDialog.exec", return_value=0):
                QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(dialog.catalog_list.count(), 1)
        return dialog

    @staticmethod
    def point(dialog, x, y):
        cell = dialog.canvas.scale * 16
        ox, oy = dialog.canvas.view_origin
        return QPoint((x + ox) * cell + 5, (y + oy) * cell + 5)

    def move(self, dialog, x, y):
        point = QPointF(self.point(dialog, x, y))
        QApplication.sendEvent(dialog.canvas, QMouseEvent(QEvent.Type.MouseMove, point, point,
                              Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def click_tile(self, dialog, x, y, button=Qt.MouseButton.LeftButton):
        QTest.mouseClick(dialog.canvas, button, pos=self.point(dialog, x, y))
        self.app.processEvents()

    def choose_chair(self, dialog):
        item = dialog.catalog_list.item(0)
        QTest.mouseClick(dialog.catalog_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.catalog_list.visualItemRect(item).center())
        self.app.processEvents()

    def click_button(self, dialog, caption):
        buttons = [button for button in dialog.findChildren(QPushButton) if button.text() == caption]
        self.assertEqual(len(buttons), 1, caption)
        QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_first_open_starts_with_room_and_plain_actions_without_asset_fields(self):
        dialog = self.editor(connected=False)
        self.assertFalse(dialog.canvas.image.isNull())
        self.assertEqual(len(dialog.draft.data["rooms"]), 1)
        self.assertEqual([dialog.tabs.tabText(index).replace("&&", "&") for index in range(dialog.tabs.count())],
                         ["Furnish", "Walls & floors", "Rooms"])
        self.assertFalse(dialog.advanced.isVisible())
        self.assertFalse(dialog.tool.isVisible())
        self.assertFalse(dialog.grid.isChecked())
        self.assertTrue(dialog.library_welcome.isVisible())
        self.assertTrue(dialog.move_tool.isVisible())
        self.assertTrue(dialog.save_button.isVisible())
        self.assertEqual(dialog.result_design, None)

    def test_connect_prepared_library_populates_picture_and_finish_galleries(self):
        dialog = self.editor()
        self.assertFalse(dialog.library_welcome.isVisible())
        self.assertFalse(dialog.catalog_list.item(0).icon().isNull())
        self.assertEqual(dialog.surface_list.count(), 1)
        self.assertFalse(dialog.surface_list.item(0).icon().isNull())
        self.assertTrue(dialog.draft.data["atlas"]["asset"])
        self.assertIn("wall_pattern", dialog.draft.data["style"])
        self.assertIn("floor_pattern", dialog.draft.data["style"])
        self.assertFalse((self.root / "project").exists())

    def test_connected_library_is_available_for_next_home_without_repeating_setup(self):
        first = self.editor()
        first.reject()
        next_home = self.editor(connected=False)
        self.assertFalse(next_home.library_welcome.isVisible())
        self.assertEqual(next_home.catalog_list.count(), 1)
        self.assertFalse(next_home.catalog_list.item(0).icon().isNull())
        self.assertEqual(next_home.draft.data["furniture"], [])

    def test_filtering_out_held_furniture_cancels_stale_cursor_item(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.move(dialog, 6, 7)
        self.assertIsNotNone(dialog.canvas.ghost)
        dialog.search.setText("no matching item")
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas.ghost)
        self.click_tile(dialog, 6, 7)
        self.assertEqual(dialog.draft.data["furniture"], [])

    def test_refreshed_library_updates_held_items_placement_preview(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.move(dialog, 6, 7)
        candidate = dialog.draft.snapshot()
        candidate["catalog"][0]["footprint"] = [2, 2]
        dialog.draft.apply(candidate)
        dialog.refresh()
        self.assertEqual(dialog.canvas.ghost["width"], 2)
        self.click_tile(dialog, 6, 7)
        self.assertEqual(dialog.canvas.footprint(dialog.draft.data["furniture"][0]), (2, 2))

    def test_finish_category_change_does_not_apply_previously_held_wallpaper(self):
        dialog = self.editor()
        dialog.tabs.setCurrentIndex(1)
        dialog.surface_list.setCurrentRow(0)
        self.assertEqual(dialog.canvas.tool, "surface")
        dialog.surface_kind.setCurrentIndex(dialog.surface_kind.findData("floor"))
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertEqual(dialog.selected_surface, "")

    def test_same_finish_can_be_picked_up_again_after_cancelling(self):
        dialog = self.editor()
        dialog.tabs.setCurrentIndex(1)
        item = dialog.surface_list.item(0)
        position = dialog.surface_list.visualItemRect(item).center()
        QTest.mouseClick(dialog.surface_list.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.click_tile(dialog, 6, 7)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Escape)
        self.assertEqual(dialog.canvas.tool, "select")
        QTest.mouseClick(dialog.surface_list.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(dialog.canvas.tool, "surface")

    def test_escape_from_search_stops_placing_without_discarding_design(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.click_tile(dialog, 6, 7)
        before = dialog.draft.snapshot()
        dialog.search.setFocus()
        QTest.keyClicks(dialog.search, "Blue")
        QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertIsNone(dialog.result_design)

    def test_choose_rotate_and_place_releases_piece_until_catalogue_is_clicked_again(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.move(dialog, 6, 7)
        self.assertEqual(dialog.canvas.tool, "place")
        self.assertTrue(dialog.canvas.ghost["valid"])
        self.click_tile(dialog, 6, 7, Qt.MouseButton.RightButton)
        self.assertEqual(dialog.canvas.ghost["rotation"], 1)
        self.click_tile(dialog, 6, 7)
        first = deepcopy(dialog.draft.data["furniture"][0])
        self.assertEqual(first["rotation"], 1)
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertEqual(dialog.tool.currentData(), "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertIsNone(dialog.canvas.drag)
        self.assertEqual(dialog.selected_furniture, first["id"])
        before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo), deepcopy(dialog.draft._redo)
        self.move(dialog, 9, 8)
        self.click_tile(dialog, 9, 8)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo, dialog.draft._redo), before)

        # A fresh catalogue click explicitly picks up the next piece.
        self.choose_chair(dialog)
        self.move(dialog, 9, 8)
        self.assertEqual(dialog.draft.data["furniture"][0], first)
        self.assertEqual(dialog.canvas.ghost["rotation"], 0)
        self.click_tile(dialog, 9, 8)
        self.assertEqual([item["rotation"] for item in dialog.draft.data["furniture"]], [1, 0])
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.choose_chair(dialog)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Escape)
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertTrue(dialog.isVisible())

    def test_place_then_drag_put_away_and_undo_restores_the_piece(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.click_tile(dialog, 6, 7)
        self.assertEqual(dialog.canvas.tool, "select")
        original = deepcopy(dialog.draft.data["furniture"][0])
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 6, 8))
        self.move(dialog, 8, 10)
        self.assertEqual(dialog.draft.data["furniture"][0], original)
        self.assertTrue(dialog.canvas.ghost["valid"])
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 8, 10))
        moved = deepcopy(dialog.draft.data["furniture"][0])
        self.assertEqual((moved["x"], moved["y"]), (8, 9))
        QTest.mouseClick(dialog.remove_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.draft.data["furniture"], [])
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.draft.data["furniture"], [moved])

    def test_duplicate_places_one_copy_and_releases_cursor(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.click_tile(dialog, 6, 7, Qt.MouseButton.RightButton)
        self.click_tile(dialog, 6, 7)
        original = deepcopy(dialog.draft.data["furniture"][0])
        before = dialog.draft.snapshot()
        undo_count = len(dialog.draft._undo)
        QTest.mouseClick(dialog.duplicate_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.canvas.tool, "place")
        self.move(dialog, 9, 8)
        self.assertEqual(dialog.canvas.ghost["rotation"], original["rotation"])
        self.click_tile(dialog, 9, 8)
        self.assertEqual(len(dialog.draft.data["furniture"]), 2)
        self.assertEqual(dialog.draft.data["furniture"][0], original)
        duplicate = dialog.draft.data["furniture"][1]
        self.assertNotEqual(duplicate["id"], original["id"])
        self.assertEqual(duplicate["rotation"], original["rotation"])
        self.assertEqual(dialog.selected_furniture, duplicate["id"])
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertIsNone(dialog.canvas.drag)
        after = dialog.draft.snapshot()
        self.move(dialog, 6, 9)
        self.click_tile(dialog, 6, 9)
        self.assertEqual(dialog.draft.snapshot(), after)
        self.assertEqual(len(dialog.draft._undo), undo_count + 1)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), before)

    def test_attach_room_grows_space_and_preserves_existing_layout_with_one_undo(self):
        dialog = self.editor()
        self.choose_chair(dialog)
        self.click_tile(dialog, 6, 7)
        dialog.tabs.setCurrentIndex(2)
        before = dialog.draft.snapshot()
        self.assertTrue(dialog.place_room_drop(-4, 5, 6, 6))
        rooms = dialog.draft.data["rooms"]
        self.assertEqual(len(rooms), 2)
        self.assertGreater(dialog.draft.data["width"], before["width"])
        self.assertEqual(rooms[1]["x"] + rooms[1]["width"], rooms[0]["x"])
        shift = rooms[0]["x"] - before["rooms"][0]["x"]
        self.assertEqual(dialog.draft.data["furniture"][0]["x"], before["furniture"][0]["x"] + shift)
        self.assertEqual(dialog.draft.data["entry"][0], before["entry"][0] + shift)
        self.assertTrue(dialog.canvas.preview_valid)
        self.assertFalse(dialog.status.isVisible())
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.draft.snapshot(), before)

    def test_existing_home_cannot_shift_coordinates_when_adding_space(self):
        dialog = self.editor()
        dialog.allow_rebase = False
        dialog.tabs.setCurrentIndex(2)
        before = dialog.draft.snapshot()
        self.assertFalse(dialog.place_room_drop(-4, 5, 6, 6))
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertTrue(dialog.status.text())
        self.assertTrue(dialog.place_room_drop(12, 5, 6, 6))
        self.assertEqual(len(dialog.draft.data["rooms"]), 2)
        self.assertEqual(dialog.draft.data["rooms"][0], before["rooms"][0])
        self.assertEqual(dialog.draft.data["entry"], before["entry"])

    def test_draw_connected_room_and_apply_finish_by_clicking_room(self):
        dialog = self.editor()
        dialog.tabs.setCurrentIndex(2)
        self.click_button(dialog, "Draw a room…")
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 12, 5))
        self.move(dialog, 15, 8)
        self.assertTrue(dialog.canvas.preview_valid)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 15, 8))
        self.assertEqual(len(dialog.draft.data["rooms"]), 2)
        room_id = dialog.draft.data["rooms"][1]["id"]
        dialog.tabs.setCurrentIndex(1)
        item = dialog.surface_list.item(0)
        QTest.mouseClick(dialog.surface_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.surface_list.visualItemRect(item).center())
        self.click_tile(dialog, 13, 6)
        self.assertEqual(dialog.draft.data["room_styles"][room_id]["wall_pattern"]["surface_id"], "(WP)Test.Blue")
        self.assertNotIn("main", dialog.draft.data["room_styles"])
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertNotIn(room_id, dialog.draft.data.get("room_styles", {}))

    def test_spouse_room_uses_same_catalogue_and_protects_its_standing_spot(self):
        dialog = self.editor("spouse")
        self.assertEqual([dialog.tabs.tabText(index).replace("&&", "&") for index in range(dialog.tabs.count())],
                         ["Furnish", "Walls & floors", "Rooms"])
        self.assertFalse(dialog.room_controls.isVisible())
        self.assertEqual((dialog.draft.data["width"], dialog.draft.data["height"]), (6, 9))
        self.choose_chair(dialog)
        self.move(dialog, 3, 5)
        self.assertFalse(dialog.canvas.ghost["valid"])
        before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo), deepcopy(dialog.draft._redo)
        self.click_tile(dialog, 3, 5)
        self.assertEqual(dialog.draft.data["furniture"], [])
        self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo, dialog.draft._redo), before)
        self.assertEqual(dialog.canvas.tool, "place")
        self.assertIsNotNone(dialog.canvas._placement)
        self.click_tile(dialog, 1, 6)
        self.assertEqual(len(dialog.draft.data["furniture"]), 1)
        self.assertEqual(dialog.draft.data["spouse_stand"], [3, 5])
        self.assertEqual(dialog.canvas.tool, "select")
        self.assertIsNone(dialog.canvas._placement)
        self.assertIsNone(dialog.canvas.ghost)
        self.assertFalse(dialog.status.isVisible())


if __name__ == "__main__":
    unittest.main()
