from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_architecture import architecture_candidate, remove_architecture_candidate
from pixelheart_core.interior_architecture_rules import architecture_rule_issues
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interior_layout import resize_room_candidate
from pixelheart_core.interior_levels import raised_room_candidate, set_room_enabled_candidate, stair_connection_clearance, validate_levels
from pixelheart_core.interiors import InteriorDraft, InteriorError, compile_interior, interior_tmx, new_interior, normalize_interior, room_edit_candidate


def base():
    result = new_interior()
    result["rooms"][0].update(y=13)
    result["entry"] = [4, 18]
    result["height"] = 24
    result["atlas"] = dict(asset="atlas.png", columns=2, tile_count=2)
    result["architecture_catalog"] = [dict(id="stardew.stairs", name="Wooden steps", category="Architecture", placement="floor",
                                           width=2, height=2, layers={"Back": [0]*4, "Buildings": [None]*4, "Front": [None]*4})]
    result["catalog"] = [validate_definition(dict(id="(F)rug", name="Rug", kind="rug", footprint=[1, 1], sprite_size=[1, 1], rotations=1))]
    return normalize_interior(result)


def raised(optional=False):
    return raised_room_candidate(base(), 4, 5, 4, 4, optional=optional)


def records(data):
    upper = next(room for room in data["rooms"] if room.get("level") == 1)
    connector = next(room for room in data["rooms"] if room.get("kind") == "stairway")
    return upper, connector, data["architecture"][0]


class InteriorLevelsTests(unittest.TestCase):
    def test_compound_add_links_distinct_floor_regions_atomically(self):
        original = base()
        result = raised_room_candidate(original, 4, 5, 4, 4)
        upper, connector, piece = records(result)
        self.assertEqual(len(original["rooms"]), 1)
        self.assertEqual((upper["x"], upper["y"], upper["level"]), (4, 5, 1))
        self.assertEqual((connector["x"], connector["y"], connector["width"], connector["height"]), (5, 9, 2, 4))
        self.assertEqual(connector["upper_room_id"], upper["id"])
        self.assertEqual(connector["lower_room_id"], "main")
        self.assertEqual((piece["x"], piece["y"], piece["room_id"]), (5, 10, connector["id"]))
        self.assertEqual(architecture_rule_issues(result), [])

    def test_raise_default_small_canvas_can_rebase_every_authored_coordinate(self):
        original = base()
        original["rooms"][0]["y"] = 5
        original["entry"] = [4, 10]
        result = raised_room_candidate(original, 4, -3, 4, 4, allow_rebase=True)
        upper, connector, _ = records(result)
        self.assertEqual(upper["y"], 4)
        self.assertEqual(result["rooms"][0]["y"], 12)
        self.assertEqual(result["entry"], [4, 17])
        with self.assertRaises(InteriorError):
            raised_room_candidate(original, 4, -3, 4, 4, allow_rebase=False)

    def test_snap_by_one_row_and_require_overlap_width(self):
        result = raised_room_candidate(base(), 4, 6, 4, 4)
        self.assertEqual(records(result)[0]["y"], 5)
        for x, y, width in ((4, 7, 4), (11, 5, 4), (4, 5, 1)):
            with self.subTest(values=(x, y, width)), self.assertRaises(InteriorError):
                raised_room_candidate(base(), x, y, width, 4)

    def test_steps_library_is_required_before_compound_add(self):
        original = base()
        original["architecture_catalog"] = []
        with self.assertRaisesRegex(InteriorError, "architectural library"):
            raised_room_candidate(original, 4, 5, 4, 4)

    def test_flat_stairs_are_readable_but_no_longer_valid(self):
        original = base()
        original["architecture"] = [dict(id="old", piece_id="stardew.stairs", room_id="main", x=7, y=15)]
        loaded = normalize_interior(original)
        self.assertEqual(loaded["architecture"], original["architecture"])
        self.assertEqual(architecture_rule_issues(loaded)[0]["code"], "stairs_connection")
        with self.assertRaisesRegex(InteriorError, "dedicated stairway"):
            architecture_candidate(base(), "stardew.stairs", 7, 15)

    def test_raise_without_connector_and_unsupported_levels_reject(self):
        for level in (1, 2, True):
            original = base()
            original["rooms"][0]["level"] = level
            with self.subTest(level=level), self.assertRaises(InteriorError):
                normalize_interior(original)

    def test_direct_mixed_level_bypass_rejects(self):
        result = raised()
        result["rooms"].append(dict(id="bypass", name="Bypass", x=2, y=5, width=2, height=8, optional=False, enabled=True))
        with self.assertRaisesRegex(InteriorError, "flat-floor bypass"):
            normalize_interior(result)

    def test_stair_neck_sides_cannot_be_inside_a_flat_room(self):
        result = raised()
        result["rooms"].append(dict(id="side", name="Side corridor", x=4, y=9, width=1, height=4, optional=False, enabled=True))
        with self.assertRaisesRegex(InteriorError, "sides of the stair passage"):
            normalize_interior(result)

    def test_stamp_cannot_be_removed_without_raised_room(self):
        result = raised()
        upper, connector, stamp = records(result)
        with self.assertRaisesRegex(InteriorError, "raised room"):
            remove_architecture_candidate(result, stamp["id"])
        with self.assertRaisesRegex(InteriorError, "raised room"):
            InteriorDraft(result).remove_room(connector["id"])

    def test_horizontal_stamp_drag_moves_connector_and_preserves_links(self):
        result = raised()
        upper, connector, stamp = records(result)
        moved = architecture_candidate(result, stamp["piece_id"], 6, stamp["y"], placement_id=stamp["id"])
        self.assertEqual(records(moved)[1]["x"], 6)
        self.assertEqual(records(moved)[0], upper)
        with self.assertRaises(InteriorError):
            architecture_candidate(result, stamp["piece_id"], 7, stamp["y"], placement_id=stamp["id"])
        with self.assertRaises(InteriorError):
            architecture_candidate(result, stamp["piece_id"], "invalid", stamp["y"], placement_id=stamp["id"])
        with self.assertRaisesRegex(InteriorError, "horizontally"):
            architecture_candidate(result, stamp["piece_id"], stamp["x"], stamp["y"]+1, placement_id=stamp["id"])

    def test_horizontal_raised_room_move_carries_whole_connection(self):
        result = raised()
        upper, connector, stamp = records(result)
        moved = room_edit_candidate(result, room_id=upper["id"], x=5, y=5, snap=False)
        self.assertEqual(records(moved)[1]["x"], connector["x"]+1)
        self.assertEqual(records(moved)[2]["x"], stamp["x"]+1)
        with self.assertRaises(InteriorError):
            room_edit_candidate(result, room_id=upper["id"], x=4, y=6, snap=False)

    def test_resize_may_keep_connection_but_cannot_break_it(self):
        result = raised()
        upper, _, _ = records(result)
        taller = resize_room_candidate(result, upper["id"], 4, 4, 4, 5)
        self.assertEqual(records(taller)[1], records(result)[1])
        with self.assertRaises(InteriorError):
            resize_room_candidate(result, upper["id"], 4, 5, 4, 5)

    def test_compound_remove_and_undo_restore_whole_connection(self):
        result = raised()
        draft = InteriorDraft(result)
        draft.remove_room(records(result)[0]["id"])
        self.assertEqual(len(draft.data["rooms"]), 1)
        self.assertEqual(draft.data["architecture"], [])
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data, result)
        self.assertTrue(draft.redo())
        self.assertEqual(len(draft.data["rooms"]), 1)

    def test_optional_raised_and_connector_toggle_together(self):
        result = raised(optional=True)
        upper, connector, _ = records(result)
        disabled = set_room_enabled_candidate(result, upper["id"], False)
        self.assertFalse(records(disabled)[0]["enabled"])
        self.assertFalse(records(disabled)[1]["enabled"])
        self.assertTrue(disabled["rooms"][0]["enabled"])
        restored = set_room_enabled_candidate(disabled, upper["id"], True)
        self.assertEqual(restored, result)
        with self.assertRaises(InteriorError):
            validate_levels(result, {"main", upper["id"]})
        with self.assertRaises(InteriorError):
            validate_levels(result, {"main", connector["id"]})

    def test_direct_tmx_export_rejects_an_orphaned_raised_variant(self):
        result = raised(optional=True)
        with self.assertRaises(InteriorError):
            interior_tmx(result, enabled={records(result)[0]["id"]})

    def test_full_inner_and_outer_landings_reject_furniture_including_rugs(self):
        result = raised()
        _, connector, stamp = records(result)
        clearance = stair_connection_clearance(result, stamp, result["architecture_catalog"][0])
        self.assertEqual(clearance, {(x, y) for x in (5, 6) for y in range(8, 14)})
        for x, y in ((5, 8), (6, 13), (5, 9), (6, 12)):
            draft = InteriorDraft(result)
            with self.subTest(point=(x, y)), self.assertRaisesRegex(InteriorError, "clear"):
                draft.place_furniture("(F)rug", x, y)

    def test_compile_only_offers_linked_optional_configurations_and_reserves_endpoints(self):
        result = raised(optional=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Image.new("RGBA", (32, 16), "red") as image:
                image.save(root / "atlas.png")
            compiled = compile_interior(result, "Home", "NPC", root, "maps/")
        self.assertEqual([variant["id"] for variant in compiled["runtime"]["variants"]], ["0", "3"])
        edits = next(patch["MapTiles"] for patch in compiled["patches"] if patch["Action"] == "EditMap" and patch["Target"].endswith("_3"))
        reserved = {(edit["Position"]["X"], edit["Position"]["Y"]) for edit in edits if edit["SetProperties"].get("NoFurniture") == "T"}
        self.assertTrue({(x, y) for x in (5, 6) for y in range(8, 14)} <= reserved)


if __name__ == "__main__":
    unittest.main()
