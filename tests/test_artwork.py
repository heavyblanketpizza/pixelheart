import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from pixelheart_core.artwork import (
    MAX_ARTWORK_BYTES,
    ArtworkValidationError,
    inspect_artwork,
    prepare_artwork,
)


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "original.png"
        self.destination = self.directory / "prepared.png"

    def make_png(self, size=(128, 192), color=(240, 110, 160, 255)):
        with Image.new("RGBA", size, color) as image:
            image.save(self.source)
        return self.source

    def prepare(self, kind="portrait", **options):
        return prepare_artwork(self.source, self.destination, kind, **options)

    def test_inspection_returns_file_bytes_and_dimensions_without_requiring_a_sheet(self):
        self.make_png((64, 64))
        original = self.source.read_bytes()
        self.assertEqual(inspect_artwork(str(self.source)), {
            "size": len(original), "width": 64, "height": 64, "format": "PNG",
        })
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_correctly_sized_artwork_preserves_exact_bytes_and_metadata(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Original artist")
        with Image.new("RGBA", (128, 256), (1, 2, 3, 0)) as image:
            image.save(self.source, pnginfo=info, compress_level=0)
        original = self.source.read_bytes()
        result = self.prepare(target_height=256)
        self.assertEqual(result, self.destination)
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)

    def test_uniform_downsize_preserves_every_frame_and_source(self):
        # Noninteger resize ratio is supported when frame boundaries are whole
        # source pixels: 65-pixel frames become 64-pixel frames.
        colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
                  (255, 255, 0, 255), (255, 0, 255, 255), (0, 255, 255, 255)]
        with Image.new("RGBA", (130, 195)) as image:
            for index, color in enumerate(colors):
                left, top = index % 2 * 65, index // 2 * 65
                image.paste(color, (left, top, left + 65, top + 65))
            image.save(self.source)
        original = self.source.read_bytes()
        self.prepare()
        self.assertEqual(self.source.read_bytes(), original)
        with Image.open(self.destination) as image:
            self.assertEqual(image.size, (128, 192))
            for index, color in enumerate(colors):
                left, top = index % 2 * 64, index // 2 * 64
                with image.crop((left, top, left + 64, top + 64)) as frame:
                    self.assertEqual(frame.getcolors(), [(64 * 64, color)])

    def test_simple_resize_preserves_palette_transparency_and_hard_edges(self):
        with Image.new("P", (256, 384), 0) as image:
            image.putpalette([0, 0, 0, 240, 50, 100] + [0] * 762)
            image.paste(1, (128, 0, 256, 128))
            image.save(self.source, transparency=0)
        self.prepare()
        with Image.open(self.destination) as image:
            self.assertEqual(image.mode, "P")
            self.assertEqual(image.info["transparency"], 0)
            self.assertEqual(image.convert("RGBA").getpixel((63, 0)), (0, 0, 0, 0))
            self.assertEqual(image.convert("RGBA").getpixel((64, 0)), (240, 50, 100, 255))

    def test_pixelation_averages_a_grid_and_preserves_frame_boundaries(self):
        with Image.new("RGBA", (64, 128), (0, 0, 0, 0)) as image:
            for frame in range(16):
                left, top = frame % 4 * 16, frame // 4 * 32
                for y in range(top, top + 32):
                    for x in range(left, left + 16):
                        image.putpixel((x, y), (frame * 12, 200 if (x + y) % 2 else 0, 40, 255))
            image.save(self.source)
        original = self.source.read_bytes()
        self.prepare("sprite", pixelate=8)
        self.assertEqual(self.source.read_bytes(), original)
        with Image.open(self.destination) as image:
            for frame in range(16):
                left, top = frame % 4 * 16, frame // 4 * 32
                with image.crop((left, top, left + 16, top + 32)) as tile:
                    self.assertEqual(tile.getcolors(), [(16 * 32, (frame * 12, 100, 40, 255))])

    def test_pixelation_keeps_fully_transparent_frames_transparent(self):
        self.make_png(color=(100, 0, 200, 0))
        self.prepare(pixelate=4)
        with Image.open(self.destination) as image:
            self.assertEqual(image.getchannel("A").getextrema(), (0, 0))

    def test_variable_rows_default_to_proportional_height(self):
        for kind, source, expected in (
            ("portrait", (256, 640), (128, 320)),
            ("sprite", (128, 896), (64, 448)),
        ):
            with self.subTest(kind=kind):
                self.make_png(source)
                self.prepare(kind)
                with Image.open(self.destination) as image:
                    self.assertEqual(image.size, expected)

    def test_romance_requires_additional_frames(self):
        self.make_png((64, 128))
        with self.assertRaisesRegex(ArtworkValidationError, "416"):
            self.prepare("sprite", romanceable=True)
        self.assertFalse(self.destination.exists())
        self.make_png((128, 832))
        self.prepare("sprite", romanceable=True, target_height=416)
        with Image.open(self.destination) as image:
            self.assertEqual(image.size, (64, 416))

    def test_target_cannot_stretch_or_discard_existing_rows(self):
        self.make_png((256, 512))
        with self.assertRaisesRegex(ArtworkValidationError, "scales proportionally to 128×256"):
            self.prepare(target_height=192)
        self.assertFalse(self.destination.exists())

    def test_invalid_layouts_and_upscaling_are_rejected(self):
        cases = [
            ("portrait", (64, 96), "Upscaling"),
            ("portrait", (128, 128), "at least 192"),
            ("portrait", (256, 383), "aspect ratio"),
            ("portrait", (129, 258), "whole-pixel frame boundaries"),
            ("sprite", (64, 144), "rows of 32"),
            ("sprite", (65, 130), "whole-pixel frame boundaries"),
        ]
        for kind, size, message in cases:
            with self.subTest(kind=kind, size=size):
                self.make_png(size)
                with self.assertRaisesRegex(ArtworkValidationError, message):
                    self.prepare(kind)
                self.assertFalse(self.destination.exists())

    def test_invalid_options_are_actionable(self):
        self.make_png()
        for pixelate in (0, -1, 3, 16, True, 2.0, "2", None):
            with self.subTest(pixelate=pixelate):
                with self.assertRaisesRegex(ArtworkValidationError, "pixelation factor"):
                    self.prepare(pixelate=pixelate)
        for target_height in (0, 128, 193, True, 192.0, "192"):
            with self.subTest(target_height=target_height):
                with self.assertRaisesRegex(ArtworkValidationError, "target"):
                    self.prepare(target_height=target_height)
        with self.assertRaisesRegex(ArtworkValidationError, "portrait or sprite"):
            self.prepare("avatar")
        with self.assertRaisesRegex(ArtworkValidationError, "true or false"):
            self.prepare(romanceable="false")

    def test_original_path_symlink_and_hardlink_cannot_be_destinations(self):
        self.make_png()
        original = self.source.read_bytes()
        alias = self.directory / "alias.png"
        alias.symlink_to(self.source)
        hardlink = self.directory / "hardlink.png"
        os.link(self.source, hardlink)
        for destination in (self.source, alias, hardlink):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(ArtworkValidationError, "separate file"):
                    prepare_artwork(self.source, destination, "portrait", pixelate=2)
                self.assertEqual(self.source.read_bytes(), original)

    def test_non_png_and_corrupt_files_are_rejected(self):
        with Image.new("RGB", (128, 192)) as image:
            image.save(self.source, format="JPEG")
        with self.assertRaisesRegex(ArtworkValidationError, "Only PNG"):
            inspect_artwork(self.source)
        for payload in (b"not an image", b"\x89PNG\r\n\x1a\n"):
            self.source.write_bytes(payload)
            with self.assertRaisesRegex(ArtworkValidationError, "cannot be read"):
                inspect_artwork(self.source)
        self.make_png()
        self.source.write_bytes(self.source.read_bytes()[:-20])
        with self.assertRaises(ArtworkValidationError):
            self.prepare()
        self.assertFalse(self.destination.exists())

    def test_container_verification_is_followed_by_actual_pixel_decoding(self):
        self.make_png()
        with patch("PIL.PngImagePlugin.PngImageFile.load", side_effect=OSError("bad pixel data")):
            with self.assertRaisesRegex(ArtworkValidationError, "bad pixel data"):
                inspect_artwork(self.source)

    def test_animated_png_is_rejected(self):
        with Image.new("RGBA", (128, 192), "red") as first, Image.new("RGBA", (128, 192), "blue") as second:
            first.save(self.source, save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaisesRegex(ArtworkValidationError, "static PNG"):
            inspect_artwork(self.source)

    def test_byte_and_pixel_limits_are_enforced_before_processing(self):
        with self.source.open("wb") as file:
            file.seek(MAX_ARTWORK_BYTES)
            file.write(b"x")
        with self.assertRaisesRegex(ArtworkValidationError, "5 MB"):
            inspect_artwork(self.source)
        with Image.new("1", (2049, 2048)) as image:
            image.save(self.source)
        with self.assertRaisesRegex(ArtworkValidationError, "4,194,304-pixel"):
            inspect_artwork(self.source)

    def test_pillow_decompression_warning_is_rejected(self):
        self.make_png()
        with patch.object(Image, "MAX_IMAGE_PIXELS", 20_000):
            with self.assertRaisesRegex(ArtworkValidationError, "safe image size"):
                inspect_artwork(self.source)

    def test_missing_nonfile_and_invalid_paths_have_actionable_errors(self):
        for path in (self.source, self.directory, None, "image\x00.png"):
            with self.subTest(path=path):
                with self.assertRaises(ArtworkValidationError):
                    inspect_artwork(path)
        self.make_png()
        with self.assertRaisesRegex(ArtworkValidationError, "destination file path"):
            prepare_artwork(self.source, "image\x00.png", "portrait")

    def test_failed_validation_preserves_existing_prepared_copy(self):
        self.make_png((128, 128))
        self.destination.write_bytes(b"previous prepared artwork")
        with self.assertRaises(ArtworkValidationError):
            self.prepare()
        self.assertEqual(self.destination.read_bytes(), b"previous prepared artwork")

    def test_failed_replacement_preserves_previous_copy_and_cleans_temporary_file(self):
        self.make_png()
        self.destination.write_bytes(b"previous prepared artwork")
        with patch("pixelheart_core.artwork.os.replace", side_effect=PermissionError("read-only destination")):
            with self.assertRaisesRegex(ArtworkValidationError, "writable destination"):
                self.prepare()
        self.assertEqual(self.destination.read_bytes(), b"previous prepared artwork")
        self.assertEqual(list(self.directory.glob(".pixelheart-*")), [])

    def test_output_parent_directories_are_created(self):
        self.make_png()
        destination = self.directory / "prepared" / "portraits.png"
        self.assertEqual(prepare_artwork(self.source, destination, "portrait"), destination)
        self.assertEqual(destination.read_bytes(), self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
