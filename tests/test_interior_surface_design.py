"""Pattern assembly preserves authored tiles and makes finish edits reversible."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_surface_design import apply_surface, stage_surface_library
from pixelheart_core.interiors import InteriorError, import_atlas, new_interior
from pixelheart_core.world import asset_path


class InteriorSurfaceDesignTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def surface(self, identity="(WP)Blue", kind="wall", colors=None, filename="patterns.png"):
        width, height = (1, 3) if kind == "wall" else (2, 2)
        colors = colors or (["blue", "cyan", "navy"] if kind == "wall" else ["red", "green", "yellow", "purple"])
        with Image.new("RGBA", (width * 16, height * 16)) as image:
            for index, color in enumerate(colors):
                x, y = index % width * 16, index // width * 16
                image.paste(color, (x, y, x + 16, y + 16))
            image.save(self.root / filename)
        return {"id": identity, "name": identity[4:], "kind": kind,
                "texture": "Maps/walls_and_floors", "preview_asset": filename,
                "rect": [0, 0, width * 16, height * 16], "dependency": "Example.Patterns"}

    def staged(self):
        wall = self.surface()
        floor = self.surface("(FL)Check", "floor", filename="floor.png")
        return stage_surface_library(new_interior(), [wall, floor], self.root)

    def pixels(self, data, tile):
        with Image.open(asset_path(data["atlas"]["asset"], self.root)) as image:
            columns = data["atlas"]["columns"]
            return image.getpixel((tile % columns * 16, tile // columns * 16))

    def test_patterns_are_embedded_in_one_portable_atlas_with_row_major_tiles(self):
        original = new_interior()
        data = self.staged()
        self.assertEqual(data["atlas"]["columns"], 8)
        self.assertEqual(data["atlas"]["tile_count"], 8)
        self.assertEqual(data["surfaces"][0]["tiles"], [0, 1, 2])
        self.assertEqual(data["surfaces"][1]["tiles"], [3, 4, 5, 6])
        self.assertEqual(self.pixels(data, 4), (0, 128, 0, 255))
        self.assertEqual(data["surfaces"][0]["dependency"], "Example.Patterns")
        self.assertNotIn("preview_asset", data["surfaces"][0])
        self.assertEqual(original, new_interior())

    def test_existing_tiles_columns_style_and_animation_indices_are_preserved(self):
        with Image.new("RGBA", (16, 32), "orange") as image:
            image.paste("black", (0, 16, 16, 32))
            image.save(self.root / "old.png")
        original = new_interior()
        original["atlas"] = import_atlas(self.root / "old.png", self.root)
        original["style"]["floor"] = 1
        original["animations"] = [{"tile_id": 0, "frames": [{"tile_id": 1, "duration_ms": 100}]}]
        before = deepcopy(original)
        data = stage_surface_library(original, [self.surface()], self.root)
        self.assertEqual(original, before)
        self.assertEqual(data["atlas"]["columns"], 1)
        self.assertEqual(data["atlas"]["tile_count"], 5)
        self.assertEqual(data["surfaces"][0]["tiles"], [2, 3, 4])
        self.assertEqual(data["style"], original["style"])
        self.assertEqual(data["animations"], original["animations"])
        self.assertEqual(self.pixels(data, 0), (255, 165, 0, 255))
        self.assertEqual(self.pixels(data, 1), (0, 0, 0, 255))
        self.assertTrue(asset_path(original["atlas"]["asset"], self.root).is_file())

    def test_repeated_import_is_idempotent_and_tiles_deduplicate_across_patterns(self):
        source = self.surface(colors=["blue", "blue", "blue"])
        first = stage_surface_library(new_interior(), [source], self.root)
        self.assertEqual(first["surfaces"][0]["tiles"], [0, 0, 0])
        second = stage_surface_library(first, [source], self.root)
        self.assertEqual(first, second)
        duplicate = {**source, "id": "(WP)Other", "name": "Other"}
        third = stage_surface_library(first, [duplicate], self.root)
        self.assertEqual(third["atlas"], first["atlas"])
        self.assertEqual(third["surfaces"][1]["tiles"], [0, 0, 0])

    def test_refresh_appends_new_tiles_without_overwriting_previous_design_appearance(self):
        source = self.surface()
        first = apply_surface(stage_surface_library(new_interior(), [source], self.root), source["id"])
        source = self.surface(colors=["white", "cyan", "navy"])
        second = stage_surface_library(first, [source], self.root)
        self.assertEqual(len(second["surfaces"]), 1)
        self.assertEqual(second["surfaces"][0]["tiles"], [8, 1, 2])
        self.assertEqual(second["style"], first["style"])
        self.assertEqual(self.pixels(second, 0), (0, 0, 255, 255))
        self.assertEqual(self.pixels(second, 8), (255, 255, 255, 255))
        refreshed = apply_surface(second, source["id"])
        self.assertEqual(refreshed["style"]["wall_pattern"]["tiles"], [8, 1, 2])

    def test_wall_and_floor_changes_are_detached_and_keep_other_finish(self):
        original = self.staged()
        wall = apply_surface(original, "(WP)Blue")
        self.assertNotIn("wall_pattern", original["style"])
        self.assertEqual(wall["style"]["wall_pattern"], {"width": 1, "height": 3,
                         "tiles": [0, 1, 2], "surface_id": "(WP)Blue"})
        both = apply_surface(wall, "(FL)Check")
        self.assertEqual(both["style"]["wall_pattern"], wall["style"]["wall_pattern"])
        self.assertEqual(both["style"]["floor_pattern"]["tiles"], [3, 4, 5, 6])
        self.assertEqual(both["style"]["floor"], 3)

    def test_apply_to_all_clears_only_corresponding_room_overrides(self):
        data = apply_surface(self.staged(), "(WP)Blue", "main")
        data = apply_surface(data, "(FL)Check", "main")
        global_wall = apply_surface(data, "(WP)Blue")
        self.assertEqual(set(global_wall["room_styles"]["main"]), {"floor_pattern", "floor"})
        global_floor = apply_surface(global_wall, "(FL)Check")
        self.assertEqual(global_floor["room_styles"], {})
        self.assertIn("wall_pattern", data["room_styles"]["main"])

    def test_invalid_pattern_or_room_cannot_mutate_design(self):
        data = self.staged()
        before = deepcopy(data)
        for surface, room in (("missing", None), ("(WP)Blue", "missing")):
            with self.subTest(surface=surface, room=room), self.assertRaises(InteriorError):
                apply_surface(data, surface, room)
            self.assertEqual(data, before)

    def test_invalid_late_surface_writes_no_partial_atlas(self):
        wall = self.surface()
        broken = {**wall, "id": "(WP)Missing", "preview_asset": "missing.png"}
        before = set(self.root.rglob("*.png"))
        with self.assertRaises(InteriorError):
            stage_surface_library(new_interior(), [wall, broken], self.root)
        self.assertEqual(set(self.root.rglob("*.png")), before)

    def test_duplicate_ids_and_excessive_surface_count_rejected_before_writing(self):
        wall = self.surface()
        for values in ([wall, wall], [wall] * 2049):
            with self.subTest(count=len(values)), self.assertRaises(InteriorError):
                stage_surface_library(new_interior(), values, self.root)
        self.assertFalse((self.root / "world_assets").exists())

    def test_full_narrow_atlas_rejects_growth_without_overwriting_existing_assets(self):
        with Image.new("RGBA", (16, 4096), "orange") as image:
            image.save(self.root / "full.png")
        data = new_interior()
        data["atlas"] = import_atlas(self.root / "full.png", self.root)
        old_path = asset_path(data["atlas"]["asset"], self.root)
        old_payload = old_path.read_bytes()
        before = deepcopy(data)
        with self.assertRaisesRegex(InteriorError, "4096"):
            stage_surface_library(data, [self.surface()], self.root)
        self.assertEqual(data, before)
        self.assertEqual(old_path.read_bytes(), old_payload)

    def test_corrupt_atlas_dimensions_and_escaping_assets_rejected(self):
        surface = self.surface()
        data = self.staged()
        data["atlas"]["columns"] = 4
        with self.assertRaises(InteriorError):
            stage_surface_library(data, [surface], self.root)
        with self.assertRaises(InteriorError):
            stage_surface_library(new_interior(), [{**surface, "preview_asset": "../outside.png"}], self.root)

    def test_corrupt_cached_surface_rejected_before_application(self):
        data = self.staged()
        for changes in ({"tiles": [0, 1, 99999]}, {"width": True}, {"kind": "paint"}):
            bad = deepcopy(data)
            bad["surfaces"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(InteriorError):
                apply_surface(bad, "(WP)Blue")


if __name__ == "__main__":
    unittest.main()
