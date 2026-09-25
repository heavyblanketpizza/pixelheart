"""Home workflows preserve live edits and distinguish drafts from connections."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow
from pixelheart_core.interiors import doorway_exit, ensure_doorway, new_interior
from pixelheart_core.life import new_life_record
from pixelheart_core.projects import load_project, new_project
from pixelheart_core.story import new_event
from pixelheart_core.world import exported_location_id, new_companion, new_location, new_world, world_issues


class HomePlacesWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        Image.new("RGBA", (16, 16), (90, 80, 70, 255)).save(self.root / "tiles.png")
        self.window = MainWindow(auto_download_icons=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))
        self.page = self.window.world
        self.open_places()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def design(self):
        design = ensure_doorway(new_interior())
        design["atlas"] = {"asset": "tiles.png", "columns": 1, "tile_count": 1}
        return design

    def place(self, name="Cottage", *, confirmed=True):
        design = self.design()
        record = new_location()
        record.update(name=name, internal_name=name, interior=design,
                      entry_x=design["entry"][0], entry_y=design["entry"][1],
                      exit_x=doorway_exit(design)[0], exit_y=doorway_exit(design)[1])
        record["entrance"]["confirmed"] = confirmed
        return record

    def open_places(self, *places, home=None):
        document = new_project()
        document["character"].update(name="Mira", internal_name="Mira", id="workflow-mira")
        if home:
            document["character"].update(home_map=home["internal_name"], home_x=home["entry_x"], home_y=home["entry_y"])
        document["world"] = new_world()
        document["world"]["locations"] = deepcopy(list(places))
        self.window.load_document(document, self.root / "character.json")
        self.window.open_section("home")
        self.page.location_list.setCurrentRow(0 if places else -1)

    def live_route(self, location):
        route = self.window.schedule.dump()
        route[0].update(location=location, x=6, y=9)
        self.window.schedule.load(route)

    def live_scene(self, location):
        event = new_event(self.window.document["character"])
        event.update(name="A quiet visit", location=location)
        event["story"]["actors"] = []
        self.window.events.load([event])

    def live_routine(self, location):
        character = deepcopy(self.window.document["character"])
        routine = new_life_record("routines")
        routine.update(name="Morning visit", stops=[dict(character["schedule"][0], location=location)])
        character["life"] = {"routines": [routine]}
        self.window.life.load(character)

    def test_removal_blocks_live_routes_actorless_scenes_routines_legacy_homes_and_entrances(self):
        for reference in ("route", "scene", "routine", "legacy", "entrance"):
            with self.subTest(reference=reference):
                home = self.place()
                self.open_places(home, home=home)
                game_id = exported_location_id(home, self.window.document["character"])
                if reference == "route":
                    self.live_route("Cottage")
                elif reference == "scene":
                    self.live_scene(game_id)
                elif reference == "routine":
                    self.live_routine("Cottage")
                elif reference == "legacy":
                    companion = new_companion("Sol")
                    companion["character"]["home_map"] = game_id
                    self.page.world["characters"].append(companion)
                else:
                    annex = self.place("Annex")
                    annex["entrance"]["map"] = "Cottage"
                    self.page.world["locations"].append(annex)
                before = self.page.dump()
                self.page.remove_buttons[self.page.location_panel].click()
                self.assertEqual(self.page.dump(), before)
                self.assertEqual(self.window.identity.dump()["home_map"], "Cottage")
                self.assertIn("still used", self.page.interior_notice.text())
                self.assertFalse(self.page.interior_notice.isHidden())
                self.assertTrue(self.page.undo_remove_button.isHidden())

    def test_undo_restores_home_without_reverting_later_identity_or_place_edits(self):
        home, annex = self.place(), self.place("Annex")
        annex["entrance"].update(x=35, arrival_x=35)
        self.open_places(home, annex, home=home)
        self.page.remove_location()
        self.assertEqual(self.window.identity.dump()["home_map"], "Town")
        self.window.identity.fields["tagline"].setText("A newly written character detail")
        self.page.location_fields["name"].setText("A better annex name")
        self.page.undo_remove_button.click()
        self.assertEqual(self.page.dump()["locations"][0], home)
        self.assertEqual(self.page.dump()["locations"][1]["name"], "A better annex name")
        self.assertEqual(self.window.identity.dump()["home_map"], "Cottage")
        self.assertEqual(self.window.identity.dump()["tagline"], "A newly written character detail")
        self.assertEqual(self.window.document["character"]["tagline"], "A newly written character detail")
        self.assertTrue(self.page.undo_remove_button.isHidden())
        self.assertTrue(self.window.dirty)

    def test_undo_does_not_replace_a_home_assigned_after_removal(self):
        home, annex = self.place(), self.place("Annex")
        annex["entrance"].update(x=35, arrival_x=35)
        self.open_places(home, annex, home=home)
        self.page.remove_location()
        self.page.assign_home()
        self.assertEqual(self.window.identity.dump()["home_map"], "Annex")
        self.page.undo_remove_location()
        self.assertEqual(self.window.identity.dump()["home_map"], "Annex")
        self.assertEqual(len(self.page.dump()["locations"]), 2)

    def test_removing_legacy_spouse_home_repairs_assignment_and_undo_restores_it(self):
        room = self.place("SpouseRoom")
        room.update(spouse_room=True, interior=new_interior("spouse"))
        room["interior"]["atlas"] = {"asset": "tiles.png", "columns": 1, "tile_count": 1}
        self.open_places(room, home=room)
        game_id = exported_location_id(room, self.window.document["character"])
        self.window.identity.fields["home_map"].setText(game_id)
        self.page.remove_location()
        self.assertEqual(self.window.identity.dump()["home_map"], "Town")
        self.assertEqual(self.page.dump()["locations"], [])
        self.page.undo_remove_location()
        self.assertEqual(self.window.identity.dump()["home_map"], game_id)

    def test_save_finishes_removal_and_clears_undo_history(self):
        self.open_places(self.place())
        self.page.remove_location()
        self.assertFalse(self.page.undo_remove_button.isHidden())
        self.assertTrue(self.window.save())
        self.assertEqual(load_project(self.window.project_file)["world"]["locations"], [])
        self.assertTrue(self.page.undo_remove_button.isHidden())
        self.page.undo_remove_location()
        self.assertEqual(self.page.dump()["locations"], [])

    def test_rename_migrates_unsaved_live_editors_and_connected_places_atomically(self):
        home, annex = self.place(), self.place("Annex")
        annex["entrance"].update(map="Cottage", x=6, y=9, arrival_x=6, arrival_y=10)
        self.open_places(home, annex, home=home)
        old_game_id = exported_location_id(home, self.window.document["character"])
        self.live_route("Cottage")
        self.live_scene(old_game_id)
        self.live_routine("Cottage")
        self.window.identity.fields["tagline"].setText("Keep my unsaved prose")
        self.page.location_fields["internal_name"].setText("Workshop")
        renamed = self.page.dump()["locations"][0]
        new_game_id = exported_location_id(renamed, self.window.document["character"])
        self.assertEqual(self.window.identity.dump()["home_map"], "Workshop")
        self.assertEqual(self.window.schedule.dump()[0]["location"], "Workshop")
        self.assertEqual(self.window.events.dump()[0]["location"], new_game_id)
        self.assertEqual(self.window.life.dump()["routines"][0]["stops"][0]["location"], "Workshop")
        self.assertEqual(self.page.dump()["locations"][1]["entrance"]["map"], "Workshop")
        self.assertEqual(self.window.document["character"]["tagline"], "Keep my unsaved prose")
        self.assertEqual(self.window.document["character"]["events"][0]["location"], new_game_id)
        self.assertEqual(self.window.document["world"], self.page.dump())
        self.assertTrue(self.window.dirty)

    def test_conflicting_rename_preserves_world_and_live_editors(self):
        home, annex = self.place(), self.place("Annex")
        annex["entrance"].update(x=35, arrival_x=35)
        self.open_places(home, annex, home=home)
        self.live_route("Cottage")
        self.live_scene("Cottage")
        before = deepcopy((self.page.dump(), self.page._live_character()))
        spy = QSignalSpy(self.page.changed)
        self.page.location_fields["internal_name"].setText("Annex")
        self.assertEqual((self.page.dump(), self.page._live_character()), before)
        self.assertEqual(spy.count(), 0)
        self.assertIn("already uses", self.page.interior_notice.text())
        self.assertFalse(self.page.interior_notice.isHidden())

    def test_rename_preserves_migration_of_selected_places_own_entrance(self):
        home = self.place(confirmed=False)
        home["entrance"]["map"] = "Cottage"
        self.open_places(home)
        self.page.location_fields["internal_name"].setText("Workshop")
        self.assertEqual(self.page.dump()["locations"][0]["entrance"]["map"], "Workshop")
        self.assertEqual(self.page.entrance_fields["map"].value(), "Workshop")
        self.assertEqual(self.window.document["world"]["locations"][0]["entrance"]["map"], "Workshop")

    def test_designed_entry_and_exit_are_read_only_and_ignore_programmatic_changes(self):
        home = self.place()
        self.open_places(home, home=home)
        self.page.connection_button.click()
        self.assertTrue(self.page.interior_connection_fields.isHidden())
        for key in ("entry_x", "entry_y", "exit_x", "exit_y"):
            with self.subTest(key=key):
                field = self.page.location_fields[key]
                self.assertFalse(field.isEnabled())
                field.setValue(home[key] + 1)
                self.assertEqual(self.page.dump()["locations"][0][key], home[key])
                self.assertEqual(field.value(), home[key])
        self.assertEqual(self.window.identity.dump()["home_x"], home["entry_x"])
        self.assertEqual(self.window.identity.dump()["home_y"], home["entry_y"])
        self.assertEqual(self.page.dump()["locations"][0]["interior"], home["interior"])

    def test_new_residence_remains_pending_until_entrance_is_explicitly_confirmed(self):
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = self.design()
            self.page.build_home_button.click()
        self.errors.assert_not_called()
        home = self.page.dump()["locations"][0]
        self.assertFalse(home["entrance"]["confirmed"])
        self.assertIn("Draft", self.page.readiness.text())
        self.assertIn("entrance", self.page.readiness.text().lower())
        issues = world_issues(self.page.dump(), self.window.document["character"], self.root)
        self.assertTrue(any(issue["level"] == "error" and ".entrance" in issue["field"] for issue in issues))
        self.page.connection_button.click()
        self.assertFalse(self.page.warps_card.isHidden())
        self.page.confirm_entrance_button.click()
        self.assertTrue(self.page.dump()["locations"][0]["entrance"]["confirmed"])
        self.assertIn("Ready for export checks", self.page.readiness.text())
        self.assertTrue(self.page.warps_card.isHidden())

    def test_each_outside_connection_edit_revokes_confirmation_and_name_edit_does_not(self):
        self.open_places(self.place())
        self.page.location_fields["name"].setText("Cottage by the river")
        self.assertTrue(self.page.dump()["locations"][0]["entrance"]["confirmed"])
        for key in ("map", "x", "y", "arrival_x", "arrival_y"):
            with self.subTest(key=key):
                widget = self.page.entrance_fields[key]
                if key == "map":
                    widget.setText("Forest")
                else:
                    widget.setValue(widget.value() + 2)
                self.assertFalse(self.page.dump()["locations"][0]["entrance"]["confirmed"])
                self.assertIn("Connect entrance", self.page.connection_button.text())
                self.assertIn("Draft", self.page.readiness.text())
                self.page.confirm_entrance()
                self.assertTrue(self.page.dump()["locations"][0]["entrance"]["confirmed"])

    def test_story_place_starts_pending_and_duplicate_entrance_cannot_be_confirmed(self):
        self.open_places(self.place())
        self.page.story_place_button.click()
        self.assertFalse(self.page.dump()["locations"][1]["entrance"]["confirmed"])
        self.page.confirm_entrance()
        self.assertFalse(self.page.dump()["locations"][1]["entrance"]["confirmed"])
        self.assertIn("already uses", self.page.connection_error.text())
        self.assertFalse(self.page.connection_error.isHidden())

    def test_readiness_keeps_missing_art_visible_after_entrance_confirmation(self):
        home = self.place(confirmed=False)
        home["interior"]["atlas"] = {"asset": "", "columns": 0, "tile_count": 0}
        self.open_places(home)
        self.assertIn("custom tile art", self.page.readiness.text())
        self.page.confirm_entrance()
        self.assertTrue(self.page.dump()["locations"][0]["entrance"]["confirmed"])
        self.assertIn("Draft", self.page.readiness.text())
        self.assertIn("custom tile art", self.page.readiness.text())
        self.assertNotIn("Ready for export checks", self.page.readiness.text())

    def test_imported_inside_coordinates_require_reconfirming_the_connection(self):
        place = new_location()
        place["entrance"]["confirmed"] = True
        self.open_places(place)
        for key in ("entry_x", "entry_y", "exit_x", "exit_y"):
            with self.subTest(key=key):
                self.page.world["locations"][0]["entrance"]["confirmed"] = True
                field = self.page.location_fields[key]
                self.assertTrue(field.isEnabled())
                field.setValue(field.value() + 1)
                self.assertFalse(self.page.dump()["locations"][0]["entrance"]["confirmed"])

    def test_missing_map_issue_opens_an_enabled_import_action(self):
        self.open_places(new_location())
        self.window.show()
        self.page.open_issue("world.locations.0.map")
        self.app.processEvents()
        self.assertTrue(self.page.import_map_button.isEnabled())
        self.assertTrue(self.page.import_map_button.hasFocus())

    def test_connect_entrance_reveals_the_form_at_minimum_window_size(self):
        self.open_places(self.place(confirmed=False))
        self.window.resize(1020, 700)
        self.window.show()
        self.app.processEvents()
        self.page.connection_button.click()
        for _ in range(3):
            self.app.processEvents()
        scroll = self.page.detail_stacks[self.page.location_panel].widget(0)
        self.assertGreater(scroll.verticalScrollBar().value(), 0)
        self.assertFalse(self.page.entrance_fields["map"].visibleRegion().isEmpty())


if __name__ == "__main__":
    unittest.main()
