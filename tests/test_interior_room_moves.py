"""Room drops preserve authored contents and reject invalid house layouts."""
from copy import deepcopy
import unittest

from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, new_interior, normalize_interior, room_edit_candidate,
)


def design():
    data = new_interior()
    data["rooms"].append({"id": "study", "name": "Study", "x": 12, "y": 5,
                          "width": 4, "height": 4, "optional": True, "enabled": True})
    data["room_styles"] = {"study": {"floor": 0}}
    data["catalog"] = [validate_definition({"id": "(F)" + identity, "name": identity, "kind": kind,
                                           "footprint": size, "sprite_size": size, "rotations": 1})
                       for identity, kind, size in (("chair", "chair", [1, 1]),
                                                    ("window", "window", [1, 2]),
                                                    ("rug", "rug", [2, 1]),
                                                    ("painting", "painting", [2, 1]))]
    return data


def item(identity, kind, x, y):
    return dict(id=identity, item_id="(F)" + kind, x=x, y=y, rotation=0, mod_data={"variant": "blue"})


class InteriorRoomMoveTests(unittest.TestCase):
    def test_drop_carries_room_contents_entry_and_style_in_one_undo(self):
        data = design()
        data["entry"] = [13, 8]
        data["furniture"] = [item("seat", "chair", 13, 6), item("glass", "window", 13, 2),
                             item("other", "chair", 5, 7)]
        draft = InteriorDraft(data)
        before = draft.snapshot()
        candidate = room_edit_candidate(draft.data, room_id="study", x=12, y=9)
        self.assertEqual(draft.snapshot(), before)
        self.assertFalse(draft.undo())
        self.assertEqual(candidate["rooms"][0], before["rooms"][0])
        self.assertEqual(candidate["room_styles"], before["room_styles"])
        self.assertEqual(candidate["entry"], [13, 12])
        self.assertEqual(candidate["furniture"], [item("seat", "chair", 13, 10),
                                                 item("glass", "window", 13, 6),
                                                 item("other", "chair", 5, 7)])
        self.assertTrue(draft.apply(candidate))
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), before)
        self.assertFalse(draft.undo())
        self.assertTrue(draft.redo())
        self.assertEqual(draft.snapshot(), candidate)

    def test_low_legacy_window_keeps_room_relative_height_after_move(self):
        data = design()
        data["furniture"] = [item("legacy", "window", 13, 3)]
        draft = InteriorDraft(data)
        candidate = room_edit_candidate(data, room_id="study", x=12, y=9)
        draft.apply(candidate)
        self.assertEqual(draft.data["furniture"][0]["y"], 7)
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.move_furniture("legacy", 14, 7)
        self.assertEqual(draft.snapshot(), candidate)

    def test_move_main_room_carries_anchors_and_can_grow_canvas(self):
        draft = InteriorDraft()
        candidate = room_edit_candidate(draft.data, room_id="main", x=80, y=70)
        self.assertEqual(candidate["entry"], [82, 75])
        self.assertEqual(candidate["spouse_stand"], [81, 70])
        self.assertEqual((candidate["width"], candidate["height"]), (91, 79))
        draft.apply(candidate)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), new_interior())

    def test_room_drop_snaps_one_tile_gap_and_overlap_to_join(self):
        for x in (11, 13):
            with self.subTest(x=x):
                candidate = room_edit_candidate(new_interior(), x=x, y=5, width=4, height=4)
                self.assertEqual(candidate["rooms"][-1]["x"], 12)
        candidate = room_edit_candidate(new_interior(), x=3, y=14, width=4, height=4)
        self.assertEqual(candidate["rooms"][-1]["y"], 13)

    def test_valid_exact_drop_does_not_shift_along_shared_edge(self):
        data = design()
        candidate = room_edit_candidate(data, room_id="study", x=12, y=8)
        self.assertEqual((candidate["rooms"][1]["x"], candidate["rooms"][1]["y"]), (12, 8))
        self.assertEqual(data, design())

    def test_floor_and_wall_furniture_across_room_join_are_not_silently_split(self):
        for kind, y in (("rug", 6), ("painting", 2)):
            with self.subTest(kind=kind):
                data = design()
                data["furniture"] = [item("across", kind, 11, y)]
                before = normalize_interior(data)
                with self.assertRaisesRegex(InteriorError, "spanning multiple rooms"):
                    room_edit_candidate(data, room_id="study", x=12, y=9)
                self.assertEqual(data, before)

    def test_drop_back_in_place_is_noop_even_with_furniture_across_join(self):
        data = design()
        data["furniture"] = [item("across", "rug", 11, 6)]
        draft = InteriorDraft(data)
        candidate = room_edit_candidate(draft.data, room_id="study", x=12, y=5)
        self.assertFalse(draft.apply(candidate))
        self.assertFalse(draft.undo())

    def test_wall_pieces_cannot_be_carried_into_neighboring_floor(self):
        data = design()
        data["furniture"] = [item("glass", "window", 13, 2)]
        with self.assertRaisesRegex(InteriorError, "wall or floor"):
            room_edit_candidate(data, room_id="study", x=5, y=13, snap=False)

    def test_invalid_drop_preserves_draft_and_redo(self):
        draft = InteriorDraft(design())
        draft.apply(room_edit_candidate(draft.data, room_id="study", x=12, y=9))
        moved = draft.snapshot()
        draft.undo()
        before = draft.snapshot()
        for x, y in ((11, 5), (30, 5)):
            with self.subTest(position=(x, y)), self.assertRaises(InteriorError):
                room_edit_candidate(draft.data, room_id="study", x=x, y=y, snap=False)
            self.assertEqual(draft.snapshot(), before)
        self.assertTrue(draft.redo())
        self.assertEqual(draft.snapshot(), moved)

    def test_moving_connector_cannot_strand_another_room(self):
        data = design()
        data["rooms"].append({"id": "far", "name": "Far room", "x": 16, "y": 5,
                              "width": 4, "height": 4, "optional": True, "enabled": True})
        with self.assertRaisesRegex(InteriorError, "connected"):
            room_edit_candidate(data, room_id="study", x=4, y=13, snap=False)

    def test_saved_room_coordinates_stay_fixed_and_rebase_is_explicit(self):
        for x, y, width, height in ((-2, 5, 4, 4), (2, 1, 4, 4)):
            with self.subTest(position=(x, y)):
                data = new_interior()
                before = deepcopy(data)
                with self.assertRaises(InteriorError):
                    room_edit_candidate(data, x=x, y=y, width=width, height=height)
                candidate = room_edit_candidate(data, x=x, y=y, width=width, height=height,
                                                allow_rebase=True)
                self.assertGreaterEqual(min(r["x"] for r in candidate["rooms"]), 1)
                self.assertGreaterEqual(min(r["y"] for r in candidate["rooms"]), 4)
                self.assertEqual(data, before)
                self.assertEqual(candidate["entry"][0] - candidate["rooms"][0]["x"], 2)
                self.assertEqual(candidate["entry"][1] - candidate["rooms"][0]["y"], 5)

    def test_disabled_rooms_do_not_attract_a_disconnected_drop(self):
        data = design()
        data["rooms"][1]["enabled"] = False
        with self.assertRaisesRegex(InteriorError, "connected"):
            room_edit_candidate(data, x=17, y=5, width=4, height=4)

    def test_canvas_growth_respects_format_limit(self):
        candidate = room_edit_candidate(new_interior(), x=12, y=5, width=84, height=4)
        self.assertEqual(candidate["width"], 96)
        with self.assertRaises(InteriorError):
            room_edit_candidate(new_interior(), x=12, y=5, width=85, height=4)

    def test_spouse_fixed_room_cannot_be_moved_or_extended(self):
        data = new_interior("spouse")
        for kwargs in ({"room_id": "main"}, {"width": 3, "height": 3}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(InteriorError, "fixed"):
                room_edit_candidate(data, x=1, y=4, **kwargs)
        self.assertEqual(data, new_interior("spouse"))


if __name__ == "__main__":
    unittest.main()
