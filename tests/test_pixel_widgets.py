"""Hearts, titles and portrait frames drawn with game or original pieces."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from pixelheart import skin
from pixelheart.pixel_widgets import HeartMeter, PixelTitle, PortraitFrame
from pixelheart_core.game_ui import UiPiece
from tests.qt_support import QtTestCase


class PixelWidgetTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        skin.refresh_pieces(None)

    def test_heart_meter_reports_progress_for_screen_readers(self):
        meter = HeartMeter(8, 3)
        self.addCleanup(meter.deleteLater)
        self.assertEqual(meter.accessibleName(), "3 of 8 hearts")
        meter.set_value(9)
        self.assertEqual(meter.filled(), 8)
        self.assertEqual(meter.sizeHint().width(), 8 * 16)
        meter.grab()

    def test_title_draws_game_font_when_available_and_falls_back(self):
        title = PixelTitle("Hello")
        self.addCleanup(title.deleteLater)
        self.assertFalse(title.uses_game_font())
        fallback_width = title.sizeHint().width()
        atlas = Image.new("RGBA", (128, 592), (0, 0, 0, 0))
        for index in range(95):
            x, y = index * 8 % 128, index * 8 // 128 * 16
            for dx in range(1, 6):
                atlas.putpixel((x + dx, y + 8), (86, 22, 12, 255))
        path = self.folder / "font.png"
        atlas.save(path)
        with patch.dict(skin._STATE["pieces"], {"font": UiPiece(path, 0)}):
            title.setText("Hello there")
            self.assertTrue(title.uses_game_font())
            self.assertEqual(title.text(), "Hello there")
            self.assertEqual(title.accessibleName(), "Hello there")
            self.assertEqual(title.sizeHint().width(), (10 * 6 + 4) * 2)
            title.grab()
            title.setText("Café ♥")
            self.assertFalse(title.uses_game_font())
        self.assertGreater(fallback_width, 0)

    def test_portrait_frame_uses_first_frame_or_placeholder(self):
        frame = PortraitFrame(128)
        self.addCleanup(frame.deleteLater)
        self.assertFalse(frame.has_portrait())
        sheet = Image.new("RGBA", (128, 192), (10, 200, 10, 255))
        sheet.putpixel((70, 0), (255, 0, 0, 255))
        path = self.folder / "portrait.png"
        sheet.save(path)
        frame.set_portrait(path)
        self.assertTrue(frame.has_portrait())
        self.assertEqual(frame.portrait_pixmap().width(), 128)
        self.assertNotEqual(frame.portrait_pixmap().toImage().pixelColor(127, 0).red(), 255)
        frame.set_portrait(self.folder / "missing.png")
        self.assertFalse(frame.has_portrait())


if __name__ == "__main__":
    unittest.main()
