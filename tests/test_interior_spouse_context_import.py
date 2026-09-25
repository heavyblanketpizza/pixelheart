"""Portable, preflighted farmhouse context artwork for spouse previews."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.interior_furniture import (
    FurnitureValidationError, import_furniture_library, validate_spouse_context,
)


class SpouseContextImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        for name, color in (("background", (130, 90, 60, 255)), ("foreground", (0, 0, 0, 0))):
            with Image.new("RGBA", (144, 176), color) as image:
                image.save(self.bundle / f"{name}.png")
        with Image.new("RGBA", (16, 16), "red") as image:
            image.save(self.bundle / "chair.png")
        self.data = {
            "format": "pixelheart-interior-library", "version": 1,
            "definitions": [{
                "id": "(F)0", "name": "Chair", "kind": "chair",
                "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1,
                "texture": "TileSheets/furniture", "preview_asset": "chair.png",
                "frames": [{"rect": [0, 0, 16, 16], "rotation": 0, "duration_ms": 100}],
            }],
            "spouse_context": {"background_asset": "background.png", "foreground_asset": "foreground.png"},
        }

    def import_library(self, data=None):
        path = self.bundle / "library.json"
        path.write_text(json.dumps(self.data if data is None else data), encoding="utf-8")
        return import_furniture_library(path, self.project)

    def assert_no_assets(self):
        self.assertEqual(list(self.project.iterdir()), [])

    def test_optional_context_preserves_bytes_and_is_portable_without_source_library(self):
        originals = {key: (self.bundle / reference).read_bytes()
                     for key, reference in self.data["spouse_context"].items()}
        result = self.import_library()
        for key, original in originals.items():
            reference = result["spouse_context"][key]
            self.assertEqual(reference, f"world_assets/interiors/textures/{hashlib.sha256(original).hexdigest()}.png")
            self.assertEqual((self.project / reference).read_bytes(), original)
            (self.bundle / self.data["spouse_context"][key]).unlink()
            with Image.open(self.project / reference) as image:
                self.assertEqual(image.size, (144, 176))
                self.assertEqual(image.mode, "RGBA")
        legacy = deepcopy(self.data)
        del legacy["spouse_context"]
        self.assertNotIn("spouse_context", self.import_library(legacy))

    def test_metadata_detaches_valid_references_and_rejects_incomplete_or_unsafe_paths(self):
        original = self.data["spouse_context"]
        normalized = validate_spouse_context(original)
        normalized["background_asset"] = "another.png"
        self.assertEqual(original["background_asset"], "background.png")
        for value in (None, [], {}, {"background_asset": "background.png"},
                      {**original, "size": [144, 176]},
                      {**original, "foreground_asset": "../outside.png"},
                      {**original, "foreground_asset": "/outside.png"},
                      {**original, "foreground_asset": "C:\\outside.png"},
                      {**original, "foreground_asset": "bad\nname.png"}):
            with self.subTest(value=value), self.assertRaises(FurnitureValidationError):
                self.import_library({**self.data, "spouse_context": value})
            self.assert_no_assets()

    def test_invalid_second_image_fails_before_valid_furniture_and_background_are_copied(self):
        for mode, size in (("RGBA", (143, 176)), ("RGBA", (144, 175)), ("RGB", (144, 176))):
            with Image.new(mode, size) as image:
                image.save(self.bundle / "foreground.png")
            with self.subTest(mode=mode, size=size), self.assertRaises(FurnitureValidationError):
                self.import_library()
            self.assert_no_assets()
        (self.bundle / "foreground.png").write_text("not a PNG", encoding="utf-8")
        with self.assertRaises(FurnitureValidationError):
            self.import_library()
        self.assert_no_assets()

    def test_escaping_symlink_is_rejected_before_any_project_copy(self):
        outside = self.root / "outside.png"
        (self.bundle / "foreground.png").rename(outside)
        (self.bundle / "foreground.png").symlink_to(outside)
        with self.assertRaisesRegex(FurnitureValidationError, "symlink"):
            self.import_library()
        self.assert_no_assets()

    def test_context_counts_toward_texture_and_byte_budgets_before_copying(self):
        total_bytes = sum((self.bundle / name).stat().st_size
                          for name in ("chair.png", "background.png", "foreground.png"))
        for limit, value, message in (("MAX_IMPORT_TEXTURES", 2, "unique PNG"),
                                      ("MAX_IMPORT_BYTES", total_bytes - 1, "combined PNG")):
            with self.subTest(limit=limit), patch(f"pixelheart_core.interior_furniture.{limit}", value):
                with self.assertRaisesRegex(FurnitureValidationError, message):
                    self.import_library()
            self.assert_no_assets()

    def test_existing_context_corruption_prevents_new_furniture_asset_copy(self):
        payload = (self.bundle / "foreground.png").read_bytes()
        folder = self.project / "world_assets/interiors/textures"
        folder.mkdir(parents=True)
        corrupt = folder / (hashlib.sha256(payload).hexdigest() + ".png")
        corrupt.write_bytes(b"user changed texture")
        with self.assertRaisesRegex(FurnitureValidationError, "unexpected contents"):
            self.import_library()
        self.assertEqual(list(folder.iterdir()), [corrupt])
        self.assertEqual(corrupt.read_bytes(), b"user changed texture")


if __name__ == "__main__":
    unittest.main()
