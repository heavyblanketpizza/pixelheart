"""Calendar fallbacks render crisply without painting outside their bounds."""

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from pixelheart.calendar_art import MOTIF_SIZE, draw_motif


class CalendarArtTests(unittest.TestCase):
    def test_all_fallback_motifs_render_at_integer_scales_without_clipping(self):
        paper = QColor("#13579b")
        for name in ("spring", "summer", "fall", "winter", "gift", "heart", "festival"):
            original = None
            for scale in (1, 2, 3):
                with self.subTest(motif=name, scale=scale):
                    offset_x, offset_y = 3, 5
                    width, height = MOTIF_SIZE * scale + 7, MOTIF_SIZE * scale + 9
                    image = QImage(width, height, QImage.Format.Format_ARGB32)
                    image.fill(paper)
                    painter = QPainter(image)
                    try:
                        draw_motif(painter, name, offset_x, offset_y, scale)
                    finally:
                        painter.end()
                    rendered = image.copy(offset_x, offset_y, MOTIF_SIZE * scale, MOTIF_SIZE * scale)
                    if original is None:
                        original = rendered
                        self.assertTrue(any(original.pixelColor(x, y) != paper
                                            for x in range(MOTIF_SIZE) for y in range(MOTIF_SIZE)))
                    else:
                        self.assertEqual(rendered, original.scaled(rendered.size(),
                                                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                                                 Qt.TransformationMode.FastTransformation))
                    for y in range(height):
                        for x in range(width):
                            if not (offset_x <= x < offset_x + rendered.width()
                                    and offset_y <= y < offset_y + rendered.height()):
                                self.assertEqual(image.pixelColor(x, y), paper)

    def test_unknown_motif_leaves_the_paint_surface_unchanged(self):
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor("#13579b"))
        original = image.copy()
        painter = QPainter(image)
        try:
            draw_motif(painter, "unknown", 0, 0)
        finally:
            painter.end()
        self.assertEqual(image, original)
