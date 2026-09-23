"""A residence doorway is structural map geometry with a separate return warp."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET

from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, compile_interior, doorway_exit, ensure_doorway,
    floor_cells, map_layers, new_interior, normalize_interior, place_doorway,
    reachable_tiles, render_interior, room_edit_candidate,
)
from pixelheart_core.world import exported_location_id, world_issues
from tests import test_interior_exporting as export_fixture


class InteriorDoorwayTests(unittest.TestCase):
    def test_legacy_opening_keeps_arrival_and_does_not_mutate_saved_design(self):
        original = new_interior()
        before = deepcopy(original)
        candidate = ensure_doorway(original)
        self.assertEqual(original, before)
        self.assertEqual(candidate["entry"], before["entry"])
        self.assertEqual(candidate["doorway"], [4, 12])
        self.assertEqual(doorway_exit(candidate), (4, 13))
        self.assertEqual(ensure_doorway(candidate), candidate)

    def test_explicit_placement_pairs_inward_arrival_and_outside_exit(self):
        candidate = place_doorway(new_interior(), 7, 12)
        self.assertEqual(candidate["entry"], [7, 11])
        self.assertEqual(doorway_exit(candidate), (7, 13))
        self.assertNotIn((7, 13), floor_cells(candidate))
        self.assertIn((7, 13), reachable_tiles(candidate))

    def test_invalid_edges_and_spouse_room_reject_without_mutation(self):
        original = new_interior()
        for x, y in ((2, 12), (11, 12), (4, 11), (4, 5), (1, 12)):
            with self.subTest(x=x, y=y), self.assertRaises(InteriorError):
                place_doorway(original, x, y)
        self.assertEqual(original, new_interior())
        self.assertEqual(ensure_doorway(new_interior("spouse")), new_interior("spouse"))
        with self.assertRaisesRegex(InteriorError, "farmhouse"):
            place_doorway(new_interior("spouse"), 3, 8)

    def test_unsuitable_legacy_layout_remains_readable(self):
        data = new_interior()
        data["rooms"][0].update(width=2)
        data["entry"] = [2, 10]
        self.assertEqual(ensure_doorway(data), data)
        data = new_interior()
        data["height"] = 13
        self.assertEqual(ensure_doorway(data), data)

    def test_furniture_cannot_block_threshold_approach_or_use_outside_passage(self):
        data = place_doorway(new_interior(), 4, 12)
        data["catalog"] = [validate_definition({"id": "(F)Chair", "name": "Chair", "kind": "chair",
                                                  "footprint": [1, 1], "rotations": 1})]
        draft = InteriorDraft(data)
        for x, y in ((4, 11), (4, 12), (4, 13)):
            with self.subTest(y=y), self.assertRaises(InteriorError):
                draft.place_furniture("(F)Chair", x, y)
        self.assertEqual(draft.data, data)
        self.assertFalse(draft.undo())

    def test_disconnected_legacy_arrival_cannot_gain_unreachable_door(self):
        data = new_interior()
        data["entry"] = [4, 7]
        data["catalog"] = [validate_definition({"id": "(F)Chair", "name": "Chair", "kind": "chair",
                                                  "footprint": [1, 1], "rotations": 1})]
        data["furniture"] = [dict(id=str(x), item_id="(F)Chair", x=x, y=8, rotation=0)
                             for x in range(2, 12)]
        self.assertEqual(ensure_doorway(data), normalize_interior(data))
        data["doorway"] = [4, 12]
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            normalize_interior(data)

    def test_room_move_and_rebase_carry_doorway_and_undo_restores(self):
        data = place_doorway(new_interior(), 4, 12)
        draft = InteriorDraft(data)
        moved = room_edit_candidate(data, room_id="main", x=4, y=6, snap=False)
        self.assertEqual(moved["doorway"], [6, 13])
        self.assertEqual(moved["entry"], [6, 12])
        self.assertEqual(doorway_exit(moved), (6, 14))
        draft.apply(moved)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data, data)
        rebased = room_edit_candidate(data, x=-4, y=5, width=6, height=6, allow_rebase=True)
        self.assertEqual(rebased["doorway"], [9, 12])

    def test_room_below_doorway_cannot_seal_passage(self):
        data = place_doorway(new_interior(), 4, 12)
        with self.assertRaisesRegex(InteriorError, "clear bottom edge"):
            room_edit_candidate(data, x=3, y=13, width=4, height=4, snap=False)

    def test_jamb_cannot_cover_an_adjacent_rooms_wall(self):
        data = new_interior()
        data["rooms"].append(dict(id="branch", name="Branch", x=5, y=13, width=4, height=4,
                                  optional=True, enabled=True))
        # The middle passage is empty but its right jamb would overlap the branch.
        with self.assertRaisesRegex(InteriorError, "clear bottom edge"):
            place_doorway(data, 4, 12)

    def test_floor_extension_is_drawn_and_passable_without_room_frame(self):
        data = place_doorway(new_interior(), 4, 12)
        data["atlas"]["tile_count"] = 1
        layers = map_layers(data)
        for y in (12, 13):
            index = y * data["width"] + 4
            self.assertEqual(layers["Back"][index], 1)
            self.assertEqual(layers["Buildings"][index], 0)
            self.assertEqual(layers["Front"][index], 0)
        self.assertNotEqual(layers["Buildings"][13 * data["width"] + 3], 0)
        with render_interior(data, ".") as preview:
            self.assertEqual(preview.getpixel((4 * 16 + 6, 13 * 16 + 6)),
                             preview.getpixel((4 * 16 + 6, 12 * 16 + 6)))


class InteriorDoorwayExportTests(unittest.TestCase):
    setUp = export_fixture.InteriorExportTests.setUp
    location = export_fixture.InteriorExportTests.location
    compile = export_fixture.InteriorExportTests.compile
    runtime = staticmethod(export_fixture.InteriorExportTests.runtime)

    def test_export_uses_passage_warp_despite_stale_location_exit(self):
        self.residence["interior"] = place_doorway(self.residence["interior"], 4, 12)
        self.residence.update(entry_x=4, entry_y=11, exit_x=4, exit_y=11)
        before = deepcopy(self.document)
        errors = [issue for issue in world_issues(self.document["world"], self.character, self.project_root)
                  if issue["level"] == "error"]
        self.assertEqual(errors, [])
        compiled = self.compile()
        identity = exported_location_id(self.residence, self.character)
        runtime = self.runtime(compiled)[identity]
        targets = {target.strip() for patch in compiled["patches"]
                   if "4 13 Town 32 63" in patch.get("AddWarps", [])
                   for target in patch["Target"].split(",")}
        self.assertEqual(targets, {"Maps/" + identity, *(variant["map_asset"] for variant in runtime["variants"])})
        self.assertIn([4, 13], runtime["protected_tiles"])
        self.assertIn([4, 12], runtime["protected_tiles"])
        passage_patches = {patch["Target"]: edit["SetProperties"]
                           for patch in compiled["patches"] for edit in patch.get("MapTiles", [])
                           if edit["Layer"] == "Back" and edit["Position"] == {"X": 4, "Y": 13}}
        self.assertEqual(passage_patches, {target: {"NoFurniture": "T", "FloorID": identity + "_main"}
                                           for target in targets})
        self.assertEqual(self.document, before)
        for patch in compiled["patches"]:
            if patch["Action"] == "Load" and patch["Target"] in targets:
                xml = ET.fromstring(compiled["files"][patch["FromFile"]])
                values = [int(value) for value in xml.find("layer[@name='Buildings']/data").text.split(",")]
                self.assertEqual(values[13 * 24 + 4], 0)

    def test_optional_variants_cannot_remove_doorway_while_leaving_arrival(self):
        data = deepcopy(self.residence["interior"])
        data["rooms"].append(dict(id="porch", name="Porch", x=4, y=13, width=4, height=4,
                                  optional=True, enabled=True))
        data["doorway"] = [5, 16]
        data = normalize_interior(data)
        compiled = compile_interior(data, "House", "Mira", self.project_root, "maps/")
        self.assertEqual(len(compiled["runtime"]["variants"]), 2)
        self.assertTrue(all("porch" in variant["enabled_rooms"] for variant in compiled["runtime"]["variants"]))


if __name__ == "__main__":
    unittest.main()
