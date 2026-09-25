"""Interior edits, structural maps, and real furniture contracts stay distinct."""

from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_furniture import attach_texture, validate_definition
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, animation_tile, compile_interior, floor_cells,
    footprint,
    import_atlas, interior_asset_references, interior_export_issues, interior_tmx,
    map_layers, new_interior, normalize_interior, render_interior,
)


class InteriorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def definition(self, **changes):
        value = {"id": "(F)Example.Chair", "name": "Chair", "kind": "chair",
                 "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1,
                 "dependency": "Example.Furniture", "mod_data": {"Example/Variant": "blue"}}
        value.update(changes)
        return validate_definition(value)

    def atlas(self, filename="tiles.png", size=(64, 16)):
        path = self.root / filename
        with Image.new("RGBA", size, "red") as image:
            if size == (64, 16):
                for index, color in enumerate(("red", "green", "blue", "yellow")):
                    image.paste(color, (index * 16, 0, index * 16 + 16, 16))
            image.save(path)
        return path

    def design(self, kind="residence"):
        data = new_interior(kind)
        data["atlas"] = import_atlas(self.atlas(), self.root)
        data["style"].update(floor=0, wall_top=1, wall_middle=2, wall_bottom=3)
        data["catalog"] = [self.definition()]
        return data

    def draft(self, kind="residence"):
        return InteriorDraft(self.design(kind))

    @staticmethod
    def gids(root, name):
        return [int(value.strip()) for value in root.find(f"layer[@name='{name}']/data").text.split(",")]

    def test_blank_designs_are_detached_bounded_drafts_with_no_game_assets(self):
        residence, spouse = new_interior(), new_interior("spouse")
        self.assertEqual((spouse["width"], spouse["height"]), (6, 9))
        self.assertEqual(residence["catalog"], [])
        self.assertEqual(residence["atlas"]["asset"], "")
        normalized = normalize_interior(residence)
        normalized["rooms"][0]["name"] = "Changed"
        self.assertEqual(residence["rooms"][0]["name"], "Main room")
        self.assertTrue(interior_export_issues(residence, self.root))

    def test_rejected_room_removal_preserves_furniture_metadata_and_history(self):
        draft = self.draft()
        room_id = draft.add_room("Study", 12, 5, 4, 4)
        item_id = draft.place_furniture("(F)Example.Chair", 13, 6)
        before = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "furniture"):
            draft.remove_room(room_id)
        self.assertEqual(draft.snapshot(), before)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"], [])
        self.assertEqual(len(draft.data["rooms"]), 2)
        self.assertTrue(draft.redo())
        self.assertEqual(draft.data["furniture"][0]["id"], item_id)
        self.assertEqual(draft.data["furniture"][0]["mod_data"], {"Example/Variant": "blue"})

    def test_room_removal_and_undo_restore_complete_room_and_item_state(self):
        draft = self.draft()
        room_id = draft.add_room("Study", 12, 5, 4, 4)
        item_id = draft.place_furniture("(F)Example.Chair", 13, 6)
        original = draft.snapshot()
        draft.remove_furniture(item_id)
        draft.remove_room(room_id)
        self.assertEqual(len(draft.data["rooms"]), 1)
        self.assertTrue(draft.undo())
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), original)

    def test_disconnect_overlap_and_entry_removal_are_atomic(self):
        draft = self.draft()
        initial = draft.snapshot()
        for rectangle in ((16, 5, 4, 4), (11, 5, 4, 4)):
            with self.subTest(rectangle=rectangle), self.assertRaises(InteriorError):
                draft.add_room("Invalid", *rectangle)
            self.assertEqual(draft.snapshot(), initial)
        draft.add_room("Side", 12, 5, 4, 4)
        before = draft.snapshot()
        with self.assertRaises(InteriorError):
            draft.remove_room("main")
        self.assertEqual(draft.snapshot(), before)

    def test_rejected_item_move_or_rotation_preserves_item_and_undo_history(self):
        data = self.design()
        data["catalog"] = [self.definition(footprint=[2, 1], rotations=4,
                                          rotation_footprints={"1": [1, 2]})]
        draft = InteriorDraft(data)
        item_id = draft.place_furniture("(F)Example.Chair", 10, 12)
        initial = draft.snapshot()
        with self.assertRaises(InteriorError):
            draft.rotate_furniture(item_id)
        with self.assertRaises(InteriorError):
            draft.move_furniture(item_id, 4, 10)
        self.assertEqual(draft.snapshot(), initial)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"], [])

    def test_room_toggle_cannot_silently_strand_furniture_or_entry(self):
        draft = self.draft()
        room_id = draft.add_room("Study", 12, 5, 4, 4)
        draft.place_furniture("(F)Example.Chair", 13, 6)
        original = draft.snapshot()
        changed = draft.snapshot()
        next(room for room in changed["rooms"] if room["id"] == room_id)["enabled"] = False
        with self.assertRaises(InteriorError):
            draft.apply(changed)
        self.assertEqual(draft.snapshot(), original)

    def test_rugs_can_overlap_furniture_but_solids_and_anchors_cannot(self):
        data = self.design()
        data["catalog"].append(self.definition(id="(F)Example.Rug", kind="rug", footprint=[3, 3]))
        draft = InteriorDraft(data)
        draft.place_furniture("(F)Example.Rug", 5, 6)
        draft.place_furniture("(F)Example.Chair", 6, 7)
        self.assertEqual(len(draft.data["furniture"]), 2)
        for x, y in ((6, 7), tuple(data["entry"])):
            with self.subTest(point=(x, y)), self.assertRaises(InteriorError):
                draft.place_furniture("(F)Example.Chair", x, y)

    def test_two_state_furniture_retains_declared_bounds_until_explicit_override(self):
        definition = self.definition(footprint=[2, 1], rotations=2)
        self.assertEqual(footprint(definition, 0), (2, 1))
        self.assertEqual(footprint(definition, 1), (2, 1))
        explicit = self.definition(footprint=[2, 1], rotations=2, rotation_footprints={"1": [1, 2]})
        self.assertEqual(footprint(explicit, 1), (1, 2))
        data = self.design()
        data["catalog"] = [definition]
        draft = InteriorDraft(data)
        identity = draft.place_furniture("(F)Example.Chair", 10, 12)
        draft.rotate_furniture(identity)
        self.assertEqual(draft.data["furniture"][0]["rotation"], 1)

    def test_wall_furniture_requires_wall_region_and_outdoor_items_are_rejected(self):
        data = self.design()
        data["catalog"] = [self.definition(kind="painting")]
        draft = InteriorDraft(data)
        draft.place_furniture("(F)Example.Chair", 6, 3)
        with self.assertRaises(InteriorError):
            draft.place_furniture("(F)Example.Chair", 6, 6)
        data["catalog"] = [self.definition(placement="outdoors")]
        draft = InteriorDraft(data)
        with self.assertRaises(InteriorError):
            draft.place_furniture("(F)Example.Chair", 6, 6)

    def test_spouse_route_must_remain_walkable(self):
        data = self.design("spouse")
        data["catalog"] = [self.definition(footprint=[1, 5])]
        draft = InteriorDraft(data)
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.place_furniture("(F)Example.Chair", 0, 4)
        self.assertEqual(draft.data["furniture"], [])
        small = self.draft("spouse")
        with self.assertRaisesRegex(InteriorError, "clear"):
            small.place_furniture("(F)Example.Chair", *data["spouse_stand"])

    def test_malformed_nested_data_raises_domain_error_without_mutation(self):
        good = self.design()
        placed = {"id": "chair", "item_id": "(F)Example.Chair", "x": 6, "y": 6, "rotation": 0}
        cases = [
            {"rooms": [None]}, {"atlas": None}, {"catalog": [None]},
            {"furniture": [dict(placed, item_id=[])]},
            {"furniture": [dict(placed, item_id={})]},
            {"furniture": [dict(placed, x=-1)]},
            {"furniture": [dict(placed, rotation=True)]},
            {"furniture": [dict(placed, mod_data={"key": []})]},
            {"entry": [4, -1]}, {"width": True},
            {"animations": [{"tile_id": 0, "frames": [{"tile_id": 1, "duration_ms": 0}]}]},
            {"atlas": dict(good["atlas"], asset="../outside.png")},
        ]
        for changes in cases:
            data = deepcopy(good)
            data.update(changes)
            before = deepcopy(data)
            with self.subTest(changes=changes), self.assertRaises(InteriorError):
                normalize_interior(data)
            self.assertEqual(data, before)

    def test_duplicate_catalog_and_placement_ids_fail_closed(self):
        data = self.design()
        data["catalog"].append(deepcopy(data["catalog"][0]))
        with self.assertRaisesRegex(InteriorError, "unique"):
            normalize_interior(data)
        draft = self.draft()
        draft.place_furniture("(F)Example.Chair", 6, 6)
        data = draft.snapshot()
        data["furniture"].append(dict(data["furniture"][0], x=8))
        with self.assertRaisesRegex(InteriorError, "unique"):
            normalize_interior(data)

    def test_spouse_dimensions_and_single_floor_area_are_enforced(self):
        for changes in ({"width": 7}, {"height": 8}):
            data = new_interior("spouse")
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(InteriorError):
                normalize_interior(data)
        data = new_interior("spouse")
        data["rooms"][0]["optional"] = True
        with self.assertRaises(InteriorError):
            normalize_interior(data)

    def test_geometry_layers_do_not_change_when_furniture_is_added(self):
        draft = self.draft()
        before = map_layers(draft.data)
        draft.place_furniture("(F)Example.Chair", 6, 6)
        self.assertEqual(map_layers(draft.data), before)
        root = ET.fromstring(interior_tmx(draft.data))
        self.assertNotIn(b"Example.Chair", interior_tmx(draft.data))
        self.assertEqual(self.gids(root, "Back"), before["Back"])
        self.assertEqual([node.attrib["name"] for node in root.findall("layer")], ["Back", "Buildings", "Front", "Paths"])

    def test_structural_map_uses_imported_wall_tiles_and_collision_only_outside_floor(self):
        data = self.design()
        layers = map_layers(data)
        width = data["width"]
        self.assertEqual(layers["Back"][2 * width + 2], 2)
        self.assertEqual(layers["Back"][3 * width + 2], 3)
        self.assertEqual(layers["Back"][4 * width + 2], 4)
        for x, y in floor_cells(data):
            self.assertEqual(layers["Buildings"][y * width + x], 0)

    def test_compilation_is_deterministic_and_excludes_disconnected_room_variants(self):
        draft = self.draft()
        first = draft.add_room("Connecting room", 12, 5, 4, 4)
        second = draft.add_room("Far room", 16, 5, 4, 4)
        one = compile_interior(draft.data, "Example_Home", "Example_NPC", self.root, "assets/interior/")
        two = compile_interior(draft.data, "Example_Home", "Example_NPC", self.root, "assets/interior/")
        self.assertEqual(one, two)
        variants = one["runtime"]["variants"]
        self.assertEqual([variant["id"] for variant in variants], ["0", "1", "3"])
        self.assertEqual(one["runtime"]["default_variant"], "3")
        self.assertEqual(variants[-1]["enabled_rooms"], sorted(("main", first, second)))
        self.assertTrue(all(patch["FromFile"] in one["files"] for patch in one["patches"] if patch["Action"] == "Load"))
        self.assertTrue(any(patch.get("MapTiles") for patch in one["patches"]))

    def test_native_wall_furniture_has_regions_sheet_and_clear_upper_wall(self):
        draft = self.draft()
        draft.add_room("Optional room", 12, 5, 4, 4)
        compiled = compile_interior(draft.data, "Example_Home", "Example_NPC", self.root, "assets/interior/")
        for variant in compiled["runtime"]["variants"]:
            patch = next(p for p in compiled["patches"] if p["Action"] == "EditMap" and p["Target"] == variant["map_asset"])
            expected = {"Example_Home_" + room for room in variant["enabled_rooms"]}
            self.assertEqual(set(patch["MapProperties"]["WallIDs"].split(",")), expected)
            self.assertEqual(set(patch["MapProperties"]["FloorIDs"].split(",")), expected)
            load = next(p for p in compiled["patches"] if p["Action"] == "Load" and p["Target"] == variant["map_asset"])
            root = ET.fromstring(compiled["files"][load["FromFile"]])
            native = root.find("tileset[@name='walls_and_floors']")
            self.assertEqual(native.find("image").get("source"), "walls_and_floors")
            ranges = [(int(sheet.get("firstgid")), int(sheet.get("firstgid")) + int(sheet.get("tilecount")))
                      for sheet in root.findall("tileset")]
            self.assertTrue(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])))
            buildings = self.gids(root, "Buildings")
            width = draft.data["width"]
            # A native window occupies the top and middle rows. The baseboard
            # must still prevent the player walking into the cleared wall area.
            for y in (2, 3):
                self.assertEqual(buildings[y * width + 2], 0)
            self.assertNotEqual(buildings[4 * width + 2], 0)
            floor = floor_cells(draft.data, set(variant["enabled_rooms"]))
            reached, pending = set(), [next(iter(floor))]
            while pending:
                x, y = pending.pop()
                if (x, y) in reached or not (0 <= x < width and 0 <= y < draft.data["height"]):
                    continue
                if buildings[y * width + x]:
                    continue
                reached.add((x, y))
                pending.extend(((x-1, y), (x+1, y), (x, y-1), (x, y+1)))
            self.assertEqual(reached, floor, "Opening the hanging area must not leak across a room boundary.")

    def test_compilation_retains_live_item_ids_metadata_and_only_used_dependencies(self):
        draft = self.draft()
        item = draft.place_furniture("(F)Example.Chair", 6, 6)
        data = draft.snapshot()
        data["catalog"].append(self.definition(id="(F)Unused", dependency="Unused.Mod"))
        compiled = compile_interior(data, "Home", "NPC", self.root, "interior/")
        self.assertEqual(compiled["dependencies"], ["Example.Furniture"])
        self.assertEqual(compiled["runtime"]["furniture"], data["furniture"])
        self.assertEqual(compiled["runtime"]["furniture"][0]["id"], item)
        compiled["runtime"]["furniture"][0]["mod_data"]["Example/Variant"] = "mutated"
        self.assertEqual(data["furniture"][0]["mod_data"]["Example/Variant"], "blue")

    def test_spouse_marker_is_unique_and_uses_original_floor_pixels(self):
        data = self.design("spouse")
        compiled = compile_interior(data, "MySpouseRoom", "MyNPC", self.root, "spouse/")
        root = ET.fromstring(compiled["files"]["spouse/room_0.tmx"])
        marker = root.find("tileset[@name='pixelheart_spouse_marker']")
        gid = int(marker.attrib["firstgid"])
        marker_property = marker.find("tile/properties/property")
        self.assertEqual(marker_property.attrib, {"name": "Pixelheart.Interiors/SpouseRoom", "value": "MySpouseRoom"})
        back = self.gids(root, "Back")
        self.assertEqual(back.count(gid), 1)
        x, y = data["spouse_stand"]
        self.assertEqual(back[y * 6 + x], gid)
        self.assertEqual(back.count(1), len(floor_cells(data)) - 1)
        self.assertEqual(compiled["runtime"]["spouse_npc"], "MyNPC")
        source = marker.find("image").attrib["source"]
        with Image.open(io.BytesIO(compiled["files"]["spouse/" + source])) as image:
            self.assertEqual(image.size, (64, 16))
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (255, 0, 0, 255))

    def test_spouse_marker_preserves_animated_floor_and_nonzero_source_index(self):
        data = self.design("spouse")
        data["style"]["floor"] = 3
        data["animations"] = [{"tile_id": 3, "frames": [
            {"tile_id": 1, "duration_ms": 100}, {"tile_id": 2, "duration_ms": 100}]}]
        compiled = compile_interior(data, "MySpouseRoom", "MyNPC", self.root, "spouse/")
        root = ET.fromstring(compiled["files"]["spouse/room_0.tmx"])
        marker = root.find("tileset[@name='pixelheart_spouse_marker']")
        tile = marker.find("tile[@id='3']")
        self.assertIsNotNone(tile.find("properties/property[@name='Pixelheart.Interiors/SpouseRoom']"))
        self.assertEqual([frame.attrib for frame in tile.findall("animation/frame")],
                         [{"tileid": "1", "duration": "100"}, {"tileid": "2", "duration": "100"}])
        marker_gid = int(marker.attrib["firstgid"]) + 3
        self.assertEqual(self.gids(root, "Back").count(marker_gid), 1)

    def test_export_rejects_tile_animation_timing_the_game_cannot_represent(self):
        data = self.design()
        data["animations"] = [{"tile_id": 0, "frames": [
            {"tile_id": 1, "duration_ms": 100}, {"tile_id": 2, "duration_ms": 200}]}]
        with self.assertRaisesRegex(InteriorError, "shared frame duration"):
            compile_interior(data, "Home", "NPC", self.root, "interior/")

    def test_tile_animation_boundaries_match_exported_durations(self):
        data = self.design()
        data["animations"] = [{"tile_id": 0, "frames": [
            {"tile_id": 1, "duration_ms": 100}, {"tile_id": 2, "duration_ms": 200}]}]
        for time, tile in ((0, 1), (99, 1), (100, 2), (299, 2), (300, 1)):
            with self.subTest(time=time):
                self.assertEqual(animation_tile(data, 0, time), tile)
        root = ET.fromstring(interior_tmx(data))
        frames = root.findall("tileset/tile/animation/frame")
        self.assertEqual([frame.attrib for frame in frames], [{"tileid": "1", "duration": "100"}, {"tileid": "2", "duration": "200"}])
        with render_interior(data, self.root, elapsed_ms=99) as first, render_interior(data, self.root, elapsed_ms=100) as second:
            self.assertEqual(first.getpixel((6 * 16, 6 * 16)), (0, 128, 0, 255))
            self.assertEqual(second.getpixel((6 * 16, 6 * 16)), (0, 0, 255, 255))

    def test_furniture_animation_is_previewed_and_its_texture_remains_project_asset(self):
        data = self.design()
        definition = self.definition(frames=[
            {"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100},
            {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 200}])
        data["catalog"] = [attach_texture(definition, self.atlas("furniture.png"), self.root)]
        draft = InteriorDraft(data)
        draft.place_furniture("(F)Example.Chair", 6, 6)
        with render_interior(draft.data, self.root, elapsed_ms=99) as first, render_interior(draft.data, self.root, elapsed_ms=100) as second:
            self.assertEqual(first.getpixel((6 * 16, 6 * 16)), (255, 0, 0, 255))
            self.assertEqual(second.getpixel((6 * 16, 6 * 16)), (0, 128, 0, 255))
        references = set(interior_asset_references(draft.data))
        self.assertIn(data["atlas"]["asset"], references)
        self.assertIn(data["catalog"][0]["preview_asset"], references)
        self.assertTrue(all((self.root / reference).is_file() for reference in references))

    def test_missing_portable_assets_and_changed_atlas_are_export_errors(self):
        data = self.design()
        path = self.root / data["atlas"]["asset"]
        path.unlink()
        self.assertTrue(interior_export_issues(data, self.root))
        data = self.design()
        with Image.new("RGBA", (32, 32), "blue") as image:
            image.save(self.root / data["atlas"]["asset"])
        self.assertTrue(interior_export_issues(data, self.root))

    def test_invalid_atlas_import_is_rejected_without_orphaned_project_asset(self):
        source = self.atlas("invalid.png", size=(33, 16))
        with self.assertRaises(InteriorError):
            import_atlas(source, self.root)
        self.assertFalse((self.root / "world_assets").exists())

    def test_import_rejects_atlas_larger_than_model_can_represent(self):
        source = self.atlas("wide.png", size=(16 * 257, 16))
        with self.assertRaises(InteriorError):
            import_atlas(source, self.root)

    def test_export_rejects_non_tile_aligned_atlas_even_when_truncated_counts_match(self):
        data = self.design()
        data["atlas"]["columns"] = 2
        data["atlas"]["tile_count"] = 3
        # Integer division alone sees 2 columns and 3 tiles, though this is not a sheet.
        with Image.new("RGBA", (33, 24), "blue") as image:
            image.save(self.root / data["atlas"]["asset"])
        self.assertTrue(interior_export_issues(data, self.root))


if __name__ == "__main__":
    unittest.main()
