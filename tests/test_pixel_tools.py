"""Pointer behavior of each painter tool, independent of the Qt canvas."""

import unittest

from pixelheart_core.pixel_document import PixelDocument, PixelError
from pixelheart_core.pixel_tools import MAX_RECENT_COLORS, PixelTools

RED = (220, 40, 40, 255)
BLUE = (40, 60, 220, 255)
CLEAR = (0, 0, 0, 0)


def painted(document):
    image = document.flatten()
    return {(x, y) for y in range(image.height) for x in range(image.width) if image.getpixel((x, y))[3]}


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.document = PixelDocument.blank(8, 8, frame=(8, 8))
        self.tools = PixelTools(self.document)
        self.tools.primary = RED

    def drag(self, *points, **keys):
        (x, y), rest = points[0], points[1:]
        self.tools.press(x, y, **keys)
        for point in rest:
            self.tools.move(*point)
        self.tools.release(*(rest[-1] if rest else points[0]))


class PencilTests(ToolTestCase):
    def test_drag_paints_a_gapless_stroke_as_one_step(self):
        self.drag((0, 0), (4, 2))
        self.assertIn((0, 0), painted(self.document))
        self.assertIn((4, 2), painted(self.document))
        self.assertEqual(len(painted(self.document)), 5)
        self.document.undo()
        self.assertEqual(painted(self.document), set())

    def test_brush_size_and_secondary_color(self):
        self.tools.brush = 2
        self.drag((2, 2))
        self.assertEqual(len(painted(self.document)), 4)
        self.tools.brush = 1
        self.tools.secondary = BLUE
        self.drag((6, 6), secondary=True)
        self.assertEqual(self.document.pixel_at(6, 6), BLUE)

    def test_shift_click_draws_a_line_from_the_last_point(self):
        self.drag((0, 0))
        self.drag((3, 3), shift=True)
        self.assertEqual(painted(self.document), {(0, 0), (1, 1), (2, 2), (3, 3)})

    def test_mirror_paints_both_halves_of_the_frame(self):
        self.tools.mirror = True
        self.drag((1, 3))
        self.assertEqual(painted(self.document), {(1, 3), (6, 3)})

    def test_eraser_and_default_secondary_clear_pixels(self):
        self.drag((0, 0), (3, 0))
        self.tools.tool = "eraser"
        self.drag((1, 0))
        self.assertNotIn((1, 0), painted(self.document))
        self.tools.tool = "pencil"
        self.drag((2, 0), secondary=True)
        self.assertEqual(painted(self.document), {(0, 0), (3, 0)})

    def test_locked_layer_refuses_before_changing_anything(self):
        self.document.update_layer(0, locked=True)
        with self.assertRaises(PixelError):
            self.tools.press(1, 1)
        self.assertFalse(self.tools.active)

    def test_recent_colors_are_unique_newest_first_and_bounded(self):
        for value in range(MAX_RECENT_COLORS + 3):
            self.tools.primary = (value, 1, 2, 255)
            self.drag((0, 0))
        self.tools.primary = (0, 1, 2, 255)
        self.drag((1, 1))
        self.assertEqual(self.tools.recent[0], (0, 1, 2, 255))
        self.assertEqual(len(self.tools.recent), MAX_RECENT_COLORS)
        self.assertEqual(len(set(self.tools.recent)), MAX_RECENT_COLORS)

    def test_cancel_discards_a_stroke_in_progress(self):
        self.tools.press(0, 0)
        self.tools.move(5, 0)
        self.tools.cancel()
        self.assertEqual(painted(self.document), set())
        self.assertFalse(self.document.can_undo)


class ShapeTests(ToolTestCase):
    def test_line_preview_keeps_only_the_final_line(self):
        self.tools.tool = "line"
        self.drag((0, 0), (7, 0), (0, 3))
        self.assertEqual(painted(self.document), {(0, 0), (0, 1), (0, 2), (0, 3)})

    def test_shift_constrains_lines_to_45_degrees(self):
        self.tools.tool = "line"
        self.drag((0, 0), (5, 4), shift=True)
        self.assertEqual(painted(self.document), {(i, i) for i in range(5)})

    def test_rectangle_outline_filled_and_square(self):
        self.tools.tool = "rectangle"
        self.drag((1, 1), (4, 3))
        self.assertNotIn((2, 2), painted(self.document))
        self.document.undo()
        self.tools.filled = True
        self.drag((1, 1), (4, 3))
        self.assertEqual(len(painted(self.document)), 12)
        self.document.undo()
        self.drag((0, 0), (5, 2), shift=True)
        self.assertEqual(len(painted(self.document)), 36)

    def test_ellipse_stays_inside_its_drag_box(self):
        self.tools.tool = "ellipse"
        self.drag((1, 1), (6, 5))
        self.assertTrue(all(1 <= x <= 6 and 1 <= y <= 5 for x, y in painted(self.document)))


class FillAndPickerTests(ToolTestCase):
    def test_fill_connected_or_every_matching_pixel(self):
        self.tools.tool = "line"
        self.drag((3, 0), (3, 7))
        self.tools.tool = "fill"
        self.tools.primary = BLUE
        self.drag((0, 0))
        self.assertEqual(self.document.pixel_at(0, 0), BLUE)
        self.assertEqual(self.document.pixel_at(7, 7), CLEAR)
        self.document.undo()
        self.tools.contiguous = False
        self.drag((0, 0))
        self.assertEqual(self.document.pixel_at(7, 7), BLUE)
        self.assertEqual(self.document.pixel_at(3, 3), RED)

    def test_mirrored_fill_also_fills_from_the_reflected_point(self):
        self.tools.tool = "line"
        self.drag((3, 0), (3, 7))
        self.drag((4, 0), (4, 7))
        self.tools.tool = "fill"
        self.tools.mirror = True
        self.tools.primary = BLUE
        self.drag((0, 0))
        self.assertEqual(self.document.pixel_at(7, 0), BLUE)

    def test_picker_reads_the_flattened_sheet(self):
        self.drag((2, 2))
        self.tools.tool = "picker"
        self.tools.primary = BLUE
        self.drag((2, 2))
        self.assertEqual(self.tools.primary, RED)
        self.drag((5, 5), secondary=True)
        self.assertEqual(self.tools.secondary, CLEAR)

    def test_alt_picks_without_painting(self):
        self.drag((2, 2))
        self.tools.primary = BLUE
        self.drag((2, 2), alt=True)
        self.assertEqual(self.tools.primary, RED)
        self.assertEqual(self.document.pixel_at(2, 2), RED)

    def test_swap_colors(self):
        self.tools.secondary = BLUE
        self.tools.swap_colors()
        self.assertEqual((self.tools.primary, self.tools.secondary), (BLUE, RED))


class SelectTests(ToolTestCase):
    def setUp(self):
        super().setUp()
        self.drag((0, 0), (1, 0))
        self.tools.tool = "select"

    def test_drag_selects_an_inclusive_box(self):
        self.drag((0, 0), (2, 1))
        self.assertEqual(self.document.selection, (0, 0, 3, 2))

    def test_click_without_dragging_deselects(self):
        self.drag((0, 0), (2, 1))
        self.drag((6, 6))
        self.assertIsNone(self.document.selection)

    def test_dragging_inside_the_selection_moves_its_pixels(self):
        self.drag((0, 0), (1, 0))
        self.drag((0, 0), (2, 3))
        self.assertTrue(self.document.floating)
        self.drag((2, 3), (3, 3))
        self.assertEqual(self.document.selection, (3, 3, 5, 4))
        self.drag((7, 7), (7, 7))
        self.assertFalse(self.document.floating)
        self.assertEqual(painted(self.document), {(3, 3), (4, 3)})
        self.document.undo()
        self.assertEqual(painted(self.document), {(0, 0), (1, 0)})


if __name__ == "__main__":
    unittest.main()
