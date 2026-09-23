"""Layout edits preserve authored destinations and update them only on moves."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart_core.interior_layout import partition_candidate, resize_room_candidate
from pixelheart_core.world import WorldError, exported_location_id, new_companion
from tests import test_interior_world_page as world_tests


class InteriorFloorplanWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = world_tests.InteriorWorldPageTests.setUp
    tearDown = world_tests.InteriorWorldPageTests.tearDown
    moving_home = world_tests.InteriorWorldPageTests.moving_home

    def design_result(self, design, offsets=None):
        constructor = self.enterContext(patch("pixelheart.interior_editor.InteriorEditor"))
        dialog = constructor.return_value
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.result_design = design
        dialog.result_room_translations = {} if offsets is None else offsets
        return constructor

    def assert_rejected_unchanged(self, expected):
        before_world = self.page.dump()
        before_character = deepcopy(self.window.document["character"])
        before_identity = self.window.identity.dump()
        spy = QSignalSpy(self.page.changed)
        self.assertFalse(self.page.design_interior())
        self.assertIn(expected, self.window.errors[-1][1])
        self.assertEqual(self.page.dump(), before_world)
        self.assertEqual(self.window.document["character"], before_character)
        self.assertEqual(self.window.identity.dump(), before_identity)
        self.assertEqual(spy.count(), 0)

    def test_shrink_cannot_remove_home_position(self):
        record, _ = self.moving_home()
        design = resize_room_candidate(record["interior"], "main", 2, 5, 5, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("home position at tile 7, 8")

    def test_internal_wall_cannot_cover_home_position(self):
        record, _ = self.moving_home()
        design = partition_candidate(record["interior"], room_id="main", axis="vertical", x=7, y=8, length=1, opening_width=0)
        self.design_result(design)
        self.assert_rejected_unchanged("home position at tile 7, 8")

    def test_live_daily_schedule_is_protected_before_document_collect(self):
        record, _ = self.moving_home()
        stops = [{"time": "900", "location": "TheirHome", "x": "10", "y": "8"}]
        self.window.schedule = Mock(dump=Mock(return_value=deepcopy(stops)))
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("daily schedule at 900")
        self.window.schedule.load.assert_not_called()
        self.assertEqual(self.window.schedule.dump(), stops)

    def test_wall_cannot_isolate_a_destination_beside_existing_furniture(self):
        record, _ = self.moving_home()
        record["interior"]["catalog"] = [{"id": "(F)1", "name": "Chair", "kind": "chair", "footprint": [1, 1]}]
        record["interior"]["furniture"] = [{"id": str(index), "item_id": "(F)1", "x": x, "y": y, "rotation": 0}
                                          for index, (x, y) in enumerate(((9, 8), (11, 8), (10, 9)))]
        self.window.document["character"]["schedule"] = [{"time": "900", "location": "TheirHome", "x": 10, "y": 8}]
        design = partition_candidate(record["interior"], "main", "horizontal", 9, 7, 3, opening_width=0)
        self.design_result(design)
        self.assert_rejected_unchanged("daily schedule at 900 at tile 10, 8")

    def test_live_routine_alias_is_protected(self):
        record, _ = self.moving_home()
        game_map = exported_location_id(record, self.window.document["character"])
        life = {"routines": [{"name": "Rainy reading", "stops": [{"location": game_map, "x": 10, "y": 8}]}]}
        self.window.life = Mock(dump=Mock(return_value=life))
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("routine “Rainy reading”, stop 1")
        self.window.life.load.assert_not_called()

    def test_live_scene_start_is_protected_without_changing_scene_moves(self):
        record, _ = self.moving_home()
        events = [{"name": "First visit", "location": "TheirHome", "story": {
            "actors": [{"name": "farmer", "x": 10, "y": 8}],
            "beats": [{"kind": "move", "x": -2, "y": 1}]}}]
        self.window.events = Mock(dump=Mock(return_value=deepcopy(events)))
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("farmer's starting position in “First visit”")
        self.window.events.load.assert_not_called()
        self.assertEqual(self.window.events.dump(), events)

    def test_other_places_entrance_and_arrival_are_protected(self):
        record, _ = self.moving_home()
        game_map = exported_location_id(record, self.window.document["character"])
        self.page.add_location()
        other = self.page.world["locations"][-1]
        other["name"] = "Cellar"
        entrance = other["entrance"]
        entrance.update(map=game_map, x=10, y=8, arrival_x=7, arrival_y=9)
        self.page.select_location(0)
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("Cellar's entrance")
        entrance.update(x=7, y=8, arrival_x=10, arrival_y=9)
        self.assert_rejected_unchanged("Cellar's return arrival")

    def test_legacy_character_destinations_are_protected(self):
        record, _ = self.moving_home()
        bundled = new_companion("Archive")
        bundled["character"].update(home_map="TheirHome", home_x=10, home_y=8)
        self.page.world["characters"].append(bundled)
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assert_rejected_unchanged("home position at tile 10, 8")

    def test_existing_invalid_draft_points_do_not_prevent_layout_repairs(self):
        record, _ = self.moving_home()
        self.window.document["character"]["schedule"] = [{"location": "TheirHome", "x": 50, "y": 50}]
        design = resize_room_candidate(record["interior"], "main", 2, 5, 7, 8)
        self.design_result(design)
        self.assertTrue(self.page.design_interior())
        self.assertFalse(self.window.errors)
        self.assertEqual(self.window.document["character"]["schedule"][0]["x"], 50)

    def test_resize_origin_change_does_not_translate_destinations(self):
        record, _ = self.moving_home()
        design = resize_room_candidate(record["interior"], "main", 3, 5, 9, 8)
        self.design_result(design)
        self.assertTrue(self.page.design_interior())
        self.assertEqual((self.window.identity.dump()["home_x"], self.window.identity.dump()["home_y"]), (7, 8))

    def test_explicit_move_then_resize_translates_once_despite_changed_dimensions(self):
        record, design = self.moving_home()
        design["rooms"][0]["width"] += 2
        self.design_result(design, {"main": (2, 0)})
        self.assertTrue(self.page.design_interior())
        self.assertEqual((self.window.identity.dump()["home_x"], self.window.identity.dump()["home_y"]), (9, 8))
        self.assertEqual((record["exit_x"], record["exit_y"]), (10, 11))

    def test_preview_validator_rejects_candidate_without_touching_world(self):
        record, _ = self.moving_home()
        original = deepcopy(record["interior"])
        constructor = self.design_result(original)
        constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
        before_world = self.page.dump()
        self.assertFalse(self.page.design_interior())
        validator = constructor.call_args.kwargs["validate_layout"]
        invalid = resize_room_candidate(original, "main", 2, 5, 5, 8)
        with self.assertRaisesRegex(WorldError, "home position"):
            validator(invalid, {})
        validator(original, {})
        self.assertEqual(self.page.dump(), before_world)
        self.assertFalse(self.window.errors)


if __name__ == "__main__":
    unittest.main()
