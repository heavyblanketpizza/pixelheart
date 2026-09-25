from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_architecture import architecture_candidate, remove_architecture_candidate, validate_architecture_definition
from pixelheart_core.interior_architecture_rules import (
    architecture_clearance_cells, architecture_rule, architecture_rule_hint,
    architecture_rule_issues, validate_architecture_rules,
)
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interior_layout import partition_candidate, remove_partition_candidate
from pixelheart_core.interior_levels import raised_room_candidate
from pixelheart_core.interiors import InteriorDraft, InteriorError, compile_interior, interior_export_issues, interior_tmx, new_interior, normalize_interior


def piece(identity, width=1, height=2, *, wall=False, rules=None, blocking=True):
    size = width*height
    result = dict(id=identity, name=identity.rsplit('.', 1)[-1], category="Architecture",
                  placement="wall" if wall else "floor", width=width, height=height,
                  layers={"Back": [0]*size if not blocking else [None]*size,
                          "Buildings": [None]*(size-width)+[1]*width if blocking else [None]*size,
                          "Front": [2]*width+[None]*(size-width) if blocking else [None]*size})
    if rules is not None:
        result["rules"] = rules
    return result


def data():
    result = new_interior()
    result["atlas"] = dict(asset="atlas.png", columns=4, tile_count=4)
    result["architecture_catalog"] = [piece("stardew.stairs", 2, 2, blocking=False),
                                      piece("free", 1, 1),
                                      piece("stardew.counter"), piece("stardew.bar-counter"),
                                      piece("stardew.bar-return", height=3),
                                      piece("stardew.work-counter", width=2),
                                      piece("stardew.dish-cupboard", 2, 2, wall=True, blocking=False),
                                      piece("stardew.wall-cabinet", 1, 1, wall=True, blocking=False),
                                      piece("stardew.bookcase", 2, 4),
                                      piece("stardew.brick-hearth", 2, 5),
                                      piece("stardew.timber-column", 1, 4)]
    result["catalog"] = [validate_definition(dict(id="(F)rug", name="Rug", kind="rug", footprint=[1, 1], sprite_size=[1, 1], rotations=1)),
                         validate_definition(dict(id="(F)chair", name="Chair", kind="chair", footprint=[1, 1], sprite_size=[1, 1], rotations=1))]
    return normalize_interior(result)


def corridor():
    result = partition_candidate(data(), "main", "vertical", 6, 6, 4, opening_width=0)
    return partition_candidate(result, "main", "vertical", 9, 6, 4, opening_width=0)


def raised():
    result = data()
    result["rooms"][0]["y"] = 10
    result["entry"] = [4, 15]
    return raised_room_candidate(result, 4, 4, 8, 2)


def legacy():
    result = data()
    result["architecture"] = [dict(id="old-steps", piece_id="stardew.stairs", x=7, y=7, room_id="main")]
    return normalize_interior(result)


class ArchitectureRulesTests(unittest.TestCase):
    def test_spouse_fixture_approach_uses_west_access_when_bottom_entry_is_blocked(self):
        result = new_interior("spouse")
        result["atlas"] = dict(asset="atlas.png", columns=4, tile_count=4)
        result["catalog"] = data()["catalog"]
        result["architecture_catalog"] = [piece("stardew.bookcase")]
        result = architecture_candidate(result, "stardew.bookcase", 2, 2)
        draft = InteriorDraft(result)
        draft.place_furniture("(F)chair", 3, 8)
        for x in range(6):
            draft.place_furniture("(F)chair", x, 6)
        self.assertEqual(architecture_rule_issues(draft.data), [])
        validate_architecture_rules(draft.data)

    def test_known_native_identity_cannot_bypass_profile(self):
        for rules in (None, "free", "wall_art"):
            value = piece("stardew.stairs", 2, 2, blocking=False, rules=rules)
            self.assertEqual(architecture_rule(value), "steps_corridor")
        self.assertIn("raised", architecture_rule_hint(value))

    def test_rule_metadata_is_validated_and_preserved(self):
        value = piece("custom", rules="steps_corridor")
        self.assertEqual(validate_architecture_definition(value, 4)["rules"], "steps_corridor")
        for invalid in ("warp", [], True):
            with self.subTest(invalid=invalid), self.assertRaises(InteriorError):
                validate_architecture_definition({**value, "rules": invalid}, 4)

    def test_stairs_reject_open_floor(self):
        with self.assertRaisesRegex(InteriorError, "raised"):
            architecture_candidate(data(), "stardew.stairs", 7, 7)

    def test_stairs_connect_a_raised_room_with_clear_landings(self):
        result = raised()
        self.assertEqual(architecture_rule_issues(result), [])
        item = result["architecture"][0]
        self.assertEqual(architecture_clearance_cells(item, result["architecture_catalog"][0]),
                         {(x, y) for x in (7, 8) for y in (6, 7, 8, 9)})

    def test_stairs_reject_missing_one_side_or_truncated_landings(self):
        result = partition_candidate(data(), "main", "vertical", 6, 6, 4, opening_width=0)
        with self.assertRaisesRegex(InteriorError, "raised"):
            architecture_candidate(result, "stardew.stairs", 7, 7)
        result = corridor()
        with self.assertRaises(InteriorError):
            architecture_candidate(result, "stardew.stairs", 7, 11)

    def test_stairs_reject_a_flat_narrow_hall_even_with_solid_sides(self):
        result = data()
        result["rooms"] = [dict(id="main", name="Hall", x=7, y=5, width=2, height=8, optional=False, enabled=True)]
        result["entry"] = [7, 11]
        result = normalize_interior(result)
        with self.assertRaisesRegex(InteriorError, "raised"):
            architecture_candidate(result, "stardew.stairs", 7, 7)

    def test_stairs_reserve_treads_and_both_landings_against_rugs(self):
        for point in ((7, 6), (7, 7), (8, 8), (8, 9)):
            draft = InteriorDraft(raised())
            before = draft.snapshot()
            with self.subTest(point=point), self.assertRaisesRegex(InteriorError, "including rugs"):
                draft.place_furniture("(F)rug", *point)
            self.assertEqual(draft.data, before)

    def test_canvas_edge_cannot_substitute_for_a_missing_stair_side_wall(self):
        for x in (0, 22):
            result = data()
            result["rooms"] = [dict(id="main", name="Hall", x=x, y=5, width=2, height=8,
                                    optional=False, enabled=True)]
            result["entry"] = [x, 11]
            result = normalize_interior(result)
            with self.subTest(x=x), self.assertRaises(InteriorError):
                architecture_candidate(result, "stardew.stairs", x, 7)

    def test_stairs_placement_rejects_existing_rug(self):
        original = data()
        original["rooms"][0]["y"] = 10
        original["entry"] = [4, 15]
        draft = InteriorDraft(original)
        draft.place_furniture("(F)rug", 7, 10)
        with self.assertRaisesRegex(InteriorError, "including rugs"):
            raised_room_candidate(draft.data, 7, 4, 2, 2)

    def test_stairs_landing_rejects_new_static_blocker(self):
        result = raised()
        with self.assertRaises(InteriorError):
            architecture_candidate(result, "free", 7, 9)

    def test_removing_the_stair_connection_is_rejected_atomically(self):
        draft = InteriorDraft(raised())
        before = draft.snapshot()
        with self.assertRaises(InteriorError):
            remove_architecture_candidate(before, before["architecture"][0]["id"])
        self.assertEqual(draft.data, before)

    def test_legacy_invalid_stairs_open_without_silent_movement(self):
        original = legacy()
        draft = InteriorDraft(original)
        self.assertEqual(draft.data, original)
        issues = architecture_rule_issues(draft.data)
        self.assertEqual(issues[0]["code"], "stairs_connection")
        self.assertEqual(issues[0]["placement_id"], "old-steps")
        self.assertTrue(issues[0]["cells"])
        with self.assertRaises(InteriorError):
            validate_architecture_rules(original)

    def test_unrelated_edit_preserves_legacy_issue_for_individual_repair(self):
        draft = InteriorDraft(legacy())
        draft.apply(architecture_candidate(draft.data, "free", 3, 6))
        self.assertEqual(draft.data["architecture"][0]["id"], "old-steps")
        draft.place_furniture("(F)chair", 10, 10)
        self.assertTrue(architecture_rule_issues(draft.data))

    def test_moving_invalid_piece_to_another_invalid_position_is_rejected(self):
        with self.assertRaises(InteriorError):
            architecture_candidate(legacy(), "stardew.stairs", 6, 7, placement_id="old-steps")

    def test_isolated_wall_strips_do_not_repair_flat_floor_stairs(self):
        draft = InteriorDraft(legacy())
        draft.apply(partition_candidate(draft.data, "main", "vertical", 6, 6, 4, opening_width=0))
        self.assertEqual(architecture_rule_issues(draft.data)[0]["code"], "stairs_connection")
        draft.apply(partition_candidate(draft.data, "main", "vertical", 9, 6, 4, opening_width=0))
        self.assertEqual(architecture_rule_issues(draft.data)[0]["code"], "stairs_connection")
        with self.assertRaisesRegex(InteriorError, "raised"):
            interior_tmx(draft.data)
        self.assertTrue(draft.undo())
        self.assertTrue(architecture_rule_issues(draft.data))
        self.assertTrue(draft.redo())
        self.assertTrue(architecture_rule_issues(draft.data))
        draft.apply(remove_architecture_candidate(draft.data, "old-steps"))
        self.assertFalse(architecture_rule_issues(draft.data))

    def test_new_clearance_cells_are_rejected_even_with_same_old_issue_code(self):
        original = legacy()
        original["furniture"] = [dict(id="old-rug", item_id="(F)rug", x=7, y=6, rotation=0, mod_data={})]
        draft = InteriorDraft(original)
        with self.assertRaisesRegex(InteriorError, "including rugs"):
            draft.place_furniture("(F)rug", 8, 9)

    def test_wall_backed_fixtures_use_first_floor_row(self):
        result = architecture_candidate(data(), "stardew.counter", 3, 4)
        self.assertEqual(architecture_rule_issues(result), [])
        with self.assertRaisesRegex(InteriorError, "first floor row"):
            architecture_candidate(data(), "stardew.counter", 3, 7)
        result = architecture_candidate(data(), "stardew.bookcase", 7, 2)
        self.assertEqual(architecture_rule_issues(result), [])

    def test_wall_art_stays_above_lower_trim(self):
        self.assertFalse(architecture_rule_issues(architecture_candidate(data(), "stardew.dish-cupboard", 3, 2)))
        with self.assertRaisesRegex(InteriorError, "upper wall"):
            architecture_candidate(data(), "stardew.dish-cupboard", 3, 3)
        self.assertFalse(architecture_rule_issues(architecture_candidate(data(), "stardew.wall-cabinet", 3, 3)))
        with self.assertRaisesRegex(InteriorError, "upper wall"):
            architecture_candidate(data(), "stardew.wall-cabinet", 3, 4)

    def test_hearth_requires_wall_and_ceiling_cap_backing(self):
        result = architecture_candidate(data(), "stardew.brick-hearth", 7, 1)
        self.assertEqual(architecture_rule_issues(result), [])
        with self.assertRaisesRegex(InteriorError, "first floor row"):
            architecture_candidate(data(), "stardew.brick-hearth", 7, 2)

    def test_wall_backed_fixtures_mount_on_solid_partition_faces(self):
        result = partition_candidate(data(), "main", "horizontal", 2, 8, 10, opening_width=2)
        counter = architecture_candidate(result, "stardew.counter", 3, 8)
        self.assertFalse(architecture_rule_issues(counter))
        hearth = architecture_candidate(result, "stardew.brick-hearth", 2, 5)
        self.assertFalse(architecture_rule_issues(hearth))
        with self.assertRaises(InteriorError):
            architecture_candidate(result, "stardew.counter", 6, 8)
        with self.assertRaises(InteriorError):
            architecture_candidate(result, "stardew.brick-hearth", 5, 5)

    def test_column_is_wall_attached_instead_of_floating(self):
        self.assertFalse(architecture_rule_issues(architecture_candidate(data(), "stardew.timber-column", 7, 2)))
        with self.assertRaisesRegex(InteriorError, "first floor row"):
            architecture_candidate(data(), "stardew.timber-column", 7, 6)

    def test_wall_post_does_not_reserve_an_interactive_front_approach(self):
        draft = InteriorDraft(architecture_candidate(data(), "stardew.timber-column", 7, 2))
        draft.place_furniture("(F)chair", 7, 6)
        self.assertFalse(architecture_rule_issues(draft.data))
        definition = next(piece for piece in draft.data["architecture_catalog"] if piece["id"] == "stardew.timber-column")
        self.assertEqual(architecture_clearance_cells(draft.data["architecture"][0], definition), set())

    def test_optional_variants_keep_raised_room_and_stairs_together(self):
        result = raised()
        linked = {room["id"] for room in result["rooms"] if room["id"] != "main"}
        for room in result["rooms"]:
            if room["id"] in linked:
                room["optional"] = True
        self.assertFalse(architecture_rule_issues(result))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Image.new("RGBA", (64, 16), "red") as image:
                image.save(root / "atlas.png")
            compiled = compile_interior(result, "Home", "NPC", root, "maps/")
        self.assertEqual({frozenset(variant["enabled_rooms"]) for variant in compiled["runtime"]["variants"]},
                         {frozenset({"main"}), frozenset({"main"} | linked)})

    def test_rug_can_remain_in_wall_fixture_approach(self):
        draft = InteriorDraft(architecture_candidate(data(), "stardew.counter", 3, 4))
        draft.place_furniture("(F)rug", 3, 6)
        self.assertEqual(architecture_rule_issues(draft.data), [])
        with self.assertRaisesRegex(InteriorError, "approach"):
            draft.place_furniture("(F)chair", 3, 6)

    def test_bar_return_needs_run_on_right_with_matching_base(self):
        with self.assertRaisesRegex(InteriorError, "immediately on its right"):
            architecture_candidate(data(), "stardew.bar-return", 6, 7)
        result = architecture_candidate(data(), "stardew.bar-counter", 7, 8)
        result = architecture_candidate(result, "stardew.bar-return", 6, 7)
        self.assertFalse(architecture_rule_issues(result))
        with self.assertRaisesRegex(InteriorError, "immediately on its right"):
            remove_architecture_candidate(result, result["architecture"][0]["id"])

    def test_bar_run_has_clear_staff_and_serving_approaches(self):
        self.assertFalse(architecture_rule_issues(architecture_candidate(data(), "stardew.bar-counter", 7, 8)))
        with self.assertRaisesRegex(InteriorError, "staff approach"):
            architecture_candidate(data(), "stardew.bar-counter", 7, 5)

    def test_work_counter_end_has_supported_left_edge(self):
        self.assertFalse(architecture_rule_issues(architecture_candidate(data(), "stardew.work-counter", 2, 4)))
        with self.assertRaisesRegex(InteriorError, "left edge"):
            architecture_candidate(data(), "stardew.work-counter", 7, 4)
        result = architecture_candidate(data(), "stardew.counter", 6, 4)
        self.assertFalse(architecture_rule_issues(architecture_candidate(result, "stardew.work-counter", 7, 4)))

    def test_strict_export_reports_legacy_invalid_piece(self):
        self.assertIn("raised", interior_export_issues(legacy(), Path("/tmp"))[0]["message"])
        with self.assertRaisesRegex(InteriorError, "raised"):
            interior_tmx(legacy())

    def test_compile_reserves_stair_treads_and_landings(self):
        result = raised()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Image.new("RGBA", (64, 16), "red") as image:
                image.save(root / "atlas.png")
            compiled = compile_interior(result, "Home", "NPC", root, "maps/")
        edits = compiled["patches"][1]["MapTiles"]
        reserved = {(edit["Position"]["X"], edit["Position"]["Y"]) for edit in edits
                    if edit["SetProperties"].get("NoFurniture") == "T"}
        self.assertTrue({(x, y) for x in (7, 8) for y in (6, 7, 8, 9)} <= reserved)


if __name__ == "__main__":
    unittest.main()
