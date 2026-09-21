"""Seat data must preserve vanilla syntax without inventing missing art."""
import copy
import unittest

from pixelheart_core.map_seats import (
    SeatError, compile_seat_definition, seat_structure_issues, validate_seat_geometry,
)


class MapSeatTests(unittest.TestCase):
    def setUp(self):
        self.seat = {"id": "Chair", "x": 3, "y": 4, "direction": "right"}

    def test_existing_seats_keep_their_exact_definition_and_input(self):
        original = copy.deepcopy(self.seat)
        self.assertEqual(compile_seat_definition(self.seat), "1/1/right/default/-1/-1/false")
        self.assertEqual(self.seat, original)
        self.assertEqual(seat_structure_issues(self.seat), [])

    def test_custom_seat_keeps_explicit_offsets_and_normalizes_asset_separator(self):
        seat = {**self.seat, "width": 2, "direction": "opposite", "seat_type": "custom",
                "offset_x": .5, "offset_y": -.25, "extra_height": 0,
                "draw_x": 3, "draw_y": 4, "draw_tilesheet": "TileSheets/Author.Mod_SeatFront"}
        self.assertEqual(compile_seat_definition(seat),
                         "2/1/opposite/custom 0.5 -0.25 0/3/4/false/TileSheets\\\\Author.Mod_SeatFront")
        validate_seat_geometry(seat, map_width=8, map_height=8, draw_columns=8, draw_rows=8)
        expected = compile_seat_definition(seat)
        for key in ("TileSheets\\Author.Mod_SeatFront", "TileSheets\\\\Author.Mod_SeatFront"):
            self.assertEqual(compile_seat_definition({**seat, "draw_tilesheet": key}), expected)

    def test_high_back_needs_explicit_overlay_with_room_above(self):
        seat = {**self.seat, "direction": "up", "seat_type": "highback_chair"}
        with self.assertRaisesRegex(SeatError, "requires explicit"):
            compile_seat_definition(seat)
        seat.update(draw_x=0, draw_y=0)
        with self.assertRaisesRegex(SeatError, "tile above"):
            compile_seat_definition(seat)
        seat["draw_y"] = 1
        self.assertEqual(compile_seat_definition(seat), "1/1/up/highback_chair/0/1/false")
        validate_seat_geometry(seat, map_width=8, map_height=8, draw_columns=1, draw_rows=2)
        with self.assertRaisesRegex(SeatError, "one-tile"):
            compile_seat_definition({**seat, "height": 2})

    def test_custom_nonzero_overlay_height_is_not_falsely_validated(self):
        seat = {**self.seat, "seat_type": "custom", "offset_x": 0, "offset_y": 0,
                "extra_height": 1, "draw_x": 0, "draw_y": 1}
        with self.assertRaisesRegex(SeatError, "geometry is not verified"):
            compile_seat_definition(seat)
        seat["extra_height"] = 0
        validate_seat_geometry(seat, map_width=8, map_height=8, draw_columns=1, draw_rows=2)

    def test_custom_offsets_cannot_be_omitted_or_silently_ignored(self):
        for missing in ("offset_x", "offset_y", "extra_height"):
            seat = {**self.seat, "seat_type": "custom", "offset_x": 0, "offset_y": 0, "extra_height": 0}
            del seat[missing]
            self.assertIn(missing, [i["field"] for i in seat_structure_issues(seat)])
        with self.assertRaisesRegex(SeatError, "only used with custom"):
            compile_seat_definition({**self.seat, "offset_x": .5})

    def test_malformed_options_are_rejected_without_numeric_coercion(self):
        for field, value in (("width", True), ("height", 0), ("width", 257), ("direction", "north"),
                             ("seat_type", "custom 0 0 1"), ("draw_x", .5), ("draw_y", True),
                             ("is_seasonal", "false")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(SeatError):
                    compile_seat_definition({**self.seat, field: value})
        for value in (True, float("nan"), float("inf"), -17, 10 ** 1000, "0.5", None):
            with self.subTest(offset=value):
                with self.assertRaises(SeatError):
                    compile_seat_definition({**self.seat, "seat_type": "custom", "offset_x": value,
                                             "offset_y": 0, "extra_height": 0})

    def test_offsets_keep_decimal_precision_and_negative_zero_has_one_representation(self):
        value = compile_seat_definition({**self.seat, "seat_type": "custom", "offset_x": 1e-16,
                                         "offset_y": -0.0, "extra_height": 10})
        self.assertIn("custom 0.0000000000000001 0 10", value)

    def test_draw_coordinates_are_a_pair_and_disabled_features_have_no_hidden_art(self):
        for options in ({"draw_x": 0}, {"draw_y": 0}, {"draw_x": 0, "draw_y": -1},
                        {"draw_x": -1, "draw_y": 0}, {"is_seasonal": True},
                        {"draw_tilesheet": "TileSheets/ChairTiles"}):
            with self.subTest(options=options):
                with self.assertRaises(SeatError):
                    compile_seat_definition({**self.seat, **options})
        self.assertEqual(compile_seat_definition({**self.seat, "draw_x": -1, "draw_y": -1}),
                         "1/1/right/default/-1/-1/false")

    def test_overlay_asset_cannot_escape_or_inject_a_seat_field(self):
        for key in ("/tmp/chair", "C:\\chair", "../chair", "a/../chair", "a//chair", "a/./chair",
                    "https://example.test/chair", "{{ModId}}/chair", "seat\nextra", "a/Chair.png", "a/Chair.xnb", ""):
            with self.subTest(key=key):
                with self.assertRaises(SeatError):
                    compile_seat_definition({**self.seat, "draw_x": 0, "draw_y": 0, "draw_tilesheet": key})

    def test_multitile_seats_fit_the_map_and_all_four_seasonal_overlays_fit_the_sheet(self):
        seat = {**self.seat, "width": 2, "height": 2, "draw_x": 1, "draw_y": 2, "is_seasonal": True}
        validate_seat_geometry(seat, map_width=5, map_height=6, draw_columns=9, draw_rows=4)
        for kwargs in ({"map_width": 4, "map_height": 6}, {"map_width": 5, "map_height": 5},
                       {"map_width": 5, "map_height": 6, "draw_columns": 8, "draw_rows": 4},
                       {"map_width": 5, "map_height": 6, "draw_columns": 9, "draw_rows": 3}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SeatError):
                    validate_seat_geometry(seat, **kwargs)

    def test_geometry_rejects_bad_anchor_or_partial_sheet_dimensions(self):
        for options in ({"x": -1}, {"y": True}, {"x": 2.5}):
            with self.assertRaises(SeatError):
                validate_seat_geometry({**self.seat, **options}, map_width=8, map_height=8)
        with self.assertRaises(SeatError):
            validate_seat_geometry(self.seat, map_width=8, map_height=8, draw_columns=16)
        self.assertTrue(seat_structure_issues(None))


if __name__ == "__main__":
    unittest.main()
