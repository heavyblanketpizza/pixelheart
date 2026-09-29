"""Exact, aliased raster helpers for the pixel painter."""

import unittest

from PIL import Image

from pixelheart_core.pixel_raster import (
    Mask, apply_mask, brush_mask, ellipse_mask, equal_color_mask, flood_mask,
    line_points, mirror_mask, rectangle_mask, union,
)


def pixels(mask):
    """Canvas coordinates painted by a mask."""
    if mask is None:
        return set()
    width, height = mask.image.size
    data = mask.image.load()
    return {(mask.left + x, mask.top + y) for y in range(height) for x in range(width) if data[x, y]}


class LineTests(unittest.TestCase):
    def test_line_includes_both_ends_without_gaps(self):
        points = line_points(0, 0, 5, 2)
        self.assertEqual(points[0], (0, 0))
        self.assertEqual(points[-1], (5, 2))
        self.assertEqual(len(points), 6)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            self.assertLessEqual(max(abs(x1 - x0), abs(y1 - y0)), 1)

    def test_single_point_line(self):
        self.assertEqual(line_points(3, 4, 3, 4), [(3, 4)])

    def test_steep_and_reversed_lines(self):
        self.assertEqual(line_points(2, 3, 2, 0), [(2, 3), (2, 2), (2, 1), (2, 0)])
        self.assertEqual(len(line_points(0, 0, -3, 7)), 8)


class MaskTests(unittest.TestCase):
    def test_brush_covers_square_centered_on_each_point(self):
        mask = brush_mask([(5, 5)], 3, (10, 10))
        self.assertEqual(pixels(mask), {(x, y) for x in range(4, 7) for y in range(4, 7)})
        even = brush_mask([(5, 5)], 2, (10, 10))
        self.assertEqual(pixels(even), {(5, 5), (6, 5), (5, 6), (6, 6)})

    def test_brush_clips_to_canvas_and_returns_none_outside(self):
        mask = brush_mask([(0, 0)], 3, (4, 4))
        self.assertEqual(pixels(mask), {(0, 0), (1, 0), (0, 1), (1, 1)})
        self.assertIsNone(brush_mask([(20, 20)], 1, (4, 4)))

    def test_rectangle_outline_and_fill(self):
        outline = rectangle_mask(1, 1, 4, 3, filled=False, bounds=(8, 8))
        self.assertIn((1, 1), pixels(outline))
        self.assertIn((4, 3), pixels(outline))
        self.assertNotIn((2, 2), pixels(outline))
        filled = rectangle_mask(4, 3, 1, 1, filled=True, bounds=(8, 8))
        self.assertEqual(len(pixels(filled)), 4 * 3)

    def test_ellipse_is_symmetric_and_inside_its_box(self):
        mask = ellipse_mask(0, 0, 6, 6, filled=False, bounds=(10, 10))
        painted = pixels(mask)
        self.assertTrue(painted)
        self.assertTrue(all(0 <= x <= 6 and 0 <= y <= 6 for x, y in painted))
        self.assertEqual(painted, {(6 - x, y) for x, y in painted})
        self.assertNotIn((3, 3), painted)
        self.assertIn((3, 3), pixels(ellipse_mask(0, 0, 6, 6, filled=True, bounds=(10, 10))))

    def test_union_merges_masks_with_different_origins(self):
        combined = union(brush_mask([(0, 0)], 1, (9, 9)), brush_mask([(8, 8)], 1, (9, 9)))
        self.assertEqual(pixels(combined), {(0, 0), (8, 8)})
        self.assertIsNone(union(None, None))


class MirrorTests(unittest.TestCase):
    def test_mirror_reflects_within_each_frame_column(self):
        mask = brush_mask([(1, 0), (17, 3)], 1, (32, 8))
        mirrored = mirror_mask(mask, frame_width=16, canvas_width=32)
        self.assertEqual(pixels(mirrored), {(1, 0), (14, 0), (17, 3), (30, 3)})

    def test_mirror_of_center_pixel_on_odd_frame_is_itself(self):
        mask = brush_mask([(2, 0)], 1, (5, 1))
        self.assertEqual(pixels(mirror_mask(mask, frame_width=5, canvas_width=5)), {(2, 0)})


class ColorMaskTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGBA", (4, 3), (0, 0, 0, 0))
        self.image.putpixel((0, 0), (255, 0, 0, 255))
        self.image.putpixel((1, 0), (255, 0, 0, 255))
        self.image.putpixel((3, 2), (255, 0, 0, 255))
        # Invisible pixels with different hidden colors are one transparent region.
        self.image.putpixel((2, 1), (9, 9, 9, 0))

    def tearDown(self):
        self.image.close()

    def test_equal_color_mask_matches_exact_rgba(self):
        mask = equal_color_mask(self.image, (255, 0, 0, 255))
        self.assertEqual(pixels(Mask(mask, 0, 0)), {(0, 0), (1, 0), (3, 2)})

    def test_transparent_pixels_match_regardless_of_hidden_color(self):
        mask = equal_color_mask(self.image, (1, 2, 3, 0))
        self.assertIn((2, 1), pixels(Mask(mask, 0, 0)))
        self.assertEqual(len(pixels(Mask(mask, 0, 0))), 12 - 3)

    def test_flood_is_four_connected(self):
        self.image.putpixel((2, 2), (255, 0, 0, 255))
        region = flood_mask(self.image, 0, 0)
        self.assertEqual(pixels(region), {(0, 0), (1, 0)})
        self.assertEqual(pixels(flood_mask(self.image, 3, 2)), {(2, 2), (3, 2)})


class ApplyMaskTests(unittest.TestCase):
    def test_apply_replaces_pixels_including_alpha(self):
        image = Image.new("RGBA", (3, 3), (10, 20, 30, 255))
        apply_mask(image, brush_mask([(1, 1)], 1, (3, 3)), (200, 100, 50, 128))
        self.assertEqual(image.getpixel((1, 1)), (200, 100, 50, 128))
        self.assertEqual(image.getpixel((0, 0)), (10, 20, 30, 255))
        apply_mask(image, brush_mask([(0, 0)], 1, (3, 3)), (0, 0, 0, 0))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))

    def test_apply_respects_clip_box(self):
        image = Image.new("RGBA", (4, 4))
        apply_mask(image, rectangle_mask(0, 0, 3, 3, filled=True, bounds=(4, 4)), (1, 2, 3, 255), clip=(1, 1, 3, 3))
        painted = {(x, y) for y in range(4) for x in range(4) if image.getpixel((x, y))[3]}
        self.assertEqual(painted, {(1, 1), (2, 1), (1, 2), (2, 2)})

    def test_apply_returns_changed_box(self):
        image = Image.new("RGBA", (4, 4))
        self.assertEqual(apply_mask(image, brush_mask([(2, 1)], 1, (4, 4)), (1, 1, 1, 255)), (2, 1, 3, 2))
        self.assertIsNone(apply_mask(image, None, (1, 1, 1, 255)))


if __name__ == "__main__":
    unittest.main()
