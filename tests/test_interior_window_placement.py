"""Window edits share the preview rule while preserving older saved layouts."""
from copy import deepcopy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pixelheart.interior_canvas import InteriorCanvas
from pixelheart_core.interior_furniture import validate_definition
from pixelheart_core.interiors import InteriorDraft, InteriorError, new_interior, normalize_interior
from tests.qt_support import QtTestCase


def window(**changes):
    definition = {"id": "(F)Test.Window", "name": "Test window", "kind": "window",
                  "footprint": [1, 2], "sprite_size": [1, 2], "rotations": 1}
    definition.update(changes)
    return validate_definition(definition)


def design(kind="residence", definition=None):
    data = new_interior(kind)
    data["catalog"] = [definition or window()]
    return data


class InteriorWindowPlacementTests(unittest.TestCase):
    def test_residence_and_spouse_windows_start_at_upper_wall(self):
        for kind, x, y in (("residence", 5, 2), ("spouse", 2, 0)):
            with self.subTest(kind=kind):
                draft = InteriorDraft(design(kind))
                before = draft.snapshot()
                with self.assertRaisesRegex(InteriorError, "top of the wall"):
                    draft.place_furniture("(F)Test.Window", x, y + 1)
                self.assertEqual(draft.snapshot(), before)
                self.assertFalse(draft.undo())
                identity = draft.place_furniture("(F)Test.Window", x, y)
                self.assertEqual(draft.data["furniture"][0]["id"], identity)

    def test_rejected_placement_keeps_redo(self):
        draft = InteriorDraft(design())
        draft.place_furniture("(F)Test.Window", 5, 2)
        expected = draft.snapshot()
        draft.undo()
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.place_furniture("(F)Test.Window", 5, 3)
        self.assertTrue(draft.redo())
        self.assertEqual(draft.snapshot(), expected)

    def test_move_and_direct_snapshot_edits_cannot_lower_window(self):
        draft = InteriorDraft(design())
        identity = draft.place_furniture("(F)Test.Window", 5, 2)
        expected = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.move_furniture(identity, 6, 3)
        changed = draft.snapshot()
        changed["furniture"][0]["y"] = 3
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.apply(changed)
        self.assertEqual(draft.snapshot(), expected)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"], [])

    def test_boarded_window_keeps_painting_type_and_upper_wall_rule(self):
        for definition in (window(id="(F)1630", name="Localized title", kind="painting"),
                           window(id="(F)Custom.Boarded", name="Boarded Window", kind="painting")):
            with self.subTest(identity=definition["id"]):
                draft = InteriorDraft(design(definition=definition))
                with self.assertRaisesRegex(InteriorError, "top of the wall"):
                    draft.place_furniture(definition["id"], 5, 3)
                draft.place_furniture(definition["id"], 5, 2)
                self.assertEqual(draft.data["catalog"][0]["kind"], "painting")

    def test_other_paintings_keep_their_existing_wall_positions(self):
        definition = window(id="(F)Test.Painting", name="Landscape", kind="painting")
        draft = InteriorDraft(design(definition=definition))
        draft.place_furniture(definition["id"], 5, 3)
        self.assertEqual(draft.data["furniture"][0]["y"], 3)

    def test_upper_wall_follows_enabled_steps_across_entire_window_width(self):
        definition = window(footprint=[2, 2], sprite_size=[2, 2])
        data = design(definition=definition)
        data["rooms"].append({"id": "side", "name": "Side room", "x": 12, "y": 7,
                              "width": 4, "height": 5, "optional": True, "enabled": True})
        draft = InteriorDraft(data)
        for x, y in ((11, 2), (11, 4), (12, 5)):
            with self.subTest(position=(x, y)), self.assertRaises(InteriorError):
                draft.place_furniture(definition["id"], x, y)
        draft.place_furniture(definition["id"], 12, 4)
        self.assertEqual(draft.data["furniture"][0]["y"], 4)
        data["rooms"][1]["enabled"] = False
        disabled = InteriorDraft(data)
        with self.assertRaises(InteriorError):
            disabled.place_furniture(definition["id"], 12, 4)

    def test_sprite_top_is_used_when_sprite_is_taller_than_footprint(self):
        definition = window(footprint=[1, 1])
        draft = InteriorDraft(design(definition=definition))
        # The visible 32px sprite starts one tile above its one-tile footprint.
        draft.place_furniture(definition["id"], 5, 3)
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.place_furniture(definition["id"], 7, 4)

    def test_full_width_must_share_one_upper_wall_edge(self):
        definition = window(footprint=[2, 1], sprite_size=[2, 1])
        data = design(definition=definition)
        data["rooms"].append({"id": "higher", "name": "Higher room", "x": 12, "y": 4,
                              "width": 4, "height": 5, "optional": True, "enabled": True})
        draft = InteriorDraft(data)
        # Both cells are wall, but only the left cell is on its upper edge.
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.place_furniture(definition["id"], 11, 2)
        draft.place_furniture(definition["id"], 12, 1)

    def test_rotation_cannot_shift_visible_top_off_wall_edge(self):
        definition = window(rotations=2, rotation_footprints={"1": [1, 1]})
        draft = InteriorDraft(design(definition=definition))
        identity = draft.place_furniture(definition["id"], 5, 2)
        before = draft.snapshot()
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.rotate_furniture(identity)
        self.assertEqual(draft.snapshot(), before)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"], [])

    def test_frame_size_overrides_fallback_sprite_dimensions(self):
        definition = window(footprint=[1, 1], sprite_size=[1, 1],
                            frames=[{"rotation": 0, "rect": [0, 0, 16, 32]}])
        draft = InteriorDraft(design(definition=definition))
        draft.place_furniture(definition["id"], 5, 3)
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.place_furniture(definition["id"], 7, 4)

    def test_legacy_low_window_loads_and_can_be_fixed_without_silent_changes(self):
        data = design()
        data["furniture"] = [{"id": "legacy", "item_id": "(F)Test.Window", "x": 5,
                              "y": 3, "rotation": 0, "mod_data": {}}]
        original = deepcopy(data)
        self.assertEqual(normalize_interior(data), original)
        draft = InteriorDraft(data)
        changed = draft.snapshot()
        changed["rooms"][0]["name"] = "Renamed room"
        draft.apply(changed)
        self.assertEqual(draft.data["furniture"], original["furniture"])
        draft.move_furniture("legacy", 5, 2)
        self.assertEqual(draft.data["furniture"][0]["y"], 2)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.data["furniture"], original["furniture"])
        self.assertEqual(InteriorDraft(draft.snapshot()).data, draft.data)

    def test_room_expansion_rebase_preserves_legacy_window_relative_position(self):
        data = design()
        data["furniture"] = [{"id": "legacy", "item_id": "(F)Test.Window", "x": 5,
                              "y": 3, "rotation": 0, "mod_data": {}}]
        draft = InteriorDraft(data)
        candidate = draft.snapshot()
        shift = 5
        candidate["width"] += shift
        for room in candidate["rooms"]:
            room["x"] += shift
        for item in candidate["furniture"]:
            item["x"] += shift
        for anchor in ("entry", "spouse_stand"):
            candidate[anchor][0] += shift
        candidate["rooms"].append({"id": "left", "name": "Left room", "x": 1, "y": 5,
                                   "width": 6, "height": 5, "optional": True, "enabled": True})
        draft.apply(candidate)
        self.assertEqual((draft.data["furniture"][0]["x"], draft.data["furniture"][0]["y"]), (10, 3))
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            draft.move_furniture("legacy", 11, 3)
        # A rebase never exempts a newly added low window.
        candidate["furniture"].append({"id": "new", "item_id": "(F)Test.Window", "x": 12,
                                       "y": 3, "rotation": 0, "mod_data": {}})
        with self.assertRaisesRegex(InteriorError, "top of the wall"):
            InteriorDraft(data).apply(candidate)
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), data)


class InteriorWindowPreviewTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_and_placement_agree_for_both_room_types(self):
        for kind, x, y in (("residence", 5, 2), ("spouse", 2, 0)):
            with self.subTest(kind=kind):
                draft = InteriorDraft(design(kind))
                canvas = InteriorCanvas(draft)
                try:
                    before = draft.snapshot()
                    canvas.set_placement(draft.data["catalog"][0])
                    canvas.cursor_tile = (x, y + 1)
                    canvas._update_ghost()
                    self.assertFalse(canvas.ghost["valid"])
                    self.assertIn("top of the wall", canvas.preview_message)
                    self.assertEqual(draft.snapshot(), before)
                    self.assertFalse(draft.undo())
                    with self.assertRaises(InteriorError):
                        draft.place_furniture("(F)Test.Window", x, y + 1)
                    canvas.cursor_tile = (x, y)
                    canvas._update_ghost()
                    self.assertTrue(canvas.ghost["valid"])
                    draft.place_furniture("(F)Test.Window", x, y)
                finally:
                    canvas.close()
                    canvas.deleteLater()
        self.app.processEvents()

    def test_existing_low_window_preview_allows_fix_and_blocks_another_low_position(self):
        data = design()
        data["furniture"] = [{"id": "legacy", "item_id": "(F)Test.Window", "x": 5,
                              "y": 3, "rotation": 0, "mod_data": {}}]
        draft = InteriorDraft(data)
        canvas = InteriorCanvas(draft)
        try:
            definition = draft.data["catalog"][0]
            self.assertFalse(canvas._validate_furniture(definition, 0, 6, 3, "legacy")[0])
            with self.assertRaises(InteriorError):
                draft.move_furniture("legacy", 6, 3)
            self.assertTrue(canvas._validate_furniture(definition, 0, 6, 2, "legacy")[0])
            draft.move_furniture("legacy", 6, 2)
            self.assertEqual(draft.data["furniture"][0]["y"], 2)
        finally:
            canvas.close()
            canvas.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
