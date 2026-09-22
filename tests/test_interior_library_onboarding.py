"""A game-folder connection resolves complete, private decorating libraries."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import (
    FurnitureValidationError, discover_furniture_libraries,
    import_furniture_library, preview_surface, resolve_furniture_library,
    validate_surface,
)


class InteriorLibraryOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "private-project"
        self.project.mkdir()

    def library(self, directory, **values):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "library.json"
        path.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                    "definitions": [], **values}), encoding="utf-8")
        return path

    def companion(self, mods):
        directory = mods / "My renamed interiors companion"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(json.dumps({"UniqueID": "Pixelheart.Interiors"}), encoding="utf-8")
        return directory

    def patterns(self, directory):
        atlas = directory / "patterns.png"
        atlas.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGBA", (48, 48), (0, 0, 0, 0)) as image:
            image.paste((211, 21, 31, 255), (0, 0, 16, 16))
            image.paste((21, 211, 31, 255), (0, 16, 16, 32))
            image.paste((21, 31, 211, 255), (0, 32, 16, 48))
            image.paste((78, 89, 90, 255), (16, 0, 48, 32))
            image.save(atlas)
        return [
            {"id": "(WP)Example.Wall", "kind": "wall", "name": "Three-part wall",
             "texture": "Mods/Example/Walls", "preview_asset": "patterns.png", "rect": [0, 0, 16, 48]},
            {"id": "(FL)Example.Floor", "kind": "floor", "name": "Complete floor",
             "texture": "Mods/Example/Floors", "preview_asset": "patterns.png", "rect": [16, 0, 32, 32]},
        ]

    def test_complete_patterns_import_once_and_preserve_runtime_notes(self):
        patterns = self.patterns(self.root / "library")
        path = self.library(self.root / "library", surfaces=patterns, warnings=["One custom animation needs an in-game check."])
        result = import_furniture_library(path, self.project)
        wall, floor = result["surfaces"]
        self.assertEqual(wall["preview_asset"], floor["preview_asset"])
        self.assertEqual(len(list(self.project.rglob("*.png"))), 1)
        self.assertEqual(result["warnings"], ["One custom animation needs an in-game check."])
        with preview_surface(wall, self.project) as image:
            self.assertEqual(image.size, (16, 48))
            self.assertEqual(image.getpixel((0, 0)), (211, 21, 31, 255))
            self.assertEqual(image.getpixel((0, 16)), (21, 211, 31, 255))
            self.assertEqual(image.getpixel((0, 32)), (21, 31, 211, 255))
        with preview_surface(floor, self.project) as image:
            self.assertEqual(image.size, (32, 32))
            self.assertEqual(image.getpixel((31, 31)), (78, 89, 90, 255))

    def test_every_pattern_is_preflighted_before_any_asset_is_copied(self):
        patterns = self.patterns(self.root / "library")
        patterns[1]["rect"][0] = 32
        path = self.library(self.root / "library", surfaces=patterns)
        with self.assertRaisesRegex(FurnitureValidationError, "beyond"):
            import_furniture_library(path, self.project)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_pattern_schema_rejects_mismatched_kind_geometry_and_escaping_assets(self):
        base = self.patterns(self.root / "library")[0]
        cases = [{"kind": "ceiling"}, {"id": "(FL)0"}, {"rect": [0, 0, 16, 16]},
                 {"rect": [True, 0, 16, 48]}, {"preview_asset": "../patterns.png"},
                 {"texture": "/Mods/Bad"}, {"preview_asset": "patterns.jpg"}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(FurnitureValidationError):
                validate_surface({**base, **changes})

    def test_bad_or_duplicate_patterns_leave_project_untouched(self):
        patterns = self.patterns(self.root / "library")
        for surfaces in ([patterns[0], patterns[0]], "not a list"):
            path = self.library(self.root / "library", surfaces=surfaces)
            with self.subTest(surfaces=surfaces), self.assertRaises(FurnitureValidationError):
                import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_game_folder_finds_renamed_companion_and_selects_newest_ready_library(self):
        game = self.root / "Game"
        companion = self.companion(game / "Mods")
        cached = self.library(companion / "cache/library")
        older = self.library(companion / "exports/20260920-manual")
        (companion / "exports/20260922-incomplete").mkdir()
        os.utime(older, ns=(10, 10))
        os.utime(cached, ns=(20, 20))
        found = discover_furniture_libraries([game], include_standard_paths=False)
        self.assertEqual(found, [cached, older])
        self.assertEqual(resolve_furniture_library(game), cached)
        self.assertEqual(resolve_furniture_library(game / "Mods"), cached)
        self.assertEqual(resolve_furniture_library(companion), cached)
        self.assertEqual(resolve_furniture_library(cached.parent), cached)
        self.assertEqual(resolve_furniture_library(cached), cached)

    def test_mac_app_bundle_and_remembered_content_patcher_folder_resolve(self):
        app = self.root / "Stardew Valley.app"
        game = app / "Contents/MacOS"
        companion = self.companion(game / "Mods")
        library = self.library(companion / "cache/library")
        patch_export = game / "patch export"
        patch_export.mkdir()
        for selected in (app, self.root, patch_export):
            with self.subTest(selected=selected):
                self.assertEqual(resolve_furniture_library(selected), library)

    def test_standard_path_discovery_does_not_search_elsewhere_in_home(self):
        game = self.root / "Library/Application Support/Steam/steamapps/common/Stardew Valley"
        companion = self.companion(game / "Mods")
        expected = self.library(companion / "cache/library")
        self.library(self.root / "Documents/Unrelated project/exports/20260922")
        self.assertEqual(discover_furniture_libraries(home=self.root, platform="darwin"), [expected])

    def test_unrelated_mods_and_linked_files_are_not_read_as_libraries(self):
        game = self.root / "Game"
        other = game / "Mods/Unrelated"
        self.library(other / "cache/library")
        other.joinpath("manifest.json").write_text('{"UniqueID":"Other.Mod"}', encoding="utf-8")
        companion = self.companion(game / "Mods")
        outside = self.library(self.root / "outside")
        cache = companion / "cache/library"
        cache.mkdir(parents=True)
        (cache / "library.json").symlink_to(outside)
        self.assertEqual(discover_furniture_libraries([game], include_standard_paths=False), [])
        (cache / "library.json").unlink()
        (companion / "exports").symlink_to(self.root / "outside", target_is_directory=True)
        self.assertEqual(discover_furniture_libraries([game], include_standard_paths=False), [])

    def test_missing_library_explains_the_game_connection_without_a_console_command(self):
        with self.assertRaises(FurnitureValidationError) as error:
            resolve_furniture_library(self.root)
        message = str(error.exception)
        self.assertIn("load a save once", message)
        self.assertNotIn("pixelheart_export", message)
        self.assertNotIn("JSON", message)


if __name__ == "__main__":
    unittest.main()
