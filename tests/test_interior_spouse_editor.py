"""Farmhouse framing leaves editable coordinates and saved map geometry intact."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interiors import interior_asset_references, interior_tmx, render_interior
from tests.test_interior_spouse_access import blocked_legacy, design


class SpouseEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "project" / "character.json"
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def editor(self, data=None):
        dialog = InteriorEditor(self.project, data or design(), "spouse")
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    @staticmethod
    def point(dialog, x, y):
        ox, oy = dialog.canvas.view_origin
        cell = dialog.canvas.scale * 16
        return QPoint((x + ox) * cell + cell // 2, (y + oy) * cell + cell // 2)

    def test_surround_is_read_only_and_clicks_map_to_the_insert_at_every_zoom(self):
        dialog = self.editor()
        dialog.set_tool("spouse_stand")
        for scale in (1, 2, 4):
            dialog.canvas.set_scale(scale)
            self.assertEqual((dialog.canvas.width(), dialog.canvas.height()), (144 * scale, 176 * scale))
            before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo)
            for x, y in ((-1, 6), (6, 6), (3, -1), (3, 9)):
                QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, x, y))
            self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo), before)
            QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 2, 6))
            self.assertEqual(dialog.draft.data["spouse_stand"], [2, 6])
            dialog.undo()
            self.assertEqual(dialog.draft.data["spouse_stand"], [3, 5])

    def test_ghost_and_save_enforce_left_access_and_legacy_rooms_can_be_repaired(self):
        dialog = self.editor(blocked_legacy())
        self.assertIn("opening on the left", dialog.access_notice.text())
        dialog.save_design()
        self.assertIsNone(dialog.result_design)
        self.assertFalse(self.project.parent.exists())
        dialog.run_change(lambda: dialog.draft.remove_furniture("block-0-8"))
        self.assertTrue(dialog.access_notice.isHidden())
        blocker = dialog.draft.data["catalog"][0]
        self.assertFalse(dialog.canvas._validate_furniture(blocker, 0, 0, 8)[0])
        self.assertTrue(dialog.canvas._validate_furniture(blocker, 0, 3, 8)[0])
        before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo)
        dialog.canvas.set_placement(blocker, 0, dialog.stage_root)
        dialog.set_tool("place")
        dialog.selected_catalog = blocker["id"]
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, 0, 8))
        self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo), before)
        dialog.save_design()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_rooms_button_keeps_click_placement_active_through_edits_and_undo(self):
        dialog = self.editor()
        dialog.tabs.setCurrentIndex(2)
        dialog.entrance_button.click()
        for target in ((2, 6), (4, 7)):
            QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=self.point(dialog, *target))
            self.assertEqual(dialog.draft.data["spouse_stand"], list(target))
            self.assertEqual(dialog.canvas.tool, "spouse_stand")
            self.assertEqual(dialog.tool.currentData(), "spouse_stand")
            self.assertEqual(dialog.canvas.selected_room_id, "")
            self.assertEqual(dialog.status.text(), "")
        self.assertEqual(len(dialog.draft._undo), 2)
        dialog.undo()
        self.assertEqual(dialog.draft.data["spouse_stand"], [2, 6])
        dialog.redo()
        self.assertEqual(dialog.draft.data["spouse_stand"], [4, 7])

    def test_heart_drag_previews_then_commits_one_undoable_edit_at_every_zoom(self):
        dialog = self.editor()
        dialog.tabs.setCurrentIndex(2)
        for scale in (1, 2, 3, 4):
            with self.subTest(scale=scale):
                dialog.zoom.setCurrentIndex(dialog.zoom.findData(scale))
                start, end = self.point(dialog, 3, 5), self.point(dialog, 2, 6)
                before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo)
                QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
                self.app.sendEvent(dialog.canvas, QMouseEvent(
                    QEvent.Type.MouseMove, QPointF(end), QPointF(end), Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo), before)
                self.assertTrue(dialog.canvas.preview_valid)
                QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
                self.assertEqual(dialog.draft.data["spouse_stand"], [2, 6])
                self.assertEqual(len(dialog.draft._undo), len(before[1]) + 1)
                dialog.undo()
                self.assertEqual(dialog.draft.snapshot(), before[0])
                dialog.redo()
                self.assertEqual(dialog.draft.data["spouse_stand"], [2, 6])
                dialog.undo()

    def test_standing_spot_uses_external_validator_for_preview_and_commit(self):
        dialog = self.editor()
        def protect(candidate, offsets):
            if candidate["spouse_stand"] == [2, 6]:
                raise ValueError("Leave the scheduled activity spot clear.")
        dialog.validate_layout = protect
        before = dialog.draft.snapshot(), deepcopy(dialog.draft._undo)
        with self.assertRaisesRegex(ValueError, "scheduled activity"):
            dialog.standing_spot_candidate(2, 6)
        self.assertFalse(dialog.move_standing_spot(2, 6))
        self.assertEqual((dialog.draft.snapshot(), dialog.draft._undo), before)
        self.assertIn("scheduled activity", dialog.status.text())

    def test_preview_omits_unused_catalog_entries_but_move_preserves_them(self):
        dialog = self.editor()
        before = dialog.draft.snapshot()
        preview = dialog.canvas.spouse_stand_candidate(2, 6)
        self.assertEqual(preview["catalog"], [])
        self.assertEqual(preview["spouse_stand"], [2, 6])
        self.assertEqual(dialog.draft.snapshot(), before)
        self.assertTrue(dialog.move_standing_spot(2, 6))
        self.assertEqual(dialog.draft.data["catalog"], before["catalog"])

    def test_auto_fit_settles_without_scrollbars_at_odd_and_even_window_sizes(self):
        from pixelheart.theme import STYLESHEET
        dialog = self.editor()
        dialog.setStyleSheet(STYLESHEET)
        with patch.object(dialog, "center_room", wraps=dialog.center_room) as center:
            for width in (1320, 1321, 1319):
                dialog.resize(width, 880)
                dialog.fit_room()
                QTest.qWait(80)
                calls = center.call_count
                viewport_size = dialog.canvas_scroll.viewport().size()
                QTest.qWait(50)
                self.assertEqual(center.call_count, calls, "Auto-fit kept centering an idle window")
                self.assertEqual(dialog.canvas_scroll.viewport().size(), viewport_size)
                self.assertEqual(dialog.canvas_scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(dialog.canvas_scroll.verticalScrollBar().maximum(), 0)
                self.assertFalse(dialog._fit_timer.isActive())

    def test_native_layers_travel_with_design_without_changing_map_or_raw_render(self):
        data = design()
        self.project.parent.mkdir()
        atlas = "world_assets/interiors/tiles.png"
        (self.project.parent / atlas).parent.mkdir(parents=True)
        with Image.new("RGBA", (16, 16), "#aa9966") as image:
            image.save(self.project.parent / atlas)
        data["atlas"] = {"asset": atlas, "columns": 1, "tile_count": 1}
        context = {}
        for key, color in (("background_asset", "#903020"), ("foreground_asset", "#206090")):
            reference = f"world_assets/interiors/{key}.png"
            target = self.project.parent / reference
            target.parent.mkdir(parents=True, exist_ok=True)
            with Image.new("RGBA", (144, 176)) as image:
                if key == "background_asset":
                    image.paste(color, (0, 0, 32, 160))
                else:
                    image.paste(color, (32, 152, 128, 160))
                image.save(target)
            context[key] = reference
        data["spouse_context"] = context
        dialog = self.editor(data)
        self.assertTrue(all((dialog.stage_root / path).is_file() for path in context.values()))
        self.assertIn("surroundings", dialog.farmhouse_caption.text())
        captured = dialog.canvas.grab().toImage()
        scale = dialog.canvas.scale
        self.assertEqual(captured.pixelColor(8 * scale, 80 * scale).name(), "#903020")
        self.assertEqual(captured.pixelColor(64 * scale, 156 * scale).name(), "#206090")
        plain = deepcopy(data)
        plain.pop("spouse_context")
        with render_interior(data, self.project.parent) as framed, render_interior(plain, self.project.parent) as raw:
            self.assertEqual(framed.size, (96, 144))
            self.assertEqual(framed.tobytes(), raw.tobytes())
        self.assertEqual(interior_tmx(data), interior_tmx(plain))
        self.assertEqual(set(interior_asset_references(data)), {atlas, *context.values()})
        dialog.save_design()
        self.assertEqual(dialog.result_design["spouse_context"], context)
        reopened = self.editor(dialog.result_design)
        self.assertFalse(reopened.canvas._context_background.isNull())


if __name__ == "__main__":
    unittest.main()
