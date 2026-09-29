"""Frame geometry and limits for the sheets the painter edits."""

import io
import unittest

from PIL import Image

from pixelheart_core.pixel_sheets import (
    MAX_CANVAS_PIXELS, SheetError, encode_png, new_sheet_size, open_png, sheet_spec,
)


class SheetSpecTests(unittest.TestCase):
    def test_game_sized_portrait_and_sprite_frames(self):
        portrait = sheet_spec("portrait", 128, 192)
        self.assertEqual((portrait.frame_width, portrait.frame_height), (64, 64))
        self.assertEqual(portrait.columns, 2)
        sprite = sheet_spec("sprite", 64, 416)
        self.assertEqual((sprite.frame_width, sprite.frame_height), (16, 32))
        self.assertEqual(sprite.rows(416), 13)

    def test_larger_proportional_sheets_scale_their_frames(self):
        self.assertEqual(sheet_spec("portrait", 256, 384).frame_width, 128)
        sprite = sheet_spec("sprite", 128, 256)
        self.assertEqual((sprite.frame_width, sprite.frame_height), (32, 64))

    def test_tilesheets_use_sixteen_pixel_tiles(self):
        spec = sheet_spec("tilesheet", 256, 64)
        self.assertEqual((spec.frame_width, spec.frame_height), (16, 16))
        self.assertEqual(spec.columns, 16)

    def test_irregular_sheet_is_one_frame_without_rows(self):
        spec = sheet_spec("portrait", 100, 90)
        self.assertEqual((spec.frame_width, spec.frame_height), (100, 90))
        self.assertFalse(spec.can_add_row(90))
        self.assertFalse(spec.can_remove_row(90))

    def test_row_limits_keep_required_frames(self):
        portrait = sheet_spec("portrait", 128, 192)
        self.assertFalse(portrait.can_remove_row(192))
        self.assertTrue(portrait.can_remove_row(256))
        self.assertTrue(portrait.can_add_row(192))
        sprite = sheet_spec("sprite", 64, 128)
        self.assertFalse(sprite.can_remove_row(128))
        tiles = sheet_spec("tilesheet", 2048, 2048)
        self.assertFalse(tiles.can_add_row(2048))
        self.assertTrue(sheet_spec("tilesheet", 64, 32).can_remove_row(32))

    def test_frame_boxes_and_lookup(self):
        spec = sheet_spec("sprite", 64, 128)
        self.assertEqual(spec.frame_box(5, 128), (16, 32, 32, 64))
        self.assertEqual(spec.frame_at(20, 40, 128), 5)
        self.assertIsNone(spec.frame_at(64, 0, 128))
        self.assertEqual(spec.frame_count(128), 16)

    def test_unknown_kind_and_oversized_canvas_are_rejected(self):
        with self.assertRaises(SheetError):
            sheet_spec("hat", 64, 64)
        with self.assertRaises(SheetError):
            sheet_spec("tilesheet", 4096, 4096)
        self.assertEqual(MAX_CANVAS_PIXELS, 4_194_304)


class NewSheetTests(unittest.TestCase):
    def test_new_sheet_sizes_match_export_templates(self):
        self.assertEqual(new_sheet_size("portrait"), (128, 192))
        self.assertEqual(new_sheet_size("sprite"), (64, 128))
        self.assertEqual(new_sheet_size("sprite", romanceable=True), (64, 416))
        self.assertEqual(new_sheet_size("tilesheet", columns=8, rows=4), (128, 64))


class EncodeTests(unittest.TestCase):
    def test_encode_png_round_trips_rgba(self):
        image = Image.new("RGBA", (4, 4), (1, 2, 3, 4))
        payload = encode_png(image)
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)), (1, 2, 3, 4))

    def test_encode_png_enforces_byte_limit(self):
        with self.assertRaises(SheetError):
            encode_png(Image.effect_noise((64, 64), 100).convert("RGBA"), max_bytes=100)

    def test_open_png_decodes_to_rgba(self):
        buffer = io.BytesIO()
        Image.new("P", (3, 2)).save(buffer, format="PNG")
        with open_png(buffer.getvalue()) as image:
            self.assertEqual((image.mode, image.size), ("RGBA", (3, 2)))

    def test_open_png_rejects_other_formats_animation_and_garbage(self):
        gif = io.BytesIO()
        Image.new("RGBA", (2, 2)).save(gif, format="GIF")
        animated = io.BytesIO()
        Image.new("RGBA", (2, 2)).save(animated, format="PNG", save_all=True,
                                       append_images=[Image.new("RGBA", (2, 2), (9, 9, 9, 255))])
        for payload in (gif.getvalue(), animated.getvalue(), b"not an image"):
            with self.assertRaises(SheetError):
                open_png(payload)


if __name__ == "__main__":
    unittest.main()
