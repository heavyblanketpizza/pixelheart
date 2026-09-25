"""Complete native kitchen pieces and safe refreshes of older placements."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_architecture import (
    architecture_candidate, architecture_cells, stage_architecture_library,
)
from pixelheart_core.interior_architecture_rules import architecture_rule_issues
from pixelheart_core.interiors import InteriorError, new_interior


class NativeKitchenRecipeTests(unittest.TestCase):
    def test_sink_and_fridge_include_the_complete_native_top(self):
        path = Path(__file__).resolve().parents[1] / "runtime/Pixelheart.Interiors/ArchitectureRecipes.json"
        recipes = {piece["id"]: piece for piece in json.loads(path.read_text())["recipes"]}
        for identity, source_x, tiles in (
            ("stardew.sink", 38, (569, 601, 633)),
            ("stardew.refrigerator", 39, (570, 602, 634)),
        ):
            with self.subTest(piece=identity):
                piece = recipes[identity]
                self.assertEqual((piece["width"], piece["height"]), (1, 3))
                self.assertEqual(piece["placement"], "floor")
                self.assertEqual(piece["rules"], "wall_backed")
                self.assertEqual(piece["map"], "Maps/SeedShop")
                self.assertEqual(piece["texture"], "Maps/townInterior")
                self.assertEqual(
                    sorted((cell["layer"], cell["x"], cell["y"], cell["source_x"],
                            cell["source_y"], cell["tile"]) for cell in piece["cells"]),
                    sorted((layer, 0, y, source_x, y + 2, tile)
                           for y, (layer, tile) in enumerate(zip(("Front", "Front", "Buildings"), tiles))),
                )

    def test_incomplete_standalone_cabinet_is_not_exported(self):
        path = Path(__file__).resolve().parents[1] / "runtime/Pixelheart.Interiors/ArchitectureRecipes.json"
        recipes = json.loads(path.read_text())["recipes"]
        self.assertNotIn("stardew.wall-cabinet", {piece["id"] for piece in recipes})


class KitchenLibraryRefreshTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with Image.new("RGBA", (64, 16)) as atlas:
            for index, color in enumerate(((80, 90, 100, 255), (40, 150, 200, 255),
                                           (160, 50, 20, 255), (200, 180, 40, 255))):
                atlas.paste(color, (index * 16, 0, (index + 1) * 16, 16))
            atlas.save(self.root / "source.png")

    def definition(self, identity="stardew.sink", *, complete=False, upper_tile=1):
        height = 3 if complete else 2
        return {
            "id": identity, "name": "Native kitchen fixture", "category": "Kitchen",
            "placement": "floor", "rules": "wall_backed", "width": 1, "height": height,
            "layers": {"Back": [None] * height,
                       "Buildings": [None] * (height - 1) + [2],
                       "Front": ([3] if complete else []) + [upper_tile, None]},
            "preview_asset": "source.png", "columns": 4, "tile_count": 4,
        }

    def placed(self, identity="stardew.sink", *, upper_tile=1):
        data = stage_architecture_library(new_interior(),
                                          [self.definition(identity, upper_tile=upper_tile)], self.root)
        return architecture_candidate(data, identity, 4, 4)

    def cabinet(self, *, tile=3):
        return {
            **self.definition("stardew.wall-cabinet"), "name": "Legacy wall cabinet",
            "placement": "wall", "rules": "wall_art", "height": 1,
            "layers": {"Back": [None], "Buildings": [None], "Front": [tile]},
        }

    def test_complete_refresh_preserves_floor_base_collision_and_is_idempotent(self):
        for identity in ("stardew.sink", "stardew.refrigerator"):
            with self.subTest(piece=identity):
                original = self.placed(identity)
                before = deepcopy(original)
                incoming = self.definition(identity, complete=True)
                refreshed = stage_architecture_library(original, [incoming], self.root)
                old_item, item = original["architecture"][0], refreshed["architecture"][0]
                self.assertEqual((item["x"], item["y"]), (4, 3))
                self.assertEqual((item["id"], item["room_id"]), (old_item["id"], old_item["room_id"]))
                self.assertEqual(architecture_cells(refreshed), architecture_cells(original))
                self.assertEqual(architecture_cells(refreshed), {(4, 5)})
                self.assertEqual(architecture_rule_issues(refreshed), [])
                self.assertEqual(stage_architecture_library(refreshed, [incoming], self.root), refreshed)
                self.assertEqual(original, before)

    def test_refresh_rejects_new_cabinet_overlap_without_changing_project_or_atlas(self):
        original = self.placed()
        cupboard = {
            **self.definition("stardew.dish-cupboard"), "name": "Dish cupboard",
            "placement": "wall", "rules": "wall_art", "width": 2,
            "layers": {"Back": [None] * 4, "Buildings": [None] * 4, "Front": [0] * 4},
        }
        original = stage_architecture_library(original, [cupboard], self.root)
        original = architecture_candidate(original, "stardew.dish-cupboard", 3, 2)
        before = deepcopy(original)
        files_before = set(self.root.rglob("*.png"))
        with self.assertRaisesRegex(InteriorError, "overlap"):
            stage_architecture_library(original, [self.definition(complete=True)], self.root)
        self.assertEqual(original, before)
        self.assertEqual(set(self.root.rglob("*.png")), files_before)

    def test_refresh_does_not_move_a_nonmatching_custom_crop(self):
        original = self.placed(upper_tile=0)
        before = deepcopy(original)
        refreshed = stage_architecture_library(original, [self.definition(complete=True)], self.root)
        self.assertEqual(refreshed["architecture"], original["architecture"])
        self.assertEqual(original, before)

    def test_complete_sink_refresh_prunes_its_unused_legacy_cabinet_fragment(self):
        original = stage_architecture_library(self.placed(), [self.cabinet()], self.root)
        before = deepcopy(original)
        refreshed = stage_architecture_library(original, [self.definition(complete=True)], self.root)
        self.assertNotIn("stardew.wall-cabinet", {piece["id"] for piece in refreshed["architecture_catalog"]})
        self.assertEqual(original, before)
        self.assertEqual(architecture_rule_issues(refreshed), [])

    def test_complete_sink_refresh_preserves_a_placed_legacy_cabinet(self):
        original = stage_architecture_library(self.placed(), [self.cabinet()], self.root)
        original = architecture_candidate(original, "stardew.wall-cabinet", 8, 3)
        cabinet = original["architecture"][-1]
        definition = next(piece for piece in original["architecture_catalog"] if piece["id"] == cabinet["piece_id"])
        refreshed = stage_architecture_library(original, [self.definition(complete=True)], self.root)
        self.assertIn(cabinet, refreshed["architecture"])
        self.assertIn(definition, refreshed["architecture_catalog"])
        self.assertEqual(architecture_rule_issues(refreshed), [])

    def test_complete_sink_refresh_preserves_a_nonmatching_custom_cabinet(self):
        original = stage_architecture_library(self.placed(), [self.cabinet(tile=0)], self.root)
        definition = next(piece for piece in original["architecture_catalog"] if piece["id"] == "stardew.wall-cabinet")
        refreshed = stage_architecture_library(original, [self.definition(complete=True)], self.root)
        self.assertIn(definition, refreshed["architecture_catalog"])

    def test_legacy_cabinet_is_retained_until_a_complete_sink_is_imported(self):
        original = stage_architecture_library(self.placed(), [self.cabinet()], self.root)
        definition = next(piece for piece in original["architecture_catalog"] if piece["id"] == "stardew.wall-cabinet")
        for incoming in (self.definition(), self.definition("stardew.refrigerator", complete=True)):
            with self.subTest(piece=incoming["id"], height=incoming["height"]):
                refreshed = stage_architecture_library(original, [incoming], self.root)
                self.assertIn(definition, refreshed["architecture_catalog"])


if __name__ == "__main__":
    unittest.main()
