"""Floorplan changes preserve contents and agree with exported map collision."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_layout import (
    corridor_candidate, opening_candidate, partition_candidate, partition_cells,
    partition_span, remove_partition_candidate, resize_room_candidate,
)
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, compile_interior, import_atlas, interior_tmx,
    map_layers, new_interior, normalize_interior, place_doorway, reachable_tiles,
    render_interior, room_edit_candidate,
)
from pixelheart_core.interior_furniture import ROOM_FRAME_TILES, validate_definition


def furnished():
    data = new_interior()
    data["catalog"] = [validate_definition({"id": "(F)Chair", "name": "Chair", "kind": "chair",
                                           "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1})]
    data["furniture"] = [{"id": "chair", "item_id": "(F)Chair", "x": 10, "y": 7, "rotation": 0, "mod_data": {}}]
    return data


class InteriorLayoutTests(unittest.TestCase):
    def test_resize_keeps_furniture_and_anchors_fixed_and_undoes_once(self):
        draft = InteriorDraft(furnished())
        before = draft.snapshot()
        candidate = resize_room_candidate(before, "main", 1, 4, 15, 11)
        self.assertEqual(candidate["furniture"], before["furniture"])
        self.assertEqual(candidate["entry"], before["entry"])
        self.assertEqual(candidate["spouse_stand"], before["spouse_stand"])
        self.assertEqual(draft.snapshot(), before)
        draft.apply(candidate)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), before)
        self.assertFalse(draft.undo())

    def test_resize_rejects_cropping_a_furnished_floor(self):
        with self.assertRaisesRegex(InteriorError, "whole furniture"):
            resize_room_candidate(furnished(), "main", 2, 5, 8, 8)

    def test_resize_preserves_doorway_attachment_and_standard_arrival(self):
        data = place_doorway(new_interior(), 5, 12)
        larger = resize_room_candidate(data, "main", 2, 5, 10, 10)
        self.assertEqual(larger["doorway"], [5, 14])
        self.assertEqual(larger["entry"], [5, 13])
        data["entry"] = [4, 10]
        larger = resize_room_candidate(data, "main", 2, 5, 10, 10)
        self.assertEqual(larger["entry"], [4, 10])

    def test_resize_rejects_overlap_and_disconnection(self):
        data = room_edit_candidate(new_interior(), x=12, y=5, width=4, height=4)
        for dimensions in ((2, 5, 11, 8), (2, 5, 9, 8)):
            with self.assertRaises(InteriorError):
                resize_room_candidate(data, "main", *dimensions)

    def test_corridor_has_narrow_geometry_and_permanent_room_contract(self):
        data = corridor_candidate(new_interior(), 12, 8, 5, 2)
        hall = data["rooms"][-1]
        self.assertEqual((hall["kind"], hall["width"], hall["height"], hall["optional"]), ("hallway", 5, 2, False))
        joined = room_edit_candidate(data, x=17, y=6, width=4, height=6)
        self.assertEqual(len(joined["rooms"]), 3)
        self.assertIn((20, 11), reachable_tiles(joined))

    def test_horizontal_partition_has_real_collision_and_open_passage(self):
        data = partition_candidate(new_interior(), "main", "horizontal", 2, 9, 10)
        wall = data["partitions"][0]
        self.assertEqual(wall["openings"], [{"offset": 4, "width": 1}])
        self.assertEqual(len(partition_span(wall)), 10)
        self.assertNotIn((6, 9), partition_cells(data))
        self.assertIn((5, 9), partition_cells(data))
        self.assertIn((2, 5), reachable_tiles(data))
        self.assertNotIn((5, 9), reachable_tiles(data))
        layers = map_layers(data)
        self.assertTrue(layers["Buildings"][9*data["width"]+5])
        self.assertEqual(layers["Buildings"][9*data["width"]+6], 0)
        for y in (7, 8, 9):
            self.assertTrue(layers["Front"][y*data["width"]+5])
            self.assertEqual(layers["Front"][y*data["width"]+6], 0)

    def test_wide_opening_replaces_previous_gap(self):
        data = partition_candidate(new_interior(), "main", "horizontal", 2, 9, 10)
        wall_id = data["partitions"][0]["id"]
        wider = opening_candidate(data, wall_id, 6, 3)
        self.assertEqual(wider["partitions"][0]["openings"], [{"offset": 6, "width": 3}])
        self.assertIn((6, 9), partition_cells(wider))
        self.assertTrue({(8, 9), (9, 9), (10, 9)}.isdisjoint(partition_cells(wider)))
        self.assertEqual(data["partitions"][0]["openings"], [{"offset": 4, "width": 1}])

    def test_complete_dividing_wall_without_opening_is_rejected(self):
        with self.assertRaisesRegex(InteriorError, "connected"):
            partition_candidate(new_interior(), "main", "vertical", 7, 5, 8, opening_width=0)
        data = partition_candidate(new_interior(), "main", "vertical", 7, 5, 8)
        with self.assertRaisesRegex(InteriorError, "connected"):
            opening_candidate(data, data["partitions"][0]["id"], 3, 0)

    def test_short_solid_partition_and_removal_are_atomic(self):
        draft = InteriorDraft()
        data = partition_candidate(draft.data, "main", "vertical", 7, 6, 3, opening_width=0)
        draft.apply(data)
        wall_id = draft.data["partitions"][0]["id"]
        removed = remove_partition_candidate(draft.data, wall_id)
        draft.apply(removed)
        self.assertEqual(draft.data["partitions"], [])
        draft.undo()
        self.assertEqual(draft.data, data)

    def test_walls_cannot_cover_furniture_or_arrival(self):
        for x, y in ((10, 7), (4, 10)):
            with self.assertRaises(InteriorError):
                partition_candidate(furnished(), "main", "vertical", x, y, 1, opening_width=0)

    def test_furniture_cannot_block_opening_or_either_approach(self):
        data = partition_candidate(furnished(), "main", "vertical", 7, 5, 8)
        for point in ((7, 8), (6, 8), (8, 8)):
            candidate = deepcopy(data)
            candidate["furniture"][0].update(zip(("x", "y"), point))
            with self.assertRaisesRegex(InteriorError, "walkable approach"):
                normalize_interior(candidate)

    def test_room_move_carries_owned_walls(self):
        data = room_edit_candidate(new_interior(), x=12, y=5, width=4, height=6)
        room = data["rooms"][-1]
        data = partition_candidate(data, room["id"], "horizontal", 12, 8, 4)
        moved = room_edit_candidate(data, room_id=room["id"], x=12, y=7)
        self.assertEqual(moved["partitions"][0]["y"], 10)
        self.assertEqual(moved["partitions"][0]["id"], data["partitions"][0]["id"])

    def test_room_rebase_carries_existing_walls(self):
        data = partition_candidate(new_interior(), "main", "horizontal", 2, 9, 10)
        rebased = room_edit_candidate(data, x=-4, y=5, width=6, height=6, allow_rebase=True)
        self.assertEqual(rebased["partitions"][0]["x"], 7)

    def test_resize_keeps_walls_fixed_and_rejects_cropping_them(self):
        data = partition_candidate(new_interior(), "main", "horizontal", 2, 9, 10)
        wider = resize_room_candidate(data, "main", 1, 5, 12, 8)
        self.assertEqual(wider["partitions"], data["partitions"])
        with self.assertRaisesRegex(InteriorError, "entire wall"):
            resize_room_candidate(data, "main", 3, 5, 9, 8)

    def test_removing_room_removes_its_walls_in_same_undo(self):
        data = room_edit_candidate(new_interior(), x=12, y=5, width=4, height=6)
        identity = data["rooms"][-1]["id"]
        data = partition_candidate(data, identity, "vertical", 14, 6, 3)
        draft = InteriorDraft(data)
        draft.remove_room(identity)
        self.assertEqual(draft.data["partitions"], [])
        draft.undo()
        self.assertEqual(draft.data, data)

    def test_inactive_rooms_do_not_leave_partition_art_or_collision(self):
        data = room_edit_candidate(new_interior(), x=12, y=5, width=4, height=6)
        identity = data["rooms"][-1]["id"]
        data = partition_candidate(data, identity, "vertical", 14, 6, 3)
        self.assertFalse(partition_cells(data, {"main"}))
        self.assertEqual(map_layers(data, {"main"}), map_layers(new_interior()))

    def test_old_designs_have_unchanged_schema_and_layers(self):
        data = new_interior()
        candidate = normalize_interior(data)
        self.assertNotIn("partitions", candidate)
        self.assertEqual(candidate, data)

    def test_opening_requires_a_walkable_floor_on_both_sides(self):
        with self.assertRaisesRegex(InteriorError, "both sides"):
            partition_candidate(new_interior(), "main", "vertical", 2, 6, 6)
        data = room_edit_candidate(new_interior(), x=12, y=5, width=4, height=6)
        self.assertTrue(partition_candidate(data, "main", "vertical", 11, 6, 4))

    def test_native_partition_tiles_match_rendered_and_exported_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "tiles.png"
            sheet = Image.new("RGBA", (64, 16))
            for index, color in enumerate(("red", "green", "blue", "yellow")):
                sheet.paste(color, (index*16, 0, index*16+16, 16))
            sheet.save(path)
            data = new_interior()
            data["atlas"] = import_atlas(path, root)
            data["style"].update(floor=0, wall_top=1, wall_middle=2, wall_bottom=3)
            data = partition_candidate(data, "main", "horizontal", 2, 9, 10)
            xml = ET.fromstring(interior_tmx(data))
            layers = map_layers(data)
            for name in layers:
                exported = [int(value) for value in xml.find(f"layer[@name='{name}']/data").text.split(",")]
                self.assertEqual(exported, layers[name])
            with render_interior(data, root) as image:
                self.assertEqual(image.getpixel((5*16+8, 7*16+8)), (0, 128, 0, 255))
                self.assertEqual(image.getpixel((6*16+8, 7*16+8)), (255, 0, 0, 255))
            compiled = compile_interior(data, "Test_Home", "Test", root, "assets/home/")
            self.assertTrue(compiled["files"])

    def test_optional_variant_never_exports_an_opening_into_missing_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGBA", (16, 16), "red").save(root / "tiles.png")
            data = new_interior()
            data["atlas"] = import_atlas(root / "tiles.png", root)
            data = room_edit_candidate(data, x=12, y=5, width=4, height=8)
            neighbor = data["rooms"][-1]["id"]
            data = partition_candidate(data, "main", "vertical", 11, 5, 8)
            compiled = compile_interior(data, "Test_Home", "Test", root, "assets/home/")
            self.assertEqual(len(compiled["runtime"]["variants"]), 1)
            self.assertIn(neighbor, compiled["runtime"]["variants"][0]["enabled_rooms"])

    def test_partition_frame_trims_faces_without_covering_the_opening(self):
        data = new_interior()
        data["atlas"].update(columns=8, tile_count=8)
        data["room_frame"] = {key: 0 for key in ROOM_FRAME_TILES}
        data["room_frame"].update(partition_vertical=4, partition_cap=5)
        data = partition_candidate(data, "main", "horizontal", 2, 9, 10)
        layers = map_layers(data)
        tile = lambda x, y: layers["Front"][y*data["width"]+x]
        self.assertEqual([tile(2, y) for y in (7, 8, 9)], [5, 5, 5])
        self.assertEqual(tile(3, 7), 6)
        self.assertEqual([tile(6, y) for y in (7, 8, 9)], [0, 0, 0])

    def test_malformed_wall_owner_is_reported_as_a_validation_error(self):
        data = partition_candidate(new_interior(), "main", "vertical", 7, 5, 8)
        data["partitions"][0]["room_id"] = []
        with self.assertRaises(InteriorError):
            normalize_interior(data)

    def test_spouse_floorplan_cannot_resize_or_partition(self):
        with self.assertRaisesRegex(InteriorError, "fixed"):
            resize_room_candidate(new_interior("spouse"), "main", 0, 3, 6, 6)
        with self.assertRaisesRegex(InteriorError, "residence"):
            partition_candidate(new_interior("spouse"), "main", "horizontal", 0, 6, 6)


if __name__ == "__main__":
    unittest.main()
