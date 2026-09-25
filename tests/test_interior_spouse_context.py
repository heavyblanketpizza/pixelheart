"""Spouse preview framing keeps the authored insert and west entrance clear."""
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_spouse_context import (
    SPOUSE_CONTEXT_ORIGIN, SPOUSE_CONTEXT_PIXEL_SIZE, SPOUSE_CONTEXT_SIZE,
    spouse_context_asset_refs, spouse_context_layers,
)
from pixelheart_core.world import WorldError


class SpouseContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def native_context(self, *, foreground_size=SPOUSE_CONTEXT_PIXEL_SIZE,
                       foreground_mode="RGBA"):
        with Image.new("RGBA", SPOUSE_CONTEXT_PIXEL_SIZE, (70, 50, 30, 170)) as image:
            image.save(self.root / "background.png")
        color = (20, 40, 60, 90) if foreground_mode == "RGBA" else (20, 40, 60)
        with Image.new(foreground_mode, foreground_size, color) as image:
            image.save(self.root / "foreground.png")
        return {"spouse_context": {"background_asset": "background.png",
                                   "foreground_asset": "foreground.png"}}

    def test_schematic_background_preserves_entire_editable_rectangle(self):
        self.assertEqual(SPOUSE_CONTEXT_SIZE, (9, 11))
        self.assertEqual(SPOUSE_CONTEXT_ORIGIN, (2, 1))
        background, foreground = spouse_context_layers({}, self.root)
        with background, foreground:
            self.assertEqual(background.size, (144, 176))
            self.assertEqual(background.mode, "RGBA")
            self.assertEqual(background.getchannel("A").crop((32, 16, 128, 160)).getbbox(), None)
            for point in ((40, 8), (136, 40), (24, 72)):
                self.assertEqual(background.getpixel(point)[3], 255)
            # No foreground partition seals the five-tile western opening.
            self.assertIsNone(foreground.getchannel("A").crop((16, 80, 32, 160)).getbbox())
            self.assertNotEqual(background.getpixel((24, 88)), background.getpixel((24, 72)))

    def test_schematic_trim_leaves_upper_half_of_last_floor_row_visible(self):
        background, foreground = spouse_context_layers({}, self.root)
        with background, foreground:
            self.assertIsNone(foreground.getchannel("A").crop((32, 16, 128, 152)).getbbox())
            self.assertEqual(foreground.getchannel("A").crop((32, 152, 128, 160)).getextrema(), (255, 255))
            self.assertEqual(foreground.getchannel("A").crop((0, 160, 144, 176)).getextrema(), (255, 255))
            room = Image.new("RGBA", (96, 144), "#336699")
            with room:
                background.alpha_composite(room, (32, 16))
                background.alpha_composite(foreground)
            self.assertEqual(background.getpixel((72, 147)), (51, 102, 153, 255))
            self.assertNotEqual(background.getpixel((72, 155)), (51, 102, 153, 255))

    def test_native_pair_loads_exact_pixels_and_optional_asset_references(self):
        data = self.native_context()
        self.assertEqual(list(spouse_context_asset_refs({})), [])
        self.assertEqual(list(spouse_context_asset_refs(data)), ["background.png", "foreground.png"])
        background, foreground = spouse_context_layers(data, self.root)
        with background, foreground:
            self.assertEqual(background.getpixel((40, 40)), (70, 50, 30, 170))
            self.assertEqual(foreground.getpixel((40, 40)), (20, 40, 60, 90))
            self.assertEqual(foreground.size, SPOUSE_CONTEXT_PIXEL_SIZE)

    def test_declared_missing_artwork_is_an_error(self):
        data = self.native_context()
        (self.root / "foreground.png").unlink()
        with self.assertRaises(WorldError):
            spouse_context_layers(data, self.root)

    def test_native_dimensions_and_rgba_are_required(self):
        for size, mode in (((144, 175), "RGBA"), ((144, 176), "RGB")):
            with self.subTest(size=size, mode=mode):
                data = self.native_context(foreground_size=size, foreground_mode=mode)
                with self.assertRaisesRegex(WorldError, "144.*176.*RGBA"):
                    spouse_context_layers(data, self.root)

    def test_native_context_cannot_read_symlink_outside_project(self):
        data = self.native_context()
        outside = Path(self.enterContext(tempfile.TemporaryDirectory())) / "outside.png"
        with Image.new("RGBA", SPOUSE_CONTEXT_PIXEL_SIZE) as image:
            image.save(outside)
        (self.root / "foreground.png").unlink()
        (self.root / "foreground.png").symlink_to(outside)
        with self.assertRaisesRegex(WorldError, "symlink"):
            spouse_context_layers(data, self.root)


if __name__ == "__main__":
    unittest.main()
