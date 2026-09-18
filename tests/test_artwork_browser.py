"""Read-only sheet selection, frame mapping, and walking playback coverage."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from pixelheart.artwork_browser import DIRECTIONS, SheetBrowser
from pixelheart_core.artwork import ArtworkValidationError


class SheetBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-frames-")
        self.root = Path(self.temporary.name)
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if isValid(widget):
                widget.close()
                widget.deleteLater()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    def browser(self, kind, parent=None):
        widget = SheetBrowser(kind, parent)
        self.widgets.append(widget)
        widget.resize(330, 300)
        return widget

    def sheet(self, kind, rows, scale=1):
        columns = 2 if kind == "portrait" else 4
        width, height = (64 * scale, 64 * scale) if kind == "portrait" else (16 * scale, 32 * scale)
        colors = [(index * 29 % 256, index * 61 % 256, index * 107 % 256, 255) for index in range(columns * rows)]
        path = self.root / f"{kind}-{rows}-{scale}.png"
        with Image.new("RGBA", (columns * width, rows * height)) as image:
            for index, color in enumerate(colors):
                left, top = (index % columns) * width, (index // columns) * height
                image.paste(color, (left, top, left + width, top + height))
            image.save(path)
        return path, colors

    def assert_frame_color(self, browser, index, expected):
        browser.select_frame(index)
        cropped = browser.pixmap.copy(browser.frame_rect(index)).toImage()
        self.assertEqual(cropped.pixelColor(0, 0).getRgb(), expected)
        self.assertEqual(cropped.pixelColor(cropped.width() - 1, cropped.height() - 1).getRgb(), expected)

    def test_all_portrait_expressions_map_in_sheet_order_and_preserve_source(self):
        browser = self.browser("portrait")
        path, colors = self.sheet("portrait", rows=5, scale=2)
        original = path.read_bytes()
        browser.set_image(path)
        self.assertEqual(browser.frame_count, 10)
        self.assertEqual(browser.frame_size.width(), 128)
        self.assertEqual(browser.frame_selector.count(), 10)
        for index, color in enumerate(colors):
            self.assert_frame_color(browser, index, color)
            self.assertIn(f"{index + 1} of 10", browser.status.text())
            self.assertIn(f"${index}", browser.frame_selector.currentText())
        self.assertIn("Happy", browser.frame_selector.itemText(1))
        self.assertEqual(browser.frame_selector.itemText(6), "Expression · $6")
        self.assertEqual(path.read_bytes(), original)

    def test_sprite_directions_preview_each_row_and_include_extra_poses(self):
        browser = self.browser("sprite")
        path, colors = self.sheet("sprite", rows=13, scale=2)
        browser.set_image(path)
        self.assertEqual(browser.frame_count, 52)
        self.assertEqual((browser.frame_size.width(), browser.frame_size.height()), (32, 64))
        for direction, name in enumerate(DIRECTIONS):
            browser.direction.setCurrentIndex(direction)
            for column in range(4):
                index = direction * 4 + column
                self.assertEqual(browser.current_frame, index)
                self.assertIn(name.lower() + " walk", browser.status.text())
                cropped = browser.pixmap.copy(browser.frame_rect(index)).toImage()
                self.assertEqual(cropped.pixelColor(0, 0).getRgb(), colors[index])
                browser.advance_frame()
            self.assertEqual(browser.current_frame, direction * 4)
        self.assert_frame_color(browser, 51, colors[51])
        self.assertIn("extra pose", browser.status.text())
        self.assertEqual(browser.frame_selector.maximum(), 51)

    def test_playback_speed_and_stop_on_selection_hide_reload_and_removal(self):
        browser = self.browser("sprite")
        path, _ = self.sheet("sprite", rows=5)
        browser.set_image(path)
        browser.show()
        self.application.processEvents()
        browser.play_button.setChecked(True)
        self.assertTrue(browser.timer.isActive())
        browser.speed.setValue(10)
        self.assertEqual(browser.timer.interval(), 100)
        elapsed = QSignalSpy(browser.timer.timeout)
        self.assertTrue(elapsed.wait(500))
        self.assertEqual(browser.current_frame, 1)
        browser.select_frame(18)
        self.assertFalse(browser.timer.isActive())
        browser.play_button.setChecked(True)
        self.assertEqual(browser.current_frame, 0)
        self.assertTrue(browser.timer.isActive())
        browser.hide()
        self.assertFalse(browser.timer.isActive())
        self.assertFalse(browser.play_button.isChecked())
        browser.show()
        self.assertFalse(browser.timer.isActive())
        browser.play_button.setChecked(True)
        browser.set_image(path)
        self.assertFalse(browser.timer.isActive())
        browser.play_button.setChecked(True)
        browser.set_image()
        self.assertFalse(browser.timer.isActive())
        self.assertEqual(browser.frame_count, 0)
        self.assertTrue(browser.pixmap.isNull())
        self.assertFalse(browser.play_button.isEnabled())

    def test_timer_is_owned_and_destroyed_with_parent(self):
        parent = QWidget()
        browser = self.browser("sprite", parent)
        path, _ = self.sheet("sprite", rows=4)
        browser.set_image(path)
        parent.show()
        browser.show()
        self.application.processEvents()
        browser.play_button.setChecked(True)
        timer = browser.timer
        self.assertTrue(timer.isActive())
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(timer))
        self.assertFalse(isValid(browser))

    def test_keyboard_selection_updates_enlarged_frame_and_pauses(self):
        browser = self.browser("sprite")
        path, colors = self.sheet("sprite", rows=4)
        browser.set_image(path)
        browser.show()
        self.application.processEvents()
        browser.play_button.setChecked(True)
        QTest.keyClick(browser.preview, Qt.Key.Key_Right)
        self.assertEqual(browser.current_frame, 1)
        self.assertEqual(browser.direction.currentIndex(), 0)
        self.assertFalse(browser.timer.isActive())
        self.assert_frame_color(browser, 9, colors[9])
        self.assertIn("Frame 9", browser.preview.accessibleDescription())
        QTest.keyClick(browser.preview, Qt.Key.Key_Down)
        self.assertEqual(browser.current_frame, 13)
        QTest.keyClick(browser.preview, Qt.Key.Key_End)
        self.assertEqual(browser.current_frame, 15)
        QTest.keyClick(browser.preview, Qt.Key.Key_Home)
        self.assertEqual(browser.current_frame, 0)

    def test_malformed_layout_displays_whole_sheet_without_inventing_frames(self):
        for kind, dimensions in (("portrait", (128, 191)), ("portrait", (129, 194)), ("sprite", (64, 130)), ("sprite", (32, 64))):
            with self.subTest(kind=kind, dimensions=dimensions):
                path = self.root / "incompatible.png"
                with Image.new("RGBA", dimensions, "red") as image:
                    image.save(path)
                browser = self.browser(kind)
                browser.set_image(path)
                self.assertEqual(browser.frame_count, 0)
                self.assertEqual((browser.pixmap.width(), browser.pixmap.height()), dimensions)
                self.assertIn("whole sheet", browser.hint.text())
                self.assertFalse(browser.frame_selector.isEnabled())
                self.assertFalse(browser.sheet_layout.isEnabled())
                self.assertTrue(browser.frame_rect(0).isEmpty())
                self.assertFalse(browser.timer.isActive())

    def test_invalid_png_is_inspected_before_qt_loading_and_clears_previous_sheet(self):
        browser = self.browser("portrait")
        path, _ = self.sheet("portrait", rows=3)
        browser.set_image(path)
        broken = self.root / "broken.png"
        broken.write_bytes(b"not a PNG")
        browser.set_image(broken)
        self.assertTrue(browser.pixmap.isNull())
        self.assertEqual(browser.frame_count, 0)
        self.assertIn("cannot be read", browser.hint.text())
        with patch("pixelheart.artwork_browser.inspect_artwork", side_effect=ArtworkValidationError("Rejected before display")), patch("pixelheart.artwork_browser.QPixmap", wraps=QPixmap) as pixmap:
            browser.set_image(path)
            self.assertTrue(all(not call.args for call in pixmap.call_args_list))
        self.assertIn("Rejected before display", browser.hint.text())

    def test_empty_states_offer_template_or_upload_without_thumbnail_grid(self):
        for kind in ("portrait", "sprite"):
            browser = self.browser(kind)
            self.assertEqual(browser.frame_count, 0)
            self.assertFalse(hasattr(browser, "frames"))
            self.assertFalse(browser.frame_selector.isEnabled())
            self.assertFalse(browser.previous_button.isEnabled())
            self.assertFalse(browser.next_button.isEnabled())
            self.assertFalse(browser.sheet_layout.isEnabled())
            self.assertIn("template", browser.hint.text())
            self.assertLess(browser.preview.height(), 180)

    def test_selectors_and_previous_next_visit_every_frame_without_wrapping(self):
        for kind, rows in (("portrait", 4), ("sprite", 13)):
            browser = self.browser(kind)
            path, _ = self.sheet(kind, rows)
            browser.set_image(path)
            browser.show()
            self.application.processEvents()
            changed = QSignalSpy(browser.frame_changed)
            QTest.mouseClick(browser.next_button, Qt.MouseButton.LeftButton)
            self.assertEqual(browser.current_frame, 1)
            self.assertEqual(changed.count(), 1)
            QTest.mouseClick(browser.previous_button, Qt.MouseButton.LeftButton)
            self.assertEqual(browser.current_frame, 0)
            self.assertFalse(browser.previous_button.isEnabled())
            if kind == "portrait":
                QTest.keyClick(browser.frame_selector, Qt.Key.Key_Down)
            else:
                QTest.keyClick(browser.frame_selector, Qt.Key.Key_Up)
            self.assertEqual(browser.current_frame, 1)
            browser.select_frame(browser.frame_count - 1)
            self.assertFalse(browser.next_button.isEnabled())
            browser.select_frame(browser.frame_count)
            self.assertEqual(browser.current_frame, browser.frame_count - 1)

    def test_short_reference_sheets_preview_without_inventing_missing_frames(self):
        for kind, rows in (("portrait", 1), ("portrait", 2), ("sprite", 2)):
            browser = self.browser(kind)
            path, colors = self.sheet(kind, rows)
            browser.set_image(path)
            self.assertEqual(browser.frame_count, len(colors))
            self.assertTrue(browser.frame_selector.isEnabled())
            self.assert_frame_color(browser, len(colors) - 1, colors[-1])
            if kind == "sprite":
                self.assertFalse(browser.play_button.isEnabled())
                before = browser.current_frame
                browser.advance_frame()
                self.assertEqual(browser.current_frame, before)

    def test_sheet_layout_is_optional_and_click_selects_exact_source_frame(self):
        for kind, rows in (("portrait", 5), ("sprite", 13)):
            browser = self.browser(kind)
            path, colors = self.sheet(kind, rows)
            original = path.read_bytes()
            browser.set_image(path)
            browser.show()
            self.application.processEvents()
            self.assertFalse(browser.sheet_layout.isChecked())
            focused_height = browser.preview.height()
            QTest.mouseClick(browser.sheet_layout, Qt.MouseButton.LeftButton)
            self.application.processEvents()
            self.assertTrue(browser.sheet_layout.isChecked())
            self.assertGreater(browser.preview.height(), focused_height)
            last = browser.frame_count - 1
            cell = browser.preview.sheet_cell_rect(last)
            rendered = browser.preview.grab().toImage()
            ratio = rendered.devicePixelRatio()
            position = cell.center()
            # An unselected cell renders the original sheet pixels, not a collage.
            self.assertEqual(rendered.pixelColor(round(position.x() * ratio), round(position.y() * ratio)).getRgb(), colors[last])
            QTest.mouseClick(browser.preview, Qt.MouseButton.LeftButton, pos=position)
            self.assertEqual(browser.current_frame, last)
            self.assertIn(f"Row {rows}, column {browser.columns}", browser.preview.accessibleDescription())
            selected = browser.preview.grab().toImage()
            self.assertNotEqual(selected.pixelColor(round(position.x() * ratio), round(position.y() * ratio)).getRgb(), colors[last])
            QTest.mouseClick(browser.sheet_layout, Qt.MouseButton.LeftButton)
            self.assertEqual(browser.preview.height(), focused_height)
            self.assert_frame_color(browser, last, colors[last])
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
