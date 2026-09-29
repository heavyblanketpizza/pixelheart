"""Layers, history, selection, frame tools and recoloring for painted sheets."""

import unittest

from PIL import Image

from pixelheart_core.pixel_document import MAX_LAYERS, PixelDocument, PixelError
from pixelheart_core.pixel_raster import brush_mask, rectangle_mask

RED = (220, 40, 40, 255)
BLUE = (40, 60, 220, 255)
GHOST = (10, 200, 90, 128)
CLEAR = (0, 0, 0, 0)


def dot(document, x, y, color, label="Pencil"):
    document.begin_edit(label)
    document.paint(brush_mask([(x, y)], 1, (document.width, document.height)), color)
    return document.commit_edit()


class CompositeTests(unittest.TestCase):
    def test_blank_document_has_one_transparent_layer(self):
        document = PixelDocument.blank(4, 3, frame=(2, 3))
        self.assertEqual((document.width, document.height), (4, 3))
        self.assertEqual(len(document.layers), 1)
        self.assertEqual(document.layers[0].name, "Layer 1")
        self.assertEqual(document.flatten().getbbox(), None)
        self.assertFalse(document.modified)

    def test_from_image_keeps_exact_pixels(self):
        image = Image.new("RGBA", (2, 2), GHOST)
        document = PixelDocument.from_image(image, name="Sheet")
        self.assertEqual(document.layers[0].name, "Sheet")
        self.assertEqual(document.flatten().getpixel((1, 1)), GHOST)
        image.putpixel((0, 0), RED)
        self.assertEqual(document.flatten().getpixel((0, 0)), GHOST, "the document owns a copy")

    def test_upper_layers_composite_over_lower_with_opacity(self):
        document = PixelDocument.blank(2, 1)
        dot(document, 0, 0, BLUE)
        document.add_layer()
        dot(document, 0, 0, RED)
        dot(document, 1, 0, RED)
        self.assertEqual(document.flatten().getpixel((0, 0)), RED)
        document.update_layer(1, opacity=50)
        mixed = document.flatten().getpixel((0, 0))
        self.assertTrue(100 < mixed[0] < 200 and mixed[3] == 255)
        self.assertEqual(document.flatten().getpixel((1, 0))[3], 128)

    def test_hidden_and_reference_layers_never_reach_the_flattened_sheet(self):
        document = PixelDocument.blank(2, 1)
        dot(document, 0, 0, BLUE)
        document.add_reference(Image.new("RGBA", (2, 1), RED), "Villager")
        self.assertEqual(document.flatten().getpixel((1, 0)), CLEAR)
        self.assertNotEqual(document.composite().getpixel((1, 0)), CLEAR, "references show while painting")
        document.update_layer(0, visible=False)
        self.assertIsNone(document.flatten().getbbox())

    def test_composite_of_a_box_matches_the_full_image(self):
        document = PixelDocument.blank(6, 6)
        dot(document, 3, 4, RED)
        self.assertEqual(document.composite((2, 3, 5, 6)).getpixel((1, 1)), RED)


class EditHistoryTests(unittest.TestCase):
    def test_edit_is_one_undo_step_and_redo_restores_it(self):
        document = PixelDocument.blank(4, 4)
        document.begin_edit("Pencil")
        for x in range(4):
            document.paint(brush_mask([(x, 1)], 1, (4, 4)), RED)
        self.assertTrue(document.commit_edit())
        self.assertTrue(document.modified)
        self.assertEqual(document.undo_label, "Pencil")
        self.assertTrue(document.undo())
        self.assertIsNone(document.flatten().getbbox())
        self.assertFalse(document.modified)
        self.assertTrue(document.redo())
        self.assertEqual(document.flatten().getpixel((3, 1)), RED)

    def test_unchanged_edit_leaves_no_history(self):
        document = PixelDocument.blank(2, 2)
        document.begin_edit("Eraser")
        document.paint(brush_mask([(0, 0)], 1, (2, 2)), CLEAR)
        self.assertFalse(document.commit_edit())
        self.assertFalse(document.can_undo)

    def test_restore_edit_supports_live_shape_previews(self):
        document = PixelDocument.blank(8, 8)
        document.begin_edit("Rectangle")
        document.paint(rectangle_mask(0, 0, 6, 6, filled=True, bounds=(8, 8)), RED)
        document.restore_edit()
        document.paint(rectangle_mask(0, 0, 1, 1, filled=True, bounds=(8, 8)), RED)
        document.commit_edit()
        self.assertEqual(document.flatten().getbbox(), (0, 0, 2, 2))

    def test_cancel_edit_discards_changes(self):
        document = PixelDocument.blank(2, 2)
        document.begin_edit("Pencil")
        document.paint(brush_mask([(0, 0)], 1, (2, 2)), RED)
        document.cancel_edit()
        self.assertIsNone(document.flatten().getbbox())
        self.assertFalse(document.can_undo)

    def test_locked_and_hidden_layers_refuse_edits(self):
        document = PixelDocument.blank(2, 2)
        document.update_layer(0, locked=True)
        with self.assertRaisesRegex(PixelError, "Unlock"):
            document.begin_edit("Pencil")
        document.update_layer(0, locked=False, visible=False)
        with self.assertRaisesRegex(PixelError, "Show"):
            document.begin_edit("Pencil")

    def test_new_step_clears_redo(self):
        document = PixelDocument.blank(2, 2)
        dot(document, 0, 0, RED)
        document.undo()
        dot(document, 1, 1, BLUE)
        self.assertFalse(document.can_redo)

    def test_mark_saved_tracks_the_saved_state(self):
        document = PixelDocument.blank(2, 2)
        dot(document, 0, 0, RED)
        document.mark_saved()
        self.assertFalse(document.modified)
        dot(document, 1, 1, RED)
        self.assertTrue(document.modified)
        document.undo()
        self.assertFalse(document.modified)
        document.undo()
        self.assertTrue(document.modified)

    def test_history_is_bounded(self):
        document = PixelDocument.blank(2, 2)
        for step in range(205):
            dot(document, step % 2, 0, (step % 250, 0, 0, 255))
        undone = 0
        while document.undo():
            undone += 1
        self.assertEqual(undone, 200)

    def test_dirty_box_accumulates_until_taken(self):
        document = PixelDocument.blank(8, 8)
        document.take_dirty()
        dot(document, 1, 1, RED)
        dot(document, 5, 2, RED)
        self.assertEqual(document.take_dirty(), (1, 1, 6, 3))
        self.assertIsNone(document.take_dirty())
        document.add_layer()
        self.assertEqual(document.take_dirty(), (0, 0, 8, 8))


class LayerTests(unittest.TestCase):
    def test_add_duplicate_move_delete_are_undoable(self):
        document = PixelDocument.blank(2, 2)
        dot(document, 0, 0, RED)
        document.add_layer("Shading")
        self.assertEqual(document.active_layer.name, "Shading")
        document.duplicate_layer(0)
        self.assertEqual([layer.name for layer in document.layers], ["Layer 1", "Layer 1 copy", "Shading"])
        document.move_layer(2, 0)
        self.assertEqual(document.layers[0].name, "Shading")
        document.delete_layer(0)
        self.assertEqual(len(document.layers), 2)
        for _ in range(3):
            document.undo()
        self.assertEqual([layer.name for layer in document.layers], ["Layer 1", "Shading"])

    def test_duplicate_owns_its_pixels(self):
        document = PixelDocument.blank(2, 2)
        dot(document, 0, 0, RED)
        document.duplicate_layer(0)
        document.set_active(1)
        dot(document, 0, 0, BLUE)
        self.assertEqual(document.layers[0].image.getpixel((0, 0)), RED)

    def test_deleted_layer_returns_with_its_pixels(self):
        document = PixelDocument.blank(2, 2)
        document.add_layer()
        dot(document, 1, 1, BLUE)
        document.delete_layer(1)
        self.assertIsNone(document.flatten().getbbox())
        document.undo()
        self.assertEqual(document.flatten().getpixel((1, 1)), BLUE)

    def test_last_layer_cannot_be_deleted_and_layer_count_is_bounded(self):
        document = PixelDocument.blank(1, 1)
        with self.assertRaises(PixelError):
            document.delete_layer(0)
        for _ in range(MAX_LAYERS - 1):
            document.add_layer()
        with self.assertRaises(PixelError):
            document.add_layer()

    def test_rename_and_opacity_changes_merge_into_one_step_when_requested(self):
        document = PixelDocument.blank(1, 1)
        for value in (90, 70, 40):
            document.update_layer(0, opacity=value, merge=True)
        self.assertEqual(document.layers[0].opacity, 40)
        document.undo()
        self.assertEqual(document.layers[0].opacity, 100)
        document.update_layer(0, name="  Outline  ")
        self.assertEqual(document.layers[0].name, "Outline")
        with self.assertRaises(PixelError):
            document.update_layer(0, name="   ")

    def test_merge_down_bakes_opacity_and_is_undoable(self):
        document = PixelDocument.blank(2, 1)
        dot(document, 0, 0, BLUE)
        document.add_layer()
        dot(document, 1, 0, RED)
        document.update_layer(1, opacity=50)
        before = document.flatten().tobytes()
        document.merge_down(1)
        self.assertEqual(len(document.layers), 1)
        self.assertEqual(document.flatten().tobytes(), before)
        document.undo()
        self.assertEqual(len(document.layers), 2)

    def test_reference_layers_are_locked_faded_and_cannot_merge(self):
        document = PixelDocument.blank(4, 4)
        reference = document.add_reference(Image.new("RGBA", (8, 2), RED), "From the game")
        self.assertTrue(reference.reference and reference.locked)
        self.assertEqual(reference.opacity, 40)
        self.assertEqual(document.active_index, 0, "painting continues on the artwork layer")
        self.assertEqual(reference.image.size, (4, 4), "references are cropped or padded to the canvas")
        with self.assertRaises(PixelError):
            document.merge_down(len(document.layers) - 1)


class SheetSizeTests(unittest.TestCase):
    def test_rows_are_added_and_removed_at_the_bottom(self):
        document = PixelDocument.blank(4, 4, frame=(2, 2))
        dot(document, 0, 3, RED)
        document.resize_height(8)
        self.assertEqual(document.height, 8)
        self.assertEqual(document.flatten().getpixel((0, 3)), RED)
        document.resize_height(2)
        self.assertIsNone(document.flatten().getbbox())
        document.undo()
        document.undo()
        self.assertEqual(document.height, 4)
        self.assertEqual(document.flatten().getpixel((0, 3)), RED)

    def test_frames_follow_the_grid(self):
        document = PixelDocument.blank(8, 4, frame=(4, 2))
        self.assertEqual(document.frame_count, 4)
        self.assertEqual(document.frame_box(3), (4, 2, 8, 4))
        self.assertEqual(document.frame_at(5, 1), 1)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.document = PixelDocument.blank(6, 6)
        self.document.begin_edit("Fill")
        self.document.paint(rectangle_mask(0, 0, 1, 1, filled=True, bounds=(6, 6)), GHOST)
        self.document.commit_edit()

    def test_selection_clips_painting(self):
        self.document.set_selection((2, 2, 4, 4))
        self.document.begin_edit("Fill")
        self.document.paint(rectangle_mask(0, 0, 5, 5, filled=True, bounds=(6, 6)), RED)
        self.document.commit_edit()
        self.assertEqual(self.document.flatten().getpixel((5, 5)), CLEAR)
        self.assertEqual(self.document.flatten().getpixel((3, 3)), RED)

    def test_selection_is_clamped_and_empty_selection_clears(self):
        self.document.set_selection((-3, 4, 99, 2))
        self.assertEqual(self.document.selection, (0, 2, 6, 4))
        self.document.set_selection((3, 3, 3, 5))
        self.assertIsNone(self.document.selection)

    def test_lift_and_drop_in_place_keeps_semi_transparent_pixels(self):
        self.document.set_selection((0, 0, 2, 2))
        self.document.lift_selection()
        self.assertTrue(self.document.floating)
        self.assertFalse(self.document.drop_floating(), "nothing moved, so nothing to undo")
        self.assertEqual(self.document.flatten().getpixel((1, 1)), GHOST)

    def test_moving_a_selection_is_one_undo_step(self):
        self.document.set_selection((0, 0, 2, 2))
        self.document.lift_selection()
        self.document.move_floating(3, 2)
        self.document.move_floating(1, 0)
        self.assertTrue(self.document.drop_floating())
        self.assertEqual(self.document.selection, (4, 2, 6, 4))
        self.assertEqual(self.document.flatten().getpixel((0, 0)), CLEAR)
        self.assertEqual(self.document.flatten().getpixel((5, 3)), GHOST)
        self.document.undo()
        self.assertEqual(self.document.flatten().getpixel((0, 0)), GHOST)
        self.assertEqual(self.document.flatten().getpixel((5, 3)), CLEAR)

    def test_floating_pixels_composite_over_the_layer(self):
        dot(self.document, 4, 4, BLUE)
        self.document.paste(Image.new("RGBA", (2, 2), CLEAR), 4, 4)
        self.document.drop_floating()
        self.assertEqual(self.document.flatten().getpixel((4, 4)), BLUE, "transparent pasted pixels keep the art below")

    def test_cancel_floating_restores_everything(self):
        self.document.set_selection((0, 0, 2, 2))
        self.document.lift_selection()
        self.document.move_floating(2, 2)
        self.document.cancel_floating()
        self.assertFalse(self.document.floating)
        self.assertEqual(self.document.flatten().getpixel((0, 0)), GHOST)
        self.assertEqual(self.document.selection, (0, 0, 2, 2))
        self.assertFalse(self.document.can_redo)

    def test_paste_clips_at_the_canvas_edge_and_flips(self):
        pasted = Image.new("RGBA", (3, 1), CLEAR)
        pasted.putpixel((0, 0), RED)
        self.document.paste(pasted, 4, 5)
        self.document.flip_floating(horizontal=True)
        self.document.drop_floating()
        self.assertEqual(self.document.flatten().getpixel((5, 5)), CLEAR)
        self.document.undo()
        self.document.paste(pasted, -2, 5)
        self.document.drop_floating()
        self.assertEqual(self.document.flatten().getpixel((0, 5)), CLEAR)

    def test_undo_while_floating_cancels_the_float(self):
        self.document.set_selection((0, 0, 2, 2))
        self.document.lift_selection()
        self.document.move_floating(3, 3)
        self.assertTrue(self.document.undo())
        self.assertEqual(self.document.flatten().getpixel((0, 0)), GHOST)
        self.assertTrue(self.document.can_undo, "the earlier fill is still undoable")

    def test_copy_and_delete_selection(self):
        self.document.set_selection((0, 0, 2, 1))
        copied = self.document.copy_selection()
        self.assertEqual(copied.size, (2, 1))
        self.assertEqual(copied.getpixel((0, 0)), GHOST)
        self.assertTrue(self.document.delete_selection())
        self.assertEqual(self.document.flatten().getpixel((0, 0)), CLEAR)
        self.assertEqual(self.document.flatten().getpixel((0, 1)), GHOST)
        self.document.clear_selection()
        self.assertIsNone(self.document.copy_selection())

    def test_structural_changes_drop_a_floating_selection_first(self):
        self.document.set_selection((0, 0, 2, 2))
        self.document.lift_selection()
        self.document.move_floating(4, 4)
        self.document.add_layer()
        self.assertFalse(self.document.floating)
        self.assertEqual(self.document.flatten().getpixel((5, 5)), GHOST)


class FrameToolTests(unittest.TestCase):
    def setUp(self):
        self.document = PixelDocument.blank(8, 4, frame=(4, 4))
        dot(self.document, 0, 1, RED)
        self.document.add_layer("Hair")
        dot(self.document, 1, 0, BLUE)

    def test_flip_region_on_active_layer_only(self):
        self.document.flip_region(self.document.frame_box(0), horizontal=True)
        self.assertEqual(self.document.layers[1].image.getpixel((2, 0)), BLUE)
        self.assertEqual(self.document.layers[0].image.getpixel((0, 1)), RED)

    def test_flip_region_on_all_layers_is_one_step(self):
        self.document.flip_region(self.document.frame_box(0), horizontal=False, all_layers=True)
        self.assertEqual(self.document.layers[0].image.getpixel((0, 2)), RED)
        self.assertEqual(self.document.layers[1].image.getpixel((1, 3)), BLUE)
        self.document.undo()
        self.assertEqual(self.document.layers[0].image.getpixel((0, 1)), RED)

    def test_copy_frame_to_another_frame_replaces_its_pixels(self):
        dot(self.document, 6, 3, RED)
        clip = self.document.copy_region(self.document.frame_box(0), all_layers=True)
        self.assertTrue(self.document.paste_region(clip, *self.document.frame_box(1)[:2]))
        self.assertEqual(self.document.layers[0].image.getpixel((4, 1)), RED)
        self.assertEqual(self.document.layers[1].image.getpixel((5, 0)), BLUE)
        self.assertEqual(self.document.layers[1].image.getpixel((6, 3)), CLEAR, "the target frame is replaced")
        self.document.undo()
        self.assertEqual(self.document.layers[1].image.getpixel((6, 3)), RED)

    def test_clear_region_skips_locked_layers(self):
        self.document.update_layer(0, locked=True)
        self.assertTrue(self.document.clear_region(self.document.frame_box(0), all_layers=True))
        self.assertEqual(self.document.layers[0].image.getpixel((0, 1)), RED)
        self.assertEqual(self.document.layers[1].image.getpixel((1, 0)), CLEAR)


class ColorTests(unittest.TestCase):
    def test_sheet_colors_are_ordered_by_use_and_skip_transparency(self):
        document = PixelDocument.blank(4, 1)
        document.begin_edit("Fill")
        document.paint(rectangle_mask(0, 0, 2, 0, filled=True, bounds=(4, 1)), RED)
        document.paint(brush_mask([(3, 0)], 1, (4, 1)), BLUE)
        document.commit_edit()
        self.assertEqual(document.sheet_colors(), [(RED, 3), (BLUE, 1)])

    def test_replace_color_in_layer_all_layers_and_selection(self):
        document = PixelDocument.blank(4, 1)
        dot(document, 0, 0, RED)
        dot(document, 3, 0, RED)
        document.add_layer()
        dot(document, 1, 0, RED)
        self.assertEqual(document.replace_color(RED, BLUE), 1)
        self.assertEqual(document.layers[0].image.getpixel((0, 0)), RED)
        document.set_selection((0, 0, 2, 1))
        self.assertEqual(document.replace_color(RED, GHOST, all_layers=True), 1)
        self.assertEqual(document.layers[0].image.getpixel((3, 0)), RED)
        document.clear_selection()
        self.assertEqual(document.replace_color(RED, GHOST, all_layers=True), 1)
        document.undo()
        self.assertEqual(document.layers[0].image.getpixel((3, 0)), RED)
        self.assertEqual(document.replace_color(BLUE, BLUE), 0)

    def test_pixel_at_reads_the_flattened_sheet(self):
        document = PixelDocument.blank(2, 2)
        dot(document, 1, 1, GHOST)
        self.assertEqual(document.pixel_at(1, 1), GHOST)
        self.assertIsNone(document.pixel_at(5, 5))


if __name__ == "__main__":
    unittest.main()
