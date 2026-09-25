import io
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_architecture import (
    architecture_bounds, architecture_candidate, architecture_cells,
    architecture_preview, architecture_walkable_connected, remove_architecture_candidate,
    stage_architecture_library, validate_architecture_definition,
)
from pixelheart_core.interior_furniture import FurnitureValidationError, import_furniture_library
from pixelheart_core.interior_layout import partition_candidate, resize_room_candidate
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, compile_interior, ensure_doorway,
    interior_asset_references, interior_tmx, map_layers, new_interior,
    normalize_interior, reachable_tiles, render_interior, room_edit_candidate,
    spouse_access_issues,
)


def definition(identity="counter", *, placement="floor", width=2, height=2):
    size = width*height
    return dict(id=identity, name="Counter", category="Kitchen", placement=placement,
                width=width, height=height,
                layers={"Back": [None]*size, "Buildings": [None]*(size-width)+[1]*width,
                        "Front": [2]*width+[None]*(size-width)})


def design():
    data = ensure_doorway(new_interior())
    data["atlas"] = dict(asset="atlas.png", columns=4, tile_count=4)
    data["architecture_catalog"] = [definition()]
    return normalize_interior(data)


class ArchitectureModelTests(unittest.TestCase):
    def spouse_design(self):
        data = new_interior("spouse")
        data["atlas"] = dict(asset="atlas.png", columns=4, tile_count=4)
        data["architecture_catalog"] = [definition("block", width=1, height=1),
                                        definition("crossing", width=6, height=1)]
        return data

    def test_spouse_architecture_can_cover_the_former_bottom_entry_and_export(self):
        data = architecture_candidate(self.spouse_design(), "block", 3, 8)
        self.assertTrue(architecture_walkable_connected(data))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGBA", (64, 16), "white").save(root / "atlas.png")
            compiled = compile_interior(data, "Spouse", "NPC", root, "room/")
        self.assertEqual(compiled["runtime"]["default_variant"], "0")
        self.assertEqual(compiled["runtime"]["entry"], [3, 8])
        self.assertEqual(compiled["runtime"]["spouse_stand"], [3, 5])

    def test_spouse_architecture_can_split_floor_served_by_separate_west_openings(self):
        data = architecture_candidate(self.spouse_design(), "crossing", 0, 6)
        self.assertTrue(architecture_walkable_connected(data))
        self.assertIn((3, 5), reachable_tiles(data))
        self.assertIn((3, 8), reachable_tiles(data))

    def test_legacy_west_architecture_barrier_reopens_for_repair(self):
        data = self.spouse_design()
        data["architecture"] = [dict(id=f"west-{y}", piece_id="block", room_id="main", x=0, y=y)
                                for y in range(4, 9)]
        saved = deepcopy(data)
        self.assertFalse(architecture_walkable_connected(data))
        draft = InteriorDraft(data)
        self.assertEqual(draft.data["architecture"], saved["architecture"])
        self.assertTrue(spouse_access_issues(draft.data))
        repaired = remove_architecture_candidate(draft.data, "west-6")
        draft.apply(repaired)
        self.assertTrue(architecture_walkable_connected(draft.data))
        self.assertFalse(spouse_access_issues(draft.data))
        self.assertTrue(draft.undo())
        self.assertTrue(spouse_access_issues(draft.data))
        self.assertEqual(data, saved)
        with self.assertRaisesRegex(InteriorError, "farmhouse opening on the left"):
            architecture_candidate(repaired, "block", 0, 6)

    def test_legacy_data_stays_unchanged(self):
        data = new_interior()
        self.assertEqual(normalize_interior(data), data)
        self.assertNotIn("architecture", data)

    def test_place_move_remove_are_atomic_with_stable_identity(self):
        original = design()
        placed = architecture_candidate(original, "counter", 7, 7)
        self.assertNotIn("architecture", original)
        item = placed["architecture"][0]
        self.assertEqual(architecture_bounds(item, placed["architecture_catalog"][0]), (7, 7, 2, 2))
        self.assertEqual(item["room_id"], "main")
        moved = architecture_candidate(placed, "counter", 8, 9, placement_id=item["id"])
        self.assertEqual(moved["architecture"][0]["id"], item["id"])
        self.assertEqual(placed["architecture"][0]["x"], 7)
        removed = remove_architecture_candidate(moved, item["id"])
        self.assertEqual(removed["architecture"], [])

    def test_buildings_define_collision_and_front_does_not(self):
        placed = architecture_candidate(design(), "counter", 7, 7)
        self.assertEqual(architecture_cells(placed), {(7, 8), (8, 8)})
        self.assertIn((7, 7), reachable_tiles(placed))
        self.assertNotIn((7, 8), reachable_tiles(placed))

    def test_zero_is_art_not_transparency(self):
        piece = definition()
        piece["layers"]["Back"][0] = 0
        result = validate_architecture_definition(piece, 4)
        self.assertEqual(result["layers"]["Back"][0], 0)
        self.assertIsNone(result["layers"]["Back"][1])

    def test_bad_definitions_reject_instead_of_losing_tiles(self):
        for change in ({"width": True}, {"width": 33}, {"placement": "ceiling"},
                       {"layers": {"Back": [0]}}, {"layers": {"AlwaysFront": [0]*4}},
                       {"layers": {"Buildings": [None]*4}},
                       {"layers": {"Buildings": [None, None, False, 1]}},
                       {"layers": {"Buildings": [None, None, 4, 1]}}):
            with self.subTest(change=change), self.assertRaises(InteriorError):
                validate_architecture_definition({**definition(), **change}, 4)

    def test_unknown_gameplay_fields_are_not_imported(self):
        result = validate_architecture_definition({**definition(), "Action": "Warp Secret 1 2"}, 4)
        self.assertNotIn("Action", result)

    def test_invalid_placement_schema_gives_friendly_error(self):
        data = design()
        for item in ({"id": "bad", "piece_id": [], "room_id": "main", "x": 7, "y": 7},
                     {"id": "bad", "piece_id": "counter", "room_id": [], "x": 7, "y": 7},
                     {"id": "bad", "piece_id": "counter", "room_id": "main", "x": True, "y": 7}):
            with self.subTest(item=item), self.assertRaises(InteriorError):
                normalize_interior({**data, "architecture": [item]})

    def test_piece_cannot_block_arrival_or_doorway(self):
        data = design()
        for x, y in ((data["entry"][0], data["entry"][1]-1),
                     (data["doorway"][0], data["doorway"][1]-1)):
            with self.subTest(position=(x, y)), self.assertRaises(InteriorError):
                architecture_candidate(data, "counter", x, y)

    def test_wall_mount_cannot_float_in_middle_of_floor(self):
        data = design()
        data["architecture_catalog"][0]["placement"] = "wall"
        with self.assertRaisesRegex(InteriorError, "enabled room"):
            architecture_candidate(data, "counter", 7, 7)
        placed = architecture_candidate(data, "counter", 7, 3)
        self.assertEqual(placed["architecture"][0]["room_id"], "main")

    def test_floor_mount_can_have_tall_art_on_north_wall(self):
        data = design()
        data["architecture_catalog"] = [definition(height=4)]
        placed = architecture_candidate(data, "counter", 7, 2)
        self.assertEqual(architecture_cells(placed), {(7, 5), (8, 5)})

    def test_wall_fixture_mounts_on_horizontal_partition_face(self):
        data = design()
        data["architecture_catalog"] = [definition(width=1, placement="wall")]
        data = partition_candidate(data, "main", "horizontal", 2, 9, 10, opening_width=2)
        placed = architecture_candidate(data, "counter", 3, 7)
        self.assertEqual(placed["architecture"][0]["room_id"], "main")
        with self.assertRaises(InteriorError):
            architecture_candidate(data, "counter", 6, 7, room_id="main")

    def test_pieces_cannot_overlap_or_cross_room_boundary(self):
        placed = architecture_candidate(design(), "counter", 7, 7)
        with self.assertRaisesRegex(InteriorError, "overlap"):
            architecture_candidate(placed, "counter", 8, 7)
        with self.assertRaises(InteriorError):
            architecture_candidate(design(), "counter", 11, 7)

    def test_room_resizing_preserves_piece_and_rejects_crop(self):
        placed = architecture_candidate(design(), "counter", 9, 7)
        bigger = resize_room_candidate(placed, "main", 2, 5, 11, 8)
        self.assertEqual(placed["architecture"], bigger["architecture"])
        with self.assertRaises(InteriorError):
            resize_room_candidate(placed, "main", 2, 5, 7, 8)

    def test_room_move_and_rebase_carry_piece(self):
        placed = architecture_candidate(design(), "counter", 7, 7)
        moved = room_edit_candidate(placed, room_id="main", x=5, y=6)
        self.assertEqual((moved["architecture"][0]["x"], moved["architecture"][0]["y"]), (10, 8))
        moved = room_edit_candidate(placed, room_id="main", x=-2, y=2, allow_rebase=True)
        self.assertEqual((moved["architecture"][0]["x"], moved["architecture"][0]["y"]), (6, 6))

    def test_room_removal_protects_piece(self):
        placed = architecture_candidate(design(), "counter", 7, 7)
        draft = InteriorDraft(placed)
        with self.assertRaisesRegex(InteriorError, "architectural"):
            draft.remove_room("main")
        self.assertEqual(draft.data, placed)

    def test_undo_redo_restores_placement_and_geometry(self):
        draft = InteriorDraft(design())
        draft.apply(architecture_candidate(draft.data, "counter", 7, 7))
        identity = draft.data["architecture"][0]["id"]
        draft.apply(architecture_candidate(draft.data, "counter", 8, 8, placement_id=identity))
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["architecture"][0]["x"], 7)
        self.assertTrue(draft.redo())
        self.assertEqual(draft.data["architecture"][0]["x"], 8)

    def test_existing_furniture_collision_rejects_fixture(self):
        from pixelheart_core.interior_furniture import validate_definition
        data = design()
        data["catalog"] = [validate_definition(dict(id="(F)Example.Chair", name="Chair", kind="chair", footprint=[1, 1], sprite_size=[1, 1], rotations=1))]
        draft = InteriorDraft(data)
        draft.place_furniture(data["catalog"][0]["id"], 7, 8)
        with self.assertRaisesRegex(InteriorError, "architectural"):
            architecture_candidate(draft.data, "counter", 7, 7)

    def test_blocking_room_connection_is_rejected(self):
        data = design()
        data["architecture_catalog"] = [definition(width=1, height=1)]
        data = room_edit_candidate(data, x=12, y=7, width=3, height=1, optional=False)
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            architecture_candidate(data, "counter", 13, 7)

    def test_native_sink_between_counters_can_enclose_its_hidden_top_tile(self):
        data = design()
        counter = definition("solid", width=1, height=2)
        counter["layers"] = {"Back": [None, None], "Buildings": [1, 1], "Front": [None, None]}
        data["architecture_catalog"] = [counter, definition("sink", width=1, height=2)]
        data = architecture_candidate(data, "solid", 4, 5)
        data = architecture_candidate(data, "sink", 5, 5)
        data = architecture_candidate(data, "solid", 6, 5)
        self.assertNotIn((5, 5), reachable_tiles(data))
        self.assertIn((5, 7), reachable_tiles(data))
        self.assertEqual(len(data["architecture"]), 3)

    def test_layers_and_tmx_preserve_native_depth(self):
        data = architecture_candidate(design(), "counter", 7, 7)
        layers = map_layers(data)
        self.assertEqual(layers["Buildings"][8*data["width"]+7], 2)
        self.assertEqual(layers["Front"][7*data["width"]+7], 3)
        root = ET.fromstring(interior_tmx(data))
        for name in ("Back", "Buildings", "Front"):
            raw = root.find(f"./layer[@name='{name}']/data").text
            self.assertEqual([int(value) for value in raw.split(",")], layers[name])

    def test_optional_piece_disappears_only_with_own_room(self):
        data = room_edit_candidate(design(), x=12, y=5, width=5, height=8, optional=True)
        room_id = data["rooms"][-1]["id"]
        data = architecture_candidate(data, "counter", 14, 7)
        self.assertEqual(architecture_cells(data, {"main"}), set())
        self.assertEqual(architecture_cells(data, {"main", room_id}), {(14, 8), (15, 8)})
        layers = map_layers(data, {"main"})
        self.assertEqual(layers["Front"][7*data["width"]+14], 0)
        data["rooms"][-1]["enabled"] = False
        self.assertEqual(normalize_interior(data)["architecture"][0]["room_id"], room_id)


class ArchitectureAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with Image.new("RGBA", (64, 16), (20, 30, 40, 255)) as atlas:
            for x, color in ((16, (200, 40, 30, 255)), (32, (20, 210, 50, 255)), (48, (10, 30, 220, 255))):
                atlas.paste(color, (x, 0, x+16, 16))
            atlas.save(self.root / "source.png")
        self.external = {**definition(), "preview_asset": "source.png", "columns": 4, "tile_count": 4}
        self.addCleanup(self.temp.cleanup)

    def test_stage_is_portable_idempotent_and_preserves_existing_indices(self):
        with Image.new("RGBA", (16, 16), (50, 60, 70, 255)) as image:
            image.save(self.root / "base.png")
        data = new_interior()
        data["atlas"] = dict(asset="base.png", columns=1, tile_count=1)
        staged = stage_architecture_library(data, [self.external], self.root)
        self.assertEqual(staged["style"], data["style"])
        self.assertEqual(staged["atlas"]["columns"], 1)
        self.assertNotIn("preview_asset", staged["architecture_catalog"][0])
        self.assertEqual(stage_architecture_library(staged, [self.external], self.root), staged)
        (self.root / "source.png").unlink()
        with architecture_preview(staged["architecture_catalog"][0], staged, self.root) as image:
            self.assertEqual(image.getpixel((0, 0)), (20, 210, 50, 255))
            self.assertEqual(image.getpixel((0, 16)), (200, 40, 30, 255))
        refs = list(interior_asset_references(staged))
        self.assertEqual(refs, [staged["atlas"]["asset"]])

    def test_references_are_validated_before_any_asset_write(self):
        data = new_interior()
        invalid = {**self.external, "id": "invalid", "tile_count": 8}
        with self.assertRaises(InteriorError):
            stage_architecture_library(data, [self.external, invalid], self.root)
        self.assertFalse((self.root / "world_assets").exists())
        self.assertNotIn("architecture_catalog", data)

    def test_library_preflights_bad_architecture_before_copying_other_assets(self):
        library = {"format": "pixelheart-interior-library", "version": 1, "definitions": [],
                   "architecture": [self.external, {**self.external, "id": "bad", "preview_asset": "missing.png"}]}
        path = self.root / "library.json"
        path.write_text(json.dumps(library))
        destination = self.root / "project"
        with self.assertRaises(FurnitureValidationError):
            import_furniture_library(path, destination)
        self.assertFalse(destination.exists())

    def test_library_import_copies_checked_assets_and_stages(self):
        path = self.root / "library.json"
        path.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                    "definitions": [], "architecture": [self.external]}))
        destination = self.root / "project"
        library = import_furniture_library(path, destination)
        self.assertTrue((destination / library["architecture"][0]["preview_asset"]).is_file())
        staged = stage_architecture_library(new_interior(), library["architecture"], destination)
        self.assertEqual(staged["architecture_catalog"][0]["name"], "Counter")

    def test_refresh_that_invalidates_existing_placement_is_atomic(self):
        staged = stage_architecture_library(ensure_doorway(new_interior()), [self.external], self.root)
        placed = architecture_candidate(staged, "counter", 9, 7)
        before = deepcopy(placed)
        larger = {**self.external, "width": 4,
                  "layers": {"Buildings": [None]*4+[1]*4}}
        files_before = set(self.root.rglob("*.png"))
        with self.assertRaises(InteriorError):
            stage_architecture_library(placed, [larger], self.root)
        self.assertEqual(placed, before)
        self.assertEqual(set(self.root.rglob("*.png")), files_before)

    def test_static_piece_does_not_reuse_animated_atlas_tile(self):
        data = new_interior()
        data["atlas"] = dict(asset="source.png", columns=4, tile_count=4)
        data["animations"] = [{"tile_id": 1, "frames": [{"tile_id": 1, "duration_ms": 100},
                                                         {"tile_id": 3, "duration_ms": 100}]}]
        staged = stage_architecture_library(data, [self.external], self.root)
        self.assertNotEqual(staged["architecture_catalog"][0]["layers"]["Buildings"][2], 1)
        self.assertEqual(staged["animations"], data["animations"])

    def test_optional_export_scopes_no_furniture_to_actual_collision(self):
        data = stage_architecture_library(ensure_doorway(new_interior()), [self.external], self.root)
        data = room_edit_candidate(data, x=12, y=5, width=5, height=8, optional=True)
        data = architecture_candidate(data, "counter", 14, 7)
        result = compile_interior(data, "Home", "Npc", self.root, "maps/")
        for patch in result["patches"]:
            if patch["Action"] != "EditMap":
                continue
            protected = {(tile["Position"]["X"], tile["Position"]["Y"])
                         for tile in patch["MapTiles"] if tile["SetProperties"].get("NoFurniture") == "T"}
            self.assertNotIn((14, 7), protected)  # Front overhang leaves its floor usable.
            self.assertEqual((14, 8) in protected, patch["Target"].endswith("_1"))

    def test_back_wall_art_is_excluded_from_runtime_wallpaper_regions(self):
        piece = {**self.external, "placement": "wall", "width": 1, "height": 3,
                 "layers": {"Back": [1, 2, 3]}}
        data = stage_architecture_library(ensure_doorway(new_interior()), [piece], self.root)
        data = architecture_candidate(data, "counter", 7, 2)
        result = compile_interior(data, "Home", "Npc", self.root, "maps/")
        edits = result["patches"][1]["MapTiles"]
        self.assertFalse(any(edit["Position"] == {"X": 7, "Y": 2}
                             and "WallID" in edit["SetProperties"] for edit in edits))

    def test_unsafe_source_path_is_rejected(self):
        for path in ("../source.png", "/tmp/source.png", "C:/source.png"):
            with self.subTest(path=path), self.assertRaises(InteriorError):
                validate_architecture_definition({**self.external, "preview_asset": path}, external=True)

    def test_render_and_compile_preserve_art_and_static_finishes(self):
        piece = deepcopy(self.external)
        piece["layers"]["Back"][0] = 3
        data = stage_architecture_library(ensure_doorway(new_interior()), [piece], self.root)
        data = architecture_candidate(data, "counter", 7, 7)
        with render_interior(data, self.root) as image:
            self.assertEqual(image.getpixel((7*16, 7*16)), (20, 210, 50, 255))
            self.assertEqual(image.getpixel((7*16, 8*16)), (200, 40, 30, 255))
        result = compile_interior(data, "TestHome", "TestNpc", self.root, "maps/")
        edits = result["patches"][1]["MapTiles"]
        affected = [edit for edit in edits if edit["Position"] == {"X": 7, "Y": 7}]
        self.assertFalse(any("FloorID" in edit["SetProperties"] for edit in affected))
        self.assertFalse(any("Action" in edit["SetProperties"] for edit in edits))
        self.assertNotIn("architecture", result["runtime"])
        self.assertIn("maps/room_0.tmx", result["files"])


if __name__ == "__main__":
    unittest.main()
