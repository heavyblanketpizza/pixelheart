"""Painted artwork versions in project records, storage and Save As."""

import copy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.pixel_document import PixelDocument
from pixelheart_core.pixel_layers import load_layers, save_layers
from pixelheart_core.pixel_sheets import encode_png
from pixelheart_core.projects import (
    ProjectError, copy_project, import_painted_artwork, load_project, new_project, resolve_artwork, save_project,
)


class PaintedArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-painted-")
        self.root = Path(self.temporary.name)
        self.file = self.root / "project" / "character.json"
        self.document = new_project()
        save_project(self.document, self.file)
        self.payload = encode_png(Image.new("RGBA", (128, 192), (200, 80, 120, 255)))

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_painted_is_content_addressed(self):
        reference = import_painted_artwork(self.payload, self.file, "portrait")
        self.assertTrue(reference.startswith("artwork/painted/portrait-"))
        self.assertTrue(reference.endswith(".png"))
        self.assertEqual((self.file.parent / reference).read_bytes(), self.payload)
        self.assertEqual(import_painted_artwork(self.payload, self.file, "portrait"), reference)
        with self.assertRaises(ProjectError):
            import_painted_artwork(self.payload, self.file, "hat")

    def test_painted_selection_resolves_and_round_trips(self):
        original = self.file.parent / "artwork" / "original.png"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"upload")
        painted = import_painted_artwork(self.payload, self.file, "portrait")
        record = {"original": "artwork/original.png", "prepared": None, "painted": painted, "selected": "painted"}
        self.document["artwork"]["portrait"] = record
        save_project(self.document, self.file)
        loaded = load_project(self.file)
        self.assertEqual(loaded["artwork"]["portrait"], record)
        self.assertEqual(resolve_artwork(loaded, self.file, "portrait").read_bytes(), self.payload)

    def test_a_sheet_drawn_from_scratch_needs_no_original(self):
        painted = import_painted_artwork(self.payload, self.file, "sprite")
        self.document["artwork"]["sprite"] = {"painted": painted, "selected": "painted"}
        save_project(self.document, self.file)
        self.assertEqual(resolve_artwork(load_project(self.file), self.file, "sprite").read_bytes(), self.payload)

    def test_invalid_painted_records_are_rejected(self):
        for record in ({"painted": None, "selected": "painted"},
                       {"original": "artwork/a.png", "selected": "painted"},
                       {"painted": "artwork/a.png", "selected": "original"},
                       {"painted": "../outside.png", "selected": "painted"},
                       {"selected": "painted"}):
            with self.subTest(record=record):
                self.document["artwork"]["portrait"] = record
                with self.assertRaises(ProjectError):
                    save_project(self.document, self.file)

    def test_save_as_copies_painted_versions_and_their_layers(self):
        layered = PixelDocument.from_image(Image.new("RGBA", (128, 192), (200, 80, 120, 255)), name="Base")
        layered.add_layer("Empty idea")
        save_layers(self.file.parent, layered, self.payload)
        painted = import_painted_artwork(self.payload, self.file, "portrait")
        self.document["artwork"]["variants"] = {"winter": {"portrait": {"painted": painted, "selected": "painted"}}}
        save_project(self.document, self.file)
        before = copy.deepcopy(self.document)
        destination = copy_project(self.document, self.file, self.root / "copy" / "character.json")
        self.assertEqual(self.document, before)
        copied = load_project(destination)
        self.assertEqual(resolve_artwork(copied, destination, "portrait", variant="winter").read_bytes(), self.payload)
        restored = load_layers(destination.parent, self.payload)
        self.assertEqual([layer.name for layer in restored.layers], ["Base", "Empty idea"])


if __name__ == "__main__":
    unittest.main()
