"""Tabletop decorations remain portable native furniture, outside map tiles."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import (
    FurnitureValidationError, attach_texture, held_item_preview_offset, validate_definition,
)
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, compile_interior, import_atlas,
    map_layers, new_interior, normalize_interior, reachable_tiles, render_interior,
)


class InteriorTabletopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def design(self):
        data = new_interior()
        data["catalog"] = [
            validate_definition({"id": "Example.Table", "kind": "table", "footprint": [2, 2],
                                 "sprite_size": [2, 3], "dependency": "Example.Tables"}),
            validate_definition({"id": "Example.Goblet", "kind": "decor", "footprint": [1, 1],
                                 "sprite_size": [1, 1], "dependency": "Example.Decor",
                                 "mod_data": {"Example/Finish": "gold"}}),
            validate_definition({"id": "Example.Unused", "kind": "decor", "footprint": [1, 1],
                                 "dependency": "Example.UnusedProvider"}),
        ]
        data["furniture"] = [{"id": "table", "item_id": "(F)Example.Table", "x": 6, "y": 6,
                              "rotation": 0, "mod_data": {},
                              "held_item": {"item_id": "(F)Example.Goblet", "mod_data": {"Example/Finish": "worn"}}}]
        return data

    def test_tabletop_normalizes_portable_identity_and_detaches_metadata(self):
        data = self.design()
        data["furniture"][0]["held_item"]["item_id"] = "Example.Goblet"
        result = normalize_interior(data)
        held = result["furniture"][0]["held_item"]
        self.assertEqual(held["item_id"], "(F)Example.Goblet")
        held["mod_data"]["Example/Finish"] = "changed"
        self.assertEqual(data["furniture"][0]["held_item"]["mod_data"], {"Example/Finish": "worn"})

    def test_observed_tabletop_offsets_are_explicit_bounded_and_detached(self):
        parent = self.design()["catalog"][0]
        with self.assertRaisesRegex(FurnitureValidationError, "observed"):
            held_item_preview_offset(parent)
        parent["rotations"] = 2
        parent["held_item_offsets"] = {"0": [8, -4], "1": [0, -12]}
        result = validate_definition(parent)
        self.assertEqual(held_item_preview_offset(result), (8, -4))
        self.assertEqual(held_item_preview_offset(result, 1), (0, -12))
        self.assertEqual(held_item_preview_offset(result, sprite_size=(16, 32)), (8, -20))
        self.assertEqual(held_item_preview_offset(result, sprite_size=(32, 16)), (0, -4))
        result["held_item_offsets"]["0"][0] = 0
        self.assertEqual(parent["held_item_offsets"]["0"], [8, -4])
        for offsets in ([], {0: [8, -4]}, {"2": [0, 0]}, {"0": [0]},
                        {"0": [True, 0]}, {"0": [0, 2049]}):
            with self.subTest(offsets=offsets), self.assertRaises(FurnitureValidationError):
                validate_definition(dict(parent, held_item_offsets=offsets))

    def test_render_matches_native_tabletop_baseline_for_tall_decor(self):
        data = self.design()
        texture = self.root / "furniture.png"
        with Image.new("RGBA", (48, 48), "blue") as image:
            image.paste("red", (32, 0, 48, 32))
            image.save(texture)
        parent, child = data["catalog"][:2]
        parent.update(held_item_offsets={"0": [8, -4]},
                      frames=[{"rotation": 0, "rect": [0, 0, 32, 48], "duration_ms": 100}])
        child.update(sprite_size=[1, 2],
                     frames=[{"rotation": 0, "rect": [32, 0, 16, 32], "duration_ms": 100,
                              "offset": [4, 5]}])
        data["catalog"][0] = attach_texture(parent, texture, self.root)
        data["catalog"][1] = attach_texture(child, texture, self.root)
        with render_interior(data, self.root) as image:
            self.assertEqual(image.getpixel((104, 76)), (255, 0, 0, 255))
            self.assertEqual(image.getpixel((119, 107)), (255, 0, 0, 255))
            self.assertEqual(image.getpixel((120, 100)), (0, 0, 255, 255))
            self.assertEqual(image.getpixel((104, 108)), (0, 0, 255, 255))
        # The child is a native held sprite; its floor-placement frame offset
        # does not move it away from the observed tabletop baseline.
        data["catalog"][0].pop("held_item_offsets")
        with render_interior(data, self.root) as image:
            self.assertEqual(image.getpixel((104, 96)), (0, 0, 255, 255))

    def test_missing_wrong_type_recursive_or_unsupported_held_items_fail_without_mutation(self):
        invalid = [None, [], {}, {"item_id": "Missing"}, {"item_id": "(O)Goblet"},
                   {"item_id": "(F)Example.Goblet", "held_item": {"item_id": "(F)Example.Goblet"}},
                   {"item_id": "(F)Example.Goblet", "rotation": 1},
                   {"item_id": "(F)Example.Goblet", "mod_data": {"nested": {}}},
                   {"item_id": "(F)Example.Goblet", "mod_data": {"bad\nkey": "value"}},
                   {"item_id": "(F)Example.Goblet", "mod_data": {"key": "bad\nvalue"}},
                   {"item_id": "(F)Example.Goblet", "mod_data": {"": "empty key"}}]
        for held in invalid:
            data = self.design()
            data["furniture"][0]["held_item"] = held
            before = deepcopy(data)
            with self.subTest(held=held), self.assertRaises(InteriorError):
                normalize_interior(data)
            self.assertEqual(data, before)

    def test_parent_child_kinds_footprints_and_indoor_placement_are_checked(self):
        cases = [(0, {"kind": "chair"}), (1, {"kind": "lamp"}),
                 (1, {"footprint": [2, 1]}), (1, {"footprint": None}),
                 (1, {"rotation_footprints": {"0": [1, 2]}}),
                 (1, {"placement": "outdoors"})]
        for index, changes in cases:
            data = self.design()
            data["catalog"][index].update(changes)
            with self.subTest(changes=changes), self.assertRaises(InteriorError):
                normalize_interior(data)
        data = self.design()
        data["catalog"][0]["kind"] = "long table"
        self.assertEqual(normalize_interior(data)["furniture"][0]["held_item"]["item_id"], "(F)Example.Goblet")

    def test_held_decor_uses_parent_collision_only_and_can_repeat_on_distinct_tables(self):
        data = self.design()
        plain = deepcopy(data)
        plain["furniture"][0].pop("held_item")
        self.assertEqual(map_layers(data), map_layers(plain))
        self.assertEqual(reachable_tiles(data), reachable_tiles(plain))
        data["furniture"].append(dict(deepcopy(data["furniture"][0]), id="other_table", x=9))
        self.assertEqual(len(normalize_interior(data)["furniture"]), 2)
        data["furniture"][1]["id"] = "table"
        with self.assertRaisesRegex(InteriorError, "unique"):
            normalize_interior(data)
        data = self.design()
        data["catalog"].append(deepcopy(data["catalog"][1]))
        with self.assertRaisesRegex(InteriorError, "unique"):
            normalize_interior(data)

    def test_undoable_tabletop_authoring_preserves_held_item_through_move(self):
        data = self.design()
        data["furniture"][0].pop("held_item")
        draft = InteriorDraft(data)
        draft.set_held_item("table", "Example.Goblet")
        held = deepcopy(draft.data["furniture"][0]["held_item"])
        self.assertEqual(held["mod_data"], {"Example/Finish": "gold"})
        draft.move_furniture("table", 7, 7)
        self.assertEqual(draft.data["furniture"][0]["held_item"], held)
        draft.clear_held_item("table")
        self.assertNotIn("held_item", draft.data["furniture"][0])
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"][0]["held_item"], held)
        before = draft.snapshot()
        with self.assertRaises(InteriorError):
            draft.set_held_item("table", "Example.Table")
        self.assertEqual(draft.snapshot(), before)

    def test_export_preserves_nested_native_item_and_declares_its_provider(self):
        data = self.design()
        atlas_path = self.root / "tiles.png"
        with Image.new("RGBA", (16, 16), "red") as image:
            image.save(atlas_path)
        data["atlas"] = import_atlas(atlas_path, self.root)
        result = compile_interior(data, "Example.Home", "Example.NPC", self.root, "Home")
        self.assertEqual(result["dependencies"], ["Example.Decor", "Example.Tables"])
        held = result["runtime"]["furniture"][0]["held_item"]
        self.assertEqual(held, data["furniture"][0]["held_item"])
        held["mod_data"]["Example/Finish"] = "changed"
        self.assertEqual(data["furniture"][0]["held_item"]["mod_data"]["Example/Finish"], "worn")
        maps = [payload for path, payload in result["files"].items() if path.endswith(".tmx")]
        self.assertTrue(maps)
        self.assertTrue(all(b"Example.Goblet" not in payload for payload in maps))


if __name__ == "__main__":
    unittest.main()
