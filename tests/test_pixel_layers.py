"""OpenRaster working files and the project's layer store."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.pixel_document import PixelDocument
from pixelheart_core.pixel_layers import (
    LayerFileError, blank_painting, copy_layer_files, layer_reference, load_layers, open_painting, read_ora,
    save_layers, write_ora,
)
from pixelheart_core.pixel_raster import brush_mask
from pixelheart_core.pixel_sheets import encode_png

RED = (220, 40, 40, 255)
GHOST = (10, 200, 90, 128)


def sample_document():
    document = PixelDocument.blank(8, 4, frame=(4, 4), name="Outline")
    document.begin_edit("Pencil")
    document.paint(brush_mask([(1, 1)], 1, (8, 4)), RED)
    document.commit_edit()
    document.add_layer("Shading")
    document.begin_edit("Pencil")
    document.paint(brush_mask([(5, 2)], 1, (8, 4)), GHOST)
    document.commit_edit()
    document.update_layer(1, opacity=60)
    document.add_layer("Hidden idea")
    document.update_layer(2, visible=False)
    document.add_reference(Image.new("RGBA", (8, 4), (0, 0, 255, 255)), "Villager")
    document.update_layer(0, locked=True)
    return document


def zip_bytes(entries, *, mimetype=b"image/openraster"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if mimetype is not None:
            archive.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def png(size, color=RED):
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class OpenRasterTests(unittest.TestCase):
    def test_round_trip_keeps_layers_properties_and_pixels(self):
        document = sample_document()
        restored = read_ora(write_ora(document))
        self.assertEqual((restored.width, restored.height, restored.frame_size), (8, 4, (4, 4)))
        self.assertEqual([layer.name for layer in restored.layers], ["Outline", "Shading", "Hidden idea", "Villager"])
        self.assertEqual([layer.visible for layer in restored.layers], [True, True, False, True])
        self.assertEqual([layer.locked for layer in restored.layers], [True, False, False, True])
        self.assertEqual([layer.opacity for layer in restored.layers], [100, 60, 100, 40])
        self.assertEqual([layer.reference for layer in restored.layers], [False, False, False, True])
        for original, copy in zip(document.layers, restored.layers):
            self.assertEqual(original.image.tobytes(), copy.image.tobytes())
        self.assertEqual(restored.flatten().tobytes(), document.flatten().tobytes())
        self.assertFalse(restored.modified)

    def test_archive_follows_openraster_layout(self):
        payload = write_ora(sample_document())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            first = archive.infolist()[0]
            self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
            self.assertEqual(archive.read("mimetype"), b"image/openraster")
            self.assertIn(b'name="Villager"', archive.read("stack.xml").split(b"<layer")[1],
                          "the first stack entry is the top layer")
            with Image.open(io.BytesIO(archive.read("mergedimage.png"))) as merged:
                self.assertEqual(merged.convert("RGBA").tobytes(), sample_document().flatten().tobytes())
            with Image.open(io.BytesIO(archive.read("Thumbnails/thumbnail.png"))) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), 256)

    def test_writing_is_deterministic(self):
        self.assertEqual(write_ora(sample_document()), write_ora(sample_document()))

    def test_frame_can_be_supplied_by_the_sheet(self):
        self.assertEqual(read_ora(write_ora(sample_document()), frame=(2, 2)).frame_size, (2, 2))

    def test_offset_layers_from_other_editors_are_placed_and_clipped(self):
        stack = (b'<image w="4" h="4"><stack>'
                 b'<layer name="Patch" src="data/a.png" x="3" y="2" opacity="0.5" visibility="hidden"/>'
                 b'</stack></image>')
        document = read_ora(zip_bytes({"stack.xml": stack, "data/a.png": png((3, 3))}))
        layer = document.layers[0]
        self.assertEqual((layer.opacity, layer.visible), (50, False))
        self.assertEqual(layer.image.getbbox(), (3, 2, 4, 4))

    def test_hostile_or_unsupported_archives_are_refused(self):
        good_layer = b'<layer name="A" src="data/a.png"/>'
        cases = {
            "not a zip": b"hello",
            "no mimetype": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack/></image>'}, mimetype=None),
            "wrong mimetype": zip_bytes({"stack.xml": b"<image/>"}, mimetype=b"image/png"),
            "entity": zip_bytes({"stack.xml": b'<!DOCTYPE x [<!ENTITY a "b">]><image w="1" h="1"><stack/></image>'}),
            "nested": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack><stack/></stack></image>'}),
            "empty": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack/></image>'}),
            "missing layer": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack>' + good_layer + b"</stack></image>"}),
            "huge canvas": zip_bytes({"stack.xml": b'<image w="4096" h="4096"><stack>' + good_layer + b"</stack></image>",
                                      "data/a.png": png((1, 1))}),
            "too many layers": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack>' + good_layer * 33 + b"</stack></image>",
                                          "data/a.png": png((1, 1))}),
            "bad opacity": zip_bytes({"stack.xml": b'<image w="1" h="1"><stack><layer name="A" src="data/a.png" opacity="nan"/></stack></image>',
                                      "data/a.png": png((1, 1))}),
        }
        for name, payload in cases.items():
            with self.subTest(name), self.assertRaises(LayerFileError):
                read_ora(payload)


class LayerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-layers-")
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.document = sample_document()
        self.png = encode_png(self.document.flatten())

    def tearDown(self):
        self.temporary.cleanup()

    def test_reference_is_keyed_by_the_flattened_png(self):
        digest = hashlib.sha256(self.png).hexdigest()
        self.assertEqual(layer_reference(self.png), f"artwork/layers/{digest}.ora")

    def test_saved_layers_come_back_for_the_same_png(self):
        reference = save_layers(self.root, self.document, self.png)
        self.assertTrue((self.root / reference).is_file())
        restored = load_layers(self.root, self.png, frame=(4, 4))
        self.assertEqual(len(restored.layers), 4)
        self.assertEqual(restored.frame_size, (4, 4))

    def test_png_changed_elsewhere_has_no_layers(self):
        save_layers(self.root, self.document, self.png)
        edited = encode_png(Image.new("RGBA", (8, 4), RED))
        self.assertIsNone(load_layers(self.root, edited))

    def test_unreadable_or_mismatched_layer_files_are_ignored(self):
        path = self.root / layer_reference(self.png)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"broken")
        self.assertIsNone(load_layers(self.root, self.png))
        other = PixelDocument.from_image(Image.new("RGBA", (8, 4), RED))
        path.write_bytes(write_ora(other))
        self.assertIsNone(load_layers(self.root, self.png), "layers must flatten to exactly this PNG")

    def test_layer_folder_cannot_escape_the_project(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "artwork").mkdir()
        (self.root / "artwork" / "layers").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(LayerFileError):
            save_layers(self.root, self.document, self.png)
        self.assertEqual(list(outside.iterdir()), [])

    def test_save_as_copies_layers_for_pngs_in_the_copied_project(self):
        save_layers(self.root, self.document, self.png)
        orphan = encode_png(Image.new("RGBA", (8, 4), GHOST))
        save_layers(self.root, PixelDocument.from_image(Image.new("RGBA", (8, 4), GHOST)), orphan)
        destination = Path(self.temporary.name) / "copy"
        (destination / "artwork" / "painted").mkdir(parents=True)
        (destination / "artwork" / "painted" / "portrait.png").write_bytes(self.png)
        self.assertEqual(copy_layer_files(self.root, destination), 1)
        self.assertIsNotNone(load_layers(destination, self.png))
        self.assertIsNone(load_layers(destination, orphan))


class OpenPaintingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-open-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_png_without_saved_layers_opens_as_one_layer_with_sheet_frames(self):
        payload = encode_png(Image.new("RGBA", (64, 128), RED))
        document, restored = open_painting(payload, "sprite", project_root=self.root)
        self.assertFalse(restored)
        self.assertEqual(document.frame_size, (16, 32))
        self.assertEqual([layer.name for layer in document.layers], ["Sprite sheet"])

    def test_saved_layers_are_restored_with_sheet_frames(self):
        document = sample_document()
        payload = encode_png(document.flatten())
        save_layers(self.root, document, payload)
        reopened, restored = open_painting(payload, "tilesheet", project_root=self.root)
        self.assertTrue(restored)
        self.assertEqual(len(reopened.layers), 4)
        self.assertEqual(reopened.frame_size, (8, 4), "irregular tilesheet sizes are one frame")

    def test_blank_painting_uses_the_sheet_grid(self):
        document = blank_painting("portrait", 128, 192)
        self.assertEqual(document.frame_size, (64, 64))
        self.assertIsNone(document.flatten().getbbox())
        self.assertEqual(document.layers[0].name, "Layer 1")


if __name__ == "__main__":
    unittest.main()
