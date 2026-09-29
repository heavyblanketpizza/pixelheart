"""The painter window: canvas input, panels, menus and the save flow."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSpinBox
from shiboken6 import isValid

from pixelheart.pixel_painter import PixelPainterDialog, choose_tilesheet_size, image_from_qimage, qimage_from_image
from pixelheart.theme import apply_theme
from pixelheart_core.pixel_document import PixelDocument
from pixelheart_core.pixel_layers import blank_painting
from pixelheart_core.pixel_sheets import SheetError
from tests.qt_support import QtTestCase

RED = (220, 40, 40, 255)
BLUE = (40, 60, 220, 255)
CLEAR = (0, 0, 0, 0)


class PainterTestCase(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.document.mark_saved()
                dialog.done(QDialog.DialogCode.Rejected)
                dialog.deleteLater()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def painter(self, document=None, **options):
        document = document or blank_painting("sprite", 64, 128)
        options.setdefault("kind", "sprite")
        options.setdefault("title", "Paint the sprite sheet")
        dialog = PixelPainterDialog(document, **options)
        self.dialogs.append(dialog)
        dialog.resize(1280, 860)
        dialog.canvas.set_zoom(8)
        return dialog

    def point(self, dialog, x, y):
        zoom = dialog.canvas.zoom
        return QPoint(x * zoom + zoom // 2, y * zoom + zoom // 2)

    def click(self, dialog, x, y, button=Qt.MouseButton.LeftButton, modifier=Qt.KeyboardModifier.NoModifier):
        QTest.mouseClick(dialog.canvas, button, modifier, self.point(dialog, x, y))

    def drag(self, dialog, start, end):
        canvas = dialog.canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.point(dialog, *start))
        QTest.mouseMove(canvas, self.point(dialog, *end))
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.point(dialog, *end))


class ConversionTests(PainterTestCase):
    def test_images_round_trip_between_pillow_and_qt(self):
        image = Image.new("RGBA", (3, 2), (1, 2, 3, 128))
        image.putpixel((2, 1), RED)
        back = image_from_qimage(qimage_from_image(image))
        self.assertEqual(back.tobytes(), image.tobytes())
        padded = QImage(3, 1, QImage.Format.Format_ARGB32)
        padded.fill(QColor(9, 8, 7, 255))
        self.assertEqual(image_from_qimage(padded).getpixel((2, 0)), (9, 8, 7, 255))


class CanvasTests(PainterTestCase):
    def test_canvas_is_the_sheet_times_the_zoom(self):
        dialog = self.painter()
        self.assertEqual((dialog.canvas.width(), dialog.canvas.height()), (64 * 8, 128 * 8))
        dialog.canvas.set_zoom(3)
        self.assertEqual(dialog.canvas.width(), 64 * 3)

    def test_left_button_paints_primary_and_right_button_paints_secondary(self):
        dialog = self.painter()
        dialog.tools.primary, dialog.tools.secondary = RED, BLUE
        self.drag(dialog, (1, 1), (4, 1))
        self.assertEqual({dialog.document.pixel_at(x, 1) for x in range(1, 5)}, {RED})
        self.click(dialog, 6, 6, Qt.MouseButton.RightButton)
        self.assertEqual(dialog.document.pixel_at(6, 6), BLUE)

    def test_canvas_draws_the_painted_pixels(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 2, 3)
        rendered = dialog.canvas.grab().toImage()
        self.assertEqual(rendered.pixelColor(self.point(dialog, 2, 3)).getRgb(), RED)

    def test_onion_skin_shows_the_previous_frame_in_a_warm_tint(self):
        dialog = self.painter()
        dialog.tools.primary = BLUE
        self.click(dialog, 2, 2)
        before = dialog.canvas.grab().toImage().pixelColor(self.point(dialog, 18, 2))
        dialog.canvas.set_active_frame(1)
        dialog.actions["onion"].setChecked(True)
        ghost = dialog.canvas.grab().toImage().pixelColor(self.point(dialog, 18, 2))
        self.assertNotEqual(ghost.getRgb(), before.getRgb())
        self.assertGreater(ghost.red(), ghost.blue())
        self.assertEqual(dialog.document.pixel_at(18, 2), CLEAR, "onion skin is only a guide")

    def test_locked_layer_shows_a_message_instead_of_painting(self):
        dialog = self.painter()
        dialog.document.update_layer(0, locked=True)
        self.click(dialog, 1, 1)
        self.assertIn("Unlock", dialog.notice.text())
        self.assertEqual(dialog.document.pixel_at(1, 1), CLEAR)

    def test_pressing_sets_the_active_frame(self):
        dialog = self.painter()
        self.click(dialog, 20, 40)
        self.assertEqual(dialog.canvas.active_frame, 5)


class MenuAndShortcutTests(PainterTestCase):
    def test_tool_shortcuts_switch_tools(self):
        dialog = self.painter()
        dialog.show()
        dialog.activateWindow()
        self.application.processEvents()
        for key, tool in ((Qt.Key.Key_E, "eraser"), (Qt.Key.Key_G, "fill"), (Qt.Key.Key_M, "select"), (Qt.Key.Key_B, "pencil")):
            QTest.keyClick(dialog.canvas, key)
            self.assertEqual(dialog.tools.tool, tool)
            self.assertTrue(dialog.tool_strip.buttons[tool].isChecked())

    def test_undo_and_redo_actions(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        self.assertTrue(dialog.actions["undo"].isEnabled())
        dialog.actions["undo"].trigger()
        self.assertEqual(dialog.document.pixel_at(1, 1), CLEAR)
        self.assertTrue(dialog.actions["redo"].isEnabled())
        dialog.actions["redo"].trigger()
        self.assertEqual(dialog.document.pixel_at(1, 1), RED)

    def test_escape_cancels_a_floating_selection_before_closing(self):
        dialog = self.painter()
        dialog.show()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        dialog.document.set_selection((0, 0, 4, 4))
        dialog.document.lift_selection()
        dialog.document.move_floating(3, 0)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.document.floating)
        self.assertEqual(dialog.document.pixel_at(1, 1), RED)

    def test_enter_never_closes_the_painter(self):
        dialog = self.painter()
        dialog.show()
        dialog.activateWindow()
        self.application.processEvents()
        dialog.color_panel.hex.setFocus()
        dialog.color_panel.hex.selectAll()
        QTest.keyClicks(dialog.color_panel.hex, "#102030")
        QTest.keyClick(dialog.color_panel.hex, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.tools.primary, (16, 32, 48, 255))
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Enter)
        self.assertTrue(dialog.isVisible())
        self.assertIsNone(dialog.result_png)

    def test_enter_places_moved_pixels(self):
        dialog = self.painter()
        dialog.show()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        dialog.document.set_selection((0, 0, 4, 4))
        dialog.document.lift_selection()
        dialog.document.move_floating(4, 0)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Return)
        self.assertFalse(dialog.document.floating)
        self.assertEqual(dialog.document.pixel_at(5, 1), RED)
        self.assertEqual(dialog.document.pixel_at(1, 1), CLEAR)
        self.assertTrue(dialog.isVisible())

    def test_copy_and_paste_use_the_system_clipboard(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        dialog.document.set_selection((0, 0, 3, 3))
        dialog.actions["copy"].trigger()
        copied = QApplication.clipboard().image()
        self.assertEqual((copied.width(), copied.height()), (3, 3))
        dialog.canvas.set_active_frame(1)
        dialog.document.clear_selection()
        dialog.actions["paste"].trigger()
        self.assertTrue(dialog.document.floating)
        dialog.actions["place"].trigger()
        self.assertEqual(dialog.document.pixel_at(17, 1), RED, "pastes at the active frame")

    def test_flip_frame_and_all_layers_option(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 0, 0)
        dialog.document.add_layer()
        dialog.tools.primary = BLUE
        self.click(dialog, 1, 0)
        dialog.canvas.set_active_frame(0)
        dialog.actions["all_layers"].setChecked(True)
        dialog.actions["flip_horizontal"].trigger()
        self.assertEqual(dialog.document.pixel_at(15, 0), RED)
        self.assertEqual(dialog.document.pixel_at(14, 0), BLUE)

    def test_copy_frame_to_another_frame(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 3, 5)
        dialog.canvas.set_active_frame(0)
        dialog.actions["copy_frame"].trigger()
        dialog.canvas.set_active_frame(6)
        dialog.actions["paste_frame"].trigger()
        self.assertEqual(dialog.document.pixel_at(32 + 3, 32 + 5), RED)

    def test_rows_follow_sheet_limits(self):
        dialog = self.painter()
        self.assertFalse(dialog.actions["remove_row"].isEnabled())
        dialog.actions["add_row"].trigger()
        self.assertEqual(dialog.document.height, 160)
        self.assertEqual(dialog.canvas.height(), 160 * 8)
        self.assertTrue(dialog.actions["remove_row"].isEnabled())

    def test_zoom_actions_step_through_levels(self):
        dialog = self.painter()
        dialog.actions["zoom_in"].trigger()
        self.assertGreater(dialog.canvas.zoom, 8)
        dialog.actions["zoom_out"].trigger()
        dialog.actions["zoom_out"].trigger()
        self.assertLess(dialog.canvas.zoom, 8)

    def test_mirror_toggle(self):
        dialog = self.painter()
        dialog.actions["mirror"].setChecked(True)
        dialog.tools.primary = RED
        self.click(dialog, 2, 2)
        self.assertEqual(dialog.document.pixel_at(13, 2), RED)


class PanelTests(PainterTestCase):
    def test_layer_list_is_top_first_and_controls_the_document(self):
        dialog = self.painter()
        dialog.layer_panel.add_button.click()
        names = [dialog.layer_panel.list.item(row).text() for row in range(dialog.layer_panel.list.count())]
        self.assertEqual(names[0].split("  ")[0], "Layer 2")
        self.assertEqual(dialog.document.active_index, 1)
        dialog.layer_panel.list.setCurrentRow(1)
        self.assertEqual(dialog.document.active_index, 0)
        dialog.layer_panel.list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.document.layers[1].visible)
        dialog.layer_panel.opacity.setValue(35)
        self.assertEqual(dialog.document.layers[0].opacity, 35)
        dialog.layer_panel.lock_button.click()
        self.assertTrue(dialog.document.layers[0].locked)

    def test_rename_layer(self):
        dialog = self.painter()
        with patch("pixelheart.pixel_panels.QInputDialog.getText", return_value=("Outline", True)):
            dialog.layer_panel.rename()
        self.assertEqual(dialog.document.layers[0].name, "Outline")

    def test_sheet_colors_and_replace(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.drag(dialog, (0, 0), (5, 0))
        self.assertEqual(dialog.color_panel.sheet_swatches.colors[0], RED)
        dialog.color_panel.sheet_swatches.chosen.emit(RED)
        self.assertEqual(dialog.tools.primary, RED)
        with patch("pixelheart.pixel_panels.QColorDialog.getColor", return_value=QColor(*BLUE)):
            dialog.color_panel.replace_color()
        self.assertEqual(dialog.document.pixel_at(3, 0), BLUE)
        self.assertIn("6", dialog.notice.text())

    def test_hex_entry_sets_the_primary_color_with_alpha(self):
        dialog = self.painter()
        dialog.color_panel.hex.setText("#10203080")
        dialog.color_panel.hex.editingFinished.emit()
        self.assertEqual(dialog.tools.primary, (16, 32, 48, 128))
        dialog.color_panel.hex.setText("nope")
        dialog.color_panel.hex.editingFinished.emit()
        self.assertEqual(dialog.tools.primary, (16, 32, 48, 128))

    def test_reference_from_file_and_from_a_supplied_source(self):
        temporary = tempfile.TemporaryDirectory(prefix="pixelheart-reference-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "villager.png"
        Image.new("RGBA", (64, 128), BLUE).save(path)
        dialog = self.painter(references=[("Original upload", lambda: (Image.new("RGBA", (64, 128), RED), "Original"))])
        with patch("pixelheart.pixel_painter.QFileDialog.getOpenFileName", return_value=(str(path), "")):
            dialog.add_reference_file()
        dialog.reference_actions[0].trigger()
        self.assertEqual([layer.reference for layer in dialog.document.layers], [False, True, True])
        self.assertEqual(dialog.document.layers[1].name, "villager")
        self.assertIsNone(dialog.document.flatten().getbbox())

    def test_preview_follows_the_active_row_and_plays_sprites(self):
        dialog = self.painter()
        dialog.canvas.set_active_frame(6)
        self.assertIn("Right", dialog.preview.caption.text())
        self.assertTrue(dialog.preview.timer.isActive())
        dialog.preview.play.setChecked(False)
        self.assertFalse(dialog.preview.timer.isActive())
        portrait = self.painter(blank_painting("portrait", 128, 192), kind="portrait")
        portrait.canvas.set_active_frame(1)
        self.assertIn("Happy", portrait.preview.caption.text())
        self.assertTrue(portrait.preview.play.isHidden())


class SaveTests(PainterTestCase):
    def test_save_returns_the_flattened_png(self):
        dialog = self.painter()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        dialog.document.add_reference(Image.new("RGBA", (64, 128), BLUE), "Guide")
        self.assertTrue(dialog.save())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        with Image.open(io.BytesIO(dialog.result_png)) as saved:
            self.assertEqual(saved.size, (64, 128))
            self.assertEqual(saved.convert("RGBA").getpixel((1, 1)), RED)
            self.assertEqual(saved.convert("RGBA").getpixel((5, 5)), CLEAR)

    def test_validation_errors_keep_the_painter_open(self):
        def refuse(payload):
            raise SheetError("That sheet needs one more row.")
        dialog = self.painter(validate=refuse)
        self.assertFalse(dialog.save())
        self.assertIn("one more row", dialog.notice.text())
        self.assertIsNone(dialog.result_png)

    def test_closing_with_changes_asks_first(self):
        dialog = self.painter()
        dialog.show()
        dialog.tools.primary = RED
        self.click(dialog, 1, 1)
        with patch("pixelheart.pixel_painter.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel) as asked:
            dialog.reject()
        asked.assert_called_once()
        self.assertTrue(dialog.isVisible())
        with patch("pixelheart.pixel_painter.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
            dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_closing_without_changes_does_not_ask(self):
        dialog = self.painter()
        dialog.show()
        with patch("pixelheart.pixel_painter.QMessageBox.question") as asked:
            dialog.reject()
        asked.assert_not_called()


class TilesheetSizeTests(PainterTestCase):
    def test_new_tilesheet_size_is_in_whole_tiles(self):
        def answer(dialog):
            spins = dialog.findChildren(QSpinBox)
            spins[0].setValue(5)
            spins[1].setValue(3)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, "exec", new=answer):
            self.assertEqual(choose_tilesheet_size(), (80, 48))
        with patch.object(QDialog, "exec", new=lambda dialog: QDialog.DialogCode.Rejected):
            self.assertIsNone(choose_tilesheet_size())


class FurniturePainterTests(PainterTestCase):
    def test_furniture_painting_shows_the_whole_piece_and_keeps_its_size(self):
        dialog = self.painter(blank_painting("furniture", 48, 32), kind="furniture", title="Paint the Oak Chair")
        self.assertFalse(dialog.actions["add_row"].isEnabled())
        self.assertFalse(dialog.actions["remove_row"].isEnabled())
        self.assertEqual(dialog.preview.caption.text(), "The whole piece · 48 × 32")
        dialog.preview.set_frame(2)
        self.assertEqual(dialog.preview.images[0].size().width(), 48)


if __name__ == "__main__":
    unittest.main()
