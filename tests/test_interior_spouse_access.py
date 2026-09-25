"""Spouse inserts join the farmhouse along their west side, below its pillar."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, interior_export_issues, interior_tmx,
    new_interior, normalize_interior, reachable_tiles, spouse_access_issues,
    spouse_entrance_tiles, validate_spouse_access,
)
from pixelheart_core.projects import load_project, new_project, save_project
from pixelheart_core.world import new_location, new_world


def design(kind="spouse"):
    data = new_interior(kind)
    data["catalog"] = [validate_definition({
        "id": "(F)Test.Blocker", "name": "Blocker", "kind": "chair",
        "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 2,
        "rotation_footprints": {"1": [1, 2]},
    }), validate_definition({
        "id": "(F)Test.Rug", "name": "Rug", "kind": "rug",
        "footprint": [6, 6], "sprite_size": [6, 6], "rotations": 1,
    })]
    return data


def blocked_legacy(*, double_barrier=False):
    data = design()
    for x in range(2 if double_barrier else 1):
        for y in range(3 if double_barrier else 4, 9):
            data["furniture"].append({"id": f"block-{x}-{y}", "item_id": "(F)Test.Blocker",
                                      "x": x, "y": y, "rotation": 0, "mod_data": {}})
    return data


class SpouseAccessTests(unittest.TestCase):
    def test_opening_excludes_the_first_floor_row_behind_the_pillar(self):
        self.assertEqual(spouse_entrance_tiles(design()), {(0, y) for y in range(4, 9)})
        self.assertEqual(spouse_entrance_tiles(design("residence")), set())
        self.assertNotIn((0, 3), spouse_entrance_tiles(design()))

    def test_closing_last_west_opening_rejects_atomically_despite_bottom_route(self):
        draft = InteriorDraft(design())
        for y in range(4, 8):
            draft.place_furniture("(F)Test.Blocker", 0, y)
        before = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "farmhouse opening on the left"):
            draft.place_furniture("(F)Test.Blocker", 0, 8)
        self.assertEqual(draft.snapshot(), before)
        self.assertTrue(draft.undo())
        self.assertEqual(len(draft.data["furniture"]), 3)
        self.assertTrue(draft.redo())
        self.assertEqual(draft.snapshot(), before)

    def test_any_one_opening_can_reach_the_stand(self):
        for open_y in range(4, 9):
            with self.subTest(open_y=open_y):
                draft = InteriorDraft(design())
                for y in range(3, 9):
                    if y != open_y:
                        draft.place_furniture("(F)Test.Blocker", 0, y)
                self.assertIn(tuple(draft.data["spouse_stand"]), reachable_tiles(draft.data))
                validate_spouse_access(draft.data)

    def test_separate_west_openings_seed_both_sides_of_a_horizontal_barrier(self):
        draft = InteriorDraft(design())
        for x in range(6):
            draft.place_furniture("(F)Test.Blocker", x, 6)
        reached = reachable_tiles(draft.data)
        self.assertIn((3, 5), reached)
        self.assertIn((3, 8), reached)

    def test_open_west_edge_does_not_excuse_an_isolated_standing_tile(self):
        draft = InteriorDraft(design())
        for point in ((2, 5), (4, 5), (3, 4)):
            draft.place_furniture("(F)Test.Blocker", *point)
        before = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.place_furniture("(F)Test.Blocker", 3, 6)
        self.assertEqual(draft.snapshot(), before)

    def test_old_bottom_entry_is_usable_but_standing_tile_stays_clear(self):
        draft = InteriorDraft(design())
        draft.place_furniture("(F)Test.Blocker", *draft.data["entry"])
        self.assertNotIn(tuple(draft.data["entry"]), reachable_tiles(draft.data))
        validate_spouse_access(draft.data)
        with self.assertRaisesRegex(InteriorError, "clear"):
            draft.place_furniture("(F)Test.Blocker", *draft.data["spouse_stand"])

    def test_rugs_do_not_block_openings_or_the_spouse(self):
        draft = InteriorDraft(design())
        draft.place_furniture("(F)Test.Rug", 0, 3)
        for y in range(4, 8):
            draft.place_furniture("(F)Test.Blocker", 0, y)
        self.assertEqual(spouse_access_issues(draft.data), [])
        self.assertIn((0, 8), reachable_tiles(draft.data))

    def test_moves_rotations_and_stand_edits_use_the_same_access_rule(self):
        draft = InteriorDraft(design())
        for y in range(4, 7):
            draft.place_furniture("(F)Test.Blocker", 0, y)
        tall = draft.place_furniture("(F)Test.Blocker", 0, 7)
        before = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.rotate_furniture(tall)
        movable = draft.place_furniture("(F)Test.Blocker", 4, 8)
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.move_furniture(movable, 0, 8)
        self.assertEqual(draft.data["furniture"][:-1], before["furniture"])

        isolated = InteriorDraft(design())
        for point in ((2, 6), (4, 6), (3, 5), (3, 7)):
            candidate = isolated.snapshot()
            candidate["spouse_stand"] = [2, 4]
            isolated.apply(candidate)
            isolated.place_furniture("(F)Test.Blocker", *point)
        candidate = isolated.snapshot()
        candidate["spouse_stand"] = [3, 6]
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            isolated.apply(candidate)

    def test_legacy_blocked_project_reopens_without_migration_but_cannot_export(self):
        legacy = blocked_legacy()
        original = deepcopy(legacy)
        self.assertEqual(normalize_interior(legacy), original)
        self.assertEqual(InteriorDraft(legacy).snapshot(), original)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "character.json"
            document = new_project()
            place = new_location()
            place.update(spouse_room=True, interior=legacy)
            document["world"] = new_world()
            document["world"]["locations"].append(place)
            save_project(document, path)
            loaded = load_project(path)["world"]["locations"][0]["interior"]
            self.assertEqual(loaded, original)
            self.assertIn("farmhouse opening on the left", interior_export_issues(loaded, path.parent)[0]["message"])
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            interior_tmx(legacy, design_id="Spouse")
        self.assertEqual(legacy, original)

    def test_legacy_repairs_are_incremental_and_undoable_without_new_blockers(self):
        legacy = blocked_legacy(double_barrier=True)
        draft = InteriorDraft(legacy)
        unrelated = draft.snapshot()
        unrelated["rooms"][0]["name"] = "Renamed room"
        draft.apply(unrelated)
        draft.remove_furniture("block-0-4")
        self.assertTrue(spouse_access_issues(draft.data))
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.place_furniture("(F)Test.Blocker", 4, 8)
        draft.remove_furniture("block-1-4")
        self.assertEqual(spouse_access_issues(draft.data), [])
        self.assertTrue(draft.undo())
        self.assertTrue(spouse_access_issues(draft.data))
        self.assertTrue(draft.redo())
        validate_spouse_access(draft.data)

    def test_architecture_collision_also_blocks_the_farmhouse_approach(self):
        data = design()
        data["atlas"] = {"asset": "atlas.png", "columns": 1, "tile_count": 1}
        data["architecture_catalog"] = [{
            "id": "block", "name": "Block", "category": "Architecture", "placement": "floor",
            "width": 1, "height": 5,
            "layers": {"Back": [None]*5, "Buildings": [0]*5, "Front": [None]*5},
        }]
        draft = InteriorDraft(data)
        candidate = draft.snapshot()
        candidate["architecture"] = [{"id": "fixture", "piece_id": "block", "room_id": "main", "x": 0, "y": 4}]
        with self.assertRaisesRegex(InteriorError, "walkable route"):
            draft.apply(candidate)

    def test_residence_keeps_its_own_entry_and_collision_rules(self):
        data = design("residence")
        draft = InteriorDraft(data)
        self.assertIn(tuple(data["entry"]), reachable_tiles(data))
        self.assertEqual(spouse_access_issues(data), [])
        with self.assertRaisesRegex(InteriorError, "clear"):
            draft.place_furniture("(F)Test.Blocker", *data["entry"])


if __name__ == "__main__":
    unittest.main()
