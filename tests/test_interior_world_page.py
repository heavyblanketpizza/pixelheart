"""World integration applies interior edits to the selected place atomically."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.editors import IdentityPage, SchedulePage
from pixelheart.life_page import LifePage
from pixelheart.story_page import StoryPage
from pixelheart.world_page import WorldPage
from pixelheart_core.interiors import new_interior, floor_cells
from pixelheart_core.projects import new_project, save_project, load_project
from pixelheart_core.world import new_location, new_companion, exported_location_id
from pixelheart_core.life import new_life_record
from pixelheart_core.story import new_event, new_actor, new_beat

try:
    from .test_world import make_map
except ImportError:
    from test_world import make_map


class InteriorWorldPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        test = self

        class Window:
            def __init__(self):
                self.document = new_project()
                self.project_file = None
                self.errors = []
                self.identity = IdentityPage()
                self.identity.load(self.document["character"])

            def ensure_saved(self):
                if self.project_file is None:
                    self.project_file = test.root / "project" / "character.json"
                    self.document["world"] = test.page.dump()
                    save_project(self.document, self.project_file)
                    test.page.load(load_project(self.project_file)["world"])
                return True

            def show_error(self, title, message):
                self.errors.append((title, message))

        self.window = Window()
        self.extra_widgets = []
        self.page = WorldPage(self.window)
        self.page.load()

    def tearDown(self):
        for widget in self.extra_widgets:
            widget.close()
            widget.deleteLater()
        self.page.close()
        self.page.deleteLater()
        self.window.identity.close()
        self.window.identity.deleteLater()
        self.app.processEvents()

    def two_locations(self):
        self.page.add_location()
        self.page.add_location()
        self.page.world["locations"][0].update(name="First", internal_name="First")
        self.page.world["locations"][1].update(name="Second", internal_name="Second")
        self.page.select_location(1)
        return self.page.world["locations"][1]["id"]

    def test_first_save_keeps_selected_place_and_applies_residence_design(self):
        identity = self.two_locations()
        design = new_interior()
        spy = QSignalSpy(self.page.changed)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            dialog = constructor.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_design = deepcopy(design)
            self.page.design_interior()
            constructor.assert_called_once_with(self.window.project_file, None, "residence", self.page,
                                                resident_name=self.window.document["character"]["name"], allow_rebase=False)
        self.assertFalse(self.window.errors)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertNotIn("interior", self.page.world["locations"][0])
        record = self.page.world["locations"][1]
        self.assertEqual(record["interior"], design)
        self.assertIsNone(record["map"])
        self.assertEqual([record["entry_x"], record["entry_y"]], design["entry"])
        self.assertIn((record["exit_x"], record["exit_y"]), floor_cells(design))
        self.assertNotEqual((record["exit_x"], record["exit_y"]), tuple(design["entry"]))
        self.assertEqual(spy.count(), 1)

    def test_cancelled_first_design_preserves_place_records(self):
        identity = self.two_locations()
        before = self.page.dump()
        spy = QSignalSpy(self.page.changed)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            constructor.return_value.result_design = new_interior()
            self.page.design_interior()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertEqual(spy.count(), 0)

    def test_assign_home_updates_document_and_identity_form_together(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(internal_name="CustomResidence", entry_x=4, entry_y=10, interior=new_interior())
        self.page.select_location(0)
        spy = QSignalSpy(self.page.changed)
        self.page.assign_home()
        expected = {"home_map": "CustomResidence", "home_x": 4, "home_y": 10}
        for key, value in expected.items():
            self.assertEqual(self.window.document["character"][key], value)
            self.assertEqual(self.window.identity.dump()[key], value)
        self.assertEqual(spy.count(), 1)

    def test_spouse_design_uses_fixed_section_and_cannot_assign_separate_home(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(spouse_room=True, room_x=4, room_y=7)
        self.page.select_location(0)
        before_home = self.window.document["character"]["home_map"]
        design = new_interior("spouse")
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = design
            self.page.design_interior()
            constructor.assert_called_once_with(self.window.project_file, None, "spouse", self.page,
                                                resident_name=self.window.document["character"]["name"], allow_rebase=False)
        record = self.page.world["locations"][0]
        self.assertEqual(record["interior"]["kind"], "spouse")
        self.assertEqual((record["interior"]["width"], record["interior"]["height"]), (6, 9))
        self.assertEqual((record["room_x"], record["room_y"]), (0, 0))
        self.assertTrue(self.page.assign_home_button.isHidden())
        self.assertFalse(self.page.location_fields["spouse_room"].isEnabled())
        self.page.assign_home()
        self.assertEqual(self.window.document["character"]["home_map"], before_home)

    def test_importing_tmx_replaces_design_and_reenables_generic_map_editor(self):
        self.page.add_location()
        self.page.world["locations"][0]["interior"] = new_interior()
        self.page.select_location(0)
        source = make_map(self.root / "source")
        with patch("pixelheart.world_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.page.import_location()
        self.assertFalse(self.window.errors)
        record = self.page.world["locations"][0]
        self.assertNotIn("interior", record)
        self.assertTrue(record["map"])
        self.assertTrue(self.page.edit_map_button.isEnabled())
        self.assertTrue(self.page.location_fields["spouse_room"].isEnabled())

    def test_reopening_design_and_error_preserve_existing_state(self):
        self.page.add_location()
        self.page.world["locations"][0]["interior"] = new_interior()
        self.page.select_location(0)
        self.window.ensure_saved()
        before = self.page.dump()
        with patch("pixelheart.interior_editor.InteriorEditor", side_effect=ValueError("Invalid texture reference")):
            self.page.design_interior()
        self.assertEqual(self.page.dump(), before)
        self.assertIn("Invalid texture reference", self.window.errors[-1][1])

    def test_empty_places_starts_with_home_actions_and_closed_advanced_tools(self):
        self.assertEqual(self.page.build_home_button.text(), "Build residence…")
        self.assertEqual(self.page.design_spouse_button.text(), "Design spouse room…")
        self.assertFalse(self.page.story_place_button.isHidden())
        self.assertTrue(self.page.location_advanced.isHidden())
        self.assertFalse(self.page.tabs.isTabVisible(1))
        self.assertTrue(self.page.location_list.isHidden())
        self.assertFalse(self.page.remove_buttons[self.page.location_panel].isEnabled())
        self.page.place_advanced_toggle.click()
        self.assertFalse(self.page.story_place_button.isHidden())
        self.assertFalse(self.page.location_advanced.isHidden())
        self.assertTrue(self.page.tabs.isTabVisible(1))
        self.page.place_advanced_toggle.click()
        self.assertTrue(self.page.location_advanced.isHidden())
        self.assertFalse(self.page.tabs.isTabVisible(1))

    def test_build_home_opens_designer_and_assigns_accepted_home_without_map_setup(self):
        self.window.document["character"].update(name="Loki", internal_name="Loki")
        self.window.identity.load(self.window.document["character"])
        before_home = self.window.document["character"]["home_map"]
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            dialog = constructor.return_value
            dialog.result_design = new_interior()
            def accept():
                self.assertEqual(self.window.document["character"]["home_map"], before_home)
                self.assertEqual(len(self.page.world["locations"]), 1)
                self.assertEqual(load_project(self.window.project_file)["world"]["locations"], [])
                return QDialog.DialogCode.Accepted
            dialog.exec.side_effect = accept
            self.page.build_home_button.click()
        self.assertFalse(self.window.errors)
        record = self.page.world["locations"][0]
        self.assertEqual((record["name"], record["internal_name"]), ("Loki's home", "LokiHome"))
        self.assertEqual(record["interior"], new_interior())
        self.assertEqual(self.window.document["character"]["home_map"], "LokiHome")
        self.assertEqual(self.window.identity.dump()["home_map"], "LokiHome")
        self.assertTrue(self.page.location_advanced.isHidden())
        self.assertEqual(self.page.build_home_button.text(), "Edit residence…")
        self.assertTrue(self.page.assign_home_button.isHidden())

    def test_residence_action_reopens_current_npcs_home_and_updates_entry(self):
        self.page.add_location()
        self.page.world["locations"][0].update(internal_name="TheirHome", interior=new_interior())
        self.page.assign_home()
        identity = self.page.world["locations"][0]["id"]
        self.page.add_location()
        design = new_interior()
        design["entry"] = [5, 10]
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = design
            self.page.build_home()
        self.assertEqual(len(self.page.world["locations"]), 2)
        self.assertFalse(self.window.errors)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertEqual(self.window.document["character"]["home_map"], "TheirHome")
        self.assertEqual(self.window.identity.dump()["home_x"], 5)
        self.assertEqual(self.window.identity.dump()["home_y"], 10)

    def test_residence_map_rename_keeps_assignment_and_protects_designed_entry(self):
        self.page.add_location()
        self.page.world["locations"][0]["interior"] = new_interior()
        self.page.assign_home()
        self.window.identity.fields["tagline"].setText("Unsaved character detail")
        self.page.location_fields["internal_name"].setText("RenamedHome")
        self.page.location_fields["entry_x"].setValue(5)
        self.page.location_fields["entry_y"].setValue(6)
        for character in (self.window.document["character"], self.window.identity.dump()):
            self.assertEqual(character["home_map"], "RenamedHome")
            self.assertEqual((character["home_x"], character["home_y"]), (2, 2))
        self.assertFalse(self.page.location_fields["entry_x"].isEnabled())
        for character in (self.window.document["character"], self.window.identity.dump()):
            self.assertEqual(character["tagline"], "Unsaved character detail")

    def test_removing_residence_restores_valid_base_game_home(self):
        self.page.add_location()
        self.page.assign_home()
        self.page.remove_location()
        defaults = new_project()["character"]
        for key in ("home_map", "home_x", "home_y"):
            self.assertEqual(self.window.identity.dump()[key], defaults[key])
            self.assertEqual(self.window.document["character"][key], defaults[key])
        self.assertEqual(self.page.build_home_button.text(), "Build residence…")

    def test_residence_edits_preserve_explicit_npc_standing_tile(self):
        self.page.add_location()
        self.page.assign_home()
        character = self.window.identity.dump()
        character.update(home_x=7, home_y=8)
        self.window.identity.load(character)
        self.page.location_fields["name"].setText("Cozy cottage")
        self.page.location_fields["internal_name"].setText("Cottage")
        self.page.location_fields["entry_x"].setValue(3)
        self.page.location_fields["entry_y"].setValue(6)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = new_interior()
            self.page.build_home()
        self.assertFalse(self.window.errors)
        for character in (self.window.document["character"], self.window.identity.dump()):
            self.assertEqual(character["home_map"], "Cottage")
            self.assertEqual((character["home_x"], character["home_y"]), (7, 8))

    def moving_home(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(internal_name="TheirHome", interior=new_interior(), entry_x=4, entry_y=10,
                      exit_x=8, exit_y=11)
        self.page.assign_home()
        self.page._update_home("TheirHome", 7, 8)
        self.window.ensure_saved()
        record = self.page.world["locations"][0]
        design = deepcopy(record["interior"])
        design["rooms"][0]["x"] += 2
        design["entry"][0] += 2
        return record, design

    def accept_design(self, design):
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = design
            self.assertTrue(self.page.design_interior())
        self.assertFalse(self.window.errors)

    def test_room_move_keeps_explicit_home_and_exit_with_room_even_when_old_tiles_remain_valid(self):
        record, design = self.moving_home()
        self.assertIn((7, 8), floor_cells(design))
        self.assertIn((8, 11), floor_cells(design))
        self.accept_design(design)
        self.assertEqual((record["exit_x"], record["exit_y"]), (10, 11))
        self.assertEqual((record["entry_x"], record["entry_y"]), (6, 10))
        for character in (self.window.document["character"], self.window.identity.dump()):
            self.assertEqual((character["home_x"], character["home_y"]), (9, 8))
        self.assertIn("Save project", self.page.interior_notice.text())
        self.window.document["world"] = self.page.dump()
        save_project(self.window.document, self.window.project_file)
        reopened = load_project(self.window.project_file)
        self.assertEqual((reopened["character"]["home_x"], reopened["character"]["home_y"]), (9, 8))
        saved_home = reopened["world"]["locations"][0]
        self.assertEqual((saved_home["exit_x"], saved_home["exit_y"]), (10, 11))

    def test_room_move_updates_live_routes_and_scene_starts_without_rewriting_relative_moves(self):
        record, design = self.moving_home()
        character = deepcopy(self.window.document["character"])
        exported_map = exported_location_id(record, character)
        character["schedule"] = [
            {"time": "600", "location": "TheirHome", "x": 7, "y": 8, "facing": "down", "activity": "Read", "custom": "keep"},
            {"time": "900", "location": "Town", "x": 7, "y": 8, "facing": "down", "activity": "Walk"},
        ]
        routine = new_life_record("routines")
        routine.update(stops=[dict(character["schedule"][0], location=exported_map)], custom="keep")
        character.setdefault("life", {})["routines"] = [routine]
        event = new_event(character)
        event.update(location=exported_map, custom="keep")
        actor = new_actor()
        actor.update(x=8, y=9, custom="keep")
        beat = new_beat("move")
        beat.update(x=-1, y=1)
        event["story"].update(stage="ready", actors=[actor], beats=[beat])
        character["events"] = [event]
        self.window.schedule = SchedulePage()
        self.extra_widgets.append(self.window.schedule)
        self.window.schedule.load(character["schedule"])
        self.window.life = LifePage()
        self.window.life.window = self.window
        self.extra_widgets.extend(self.window.life.editors.values())
        self.window.life.load(character)
        self.window.story = StoryPage(self.window)
        self.extra_widgets.append(self.window.story)
        self.window.events = self.window.story.events
        self.window.story.load(character)
        # The document intentionally has stale content; edits exist in the UI.
        self.assertNotEqual(self.window.document["character"]["events"], character["events"])
        self.accept_design(design)
        for snapshot in (self.window.document["character"], {
                "schedule": self.window.schedule.dump(), "life": self.window.life.dump(),
                "events": self.window.events.dump()}):
            self.assertEqual((snapshot["schedule"][0]["x"], snapshot["schedule"][0]["y"]), (9, 8))
            self.assertEqual(snapshot["schedule"][0]["custom"], "keep")
            self.assertEqual(snapshot["schedule"][1], character["schedule"][1])
            stop = snapshot["life"]["routines"][0]["stops"][0]
            self.assertEqual((stop["x"], stop["y"]), (9, 8))
            self.assertEqual(snapshot["life"]["routines"][0]["custom"], "keep")
            scene = snapshot["events"][0]
            self.assertEqual((scene["story"]["actors"][0]["x"], scene["story"]["actors"][0]["y"]), (10, 9))
            self.assertEqual(scene["story"]["actors"][0]["custom"], "keep")
            self.assertEqual(scene["story"]["beats"], [beat])
            self.assertEqual(scene["story"]["stage"], "ready")
            self.assertEqual(scene["custom"], "keep")
        self.assertIn("Room moved: review scene blocking and walking routes.", self.page.interior_notice.text())
        self.assertFalse(self.page.interior_notice.isHidden())

    def test_translated_explicit_home_is_not_mistaken_for_old_entry(self):
        record, design = self.moving_home()
        self.page._update_home("TheirHome", 2, 10)
        self.accept_design(design)
        self.assertEqual((self.window.identity.dump()["home_x"], self.window.identity.dump()["home_y"]), (4, 10))
        self.assertEqual((record["entry_x"], record["entry_y"]), (6, 10))

    def test_room_move_updates_legacy_bundled_characters_and_keeps_unrelated_points(self):
        record, design = self.moving_home()
        bundled = new_companion("Archived character")
        character = bundled["character"]
        character.update(home_map=exported_location_id(record, self.window.document["character"]), home_x=7, home_y=8)
        character["schedule"] = [{"location": "TheirHome", "x": "8", "y": "9", "custom": "keep"},
                                 {"location": "TheirHome", "x": 20, "y": 9}]
        self.page.world["characters"].append(bundled)
        self.accept_design(design)
        self.assertEqual((character["home_x"], character["home_y"]), (9, 8))
        self.assertEqual(character["schedule"][0], {"location": "TheirHome", "x": 10, "y": 9, "custom": "keep"})
        self.assertEqual(character["schedule"][1], {"location": "TheirHome", "x": 20, "y": 9})

    def test_cancelled_room_move_preserves_home_exit_and_authored_destinations(self):
        record, design = self.moving_home()
        self.window.document["character"]["schedule"] = [{"location": "TheirHome", "x": 7, "y": 8}]
        before_world, before_character = self.page.dump(), deepcopy(self.window.document["character"])
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            constructor.return_value.result_design = design
            self.assertFalse(self.page.design_interior())
        self.assertEqual(self.page.dump(), before_world)
        self.assertEqual(self.window.document["character"], before_character)

    def test_room_move_keeps_other_places_entrances_and_return_arrivals_with_room(self):
        record, design = self.moving_home()
        entrances = []
        for alias in ("TheirHome", exported_location_id(record, self.window.document["character"]), "Town"):
            self.page.add_location()
            entrance = self.page.world["locations"][-1]["entrance"]
            entrance.update(map=alias, x=7, y=8, arrival_x=8, arrival_y=9)
            entrances.append(entrance)
        self.page.select_location(0)
        self.accept_design(design)
        for entrance in entrances[:2]:
            self.assertEqual((entrance["x"], entrance["y"], entrance["arrival_x"], entrance["arrival_y"]), (9, 8, 10, 9))
        self.assertEqual(entrances[2], {"map": "Town", "x": 7, "y": 8, "arrival_x": 8, "arrival_y": 9, "confirmed": False})

    def test_cancelled_new_home_leaves_no_pending_record_on_disk_or_in_memory(self):
        before = self.page.dump()
        home = self.window.document["character"]["home_map"]
        spy = QSignalSpy(self.page.changed)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.page.build_home()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(load_project(self.window.project_file)["world"], before)
        self.assertEqual(self.window.document["character"]["home_map"], home)
        self.assertEqual(self.page.location_index, -1)
        self.assertEqual(spy.count(), 0)

    def test_cancel_or_error_creating_another_home_restores_previous_selection(self):
        identity = self.two_locations()
        before = self.page.dump()
        with patch("pixelheart.interior_editor.InteriorEditor", side_effect=ValueError("Missing library")):
            self.page.build_home()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertEqual(load_project(self.window.project_file)["world"], before)
        self.assertIn("Missing library", self.window.errors[-1][1])

    def test_cancelling_project_save_never_creates_a_place_or_opens_designer(self):
        with patch.object(self.window, "ensure_saved", return_value=False), patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            self.page.build_home()
            constructor.assert_not_called()
        self.assertEqual(self.page.world["locations"], [])

    def test_spouse_action_creates_once_and_reopens_existing_room(self):
        self.window.document["character"].update(name="Loki", internal_name="Loki")
        self.window.identity.load(self.window.document["character"])
        before_home = self.window.document["character"]["home_map"]
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = new_interior("spouse")
            self.page.design_spouse_room()
        record = self.page.world["locations"][0]
        self.assertEqual(record["name"], "Loki's spouse room")
        self.assertEqual(record["internal_name"], "LokiSpouseRoom")
        self.assertTrue(record["spouse_room"])
        self.assertEqual(self.window.document["character"]["home_map"], before_home)
        identity = record["id"]
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.page.design_spouse_room()
            self.assertEqual(constructor.call_args.args[1], new_interior("spouse"))
        self.assertEqual(len(self.page.world["locations"]), 1)
        self.assertEqual(self.page.world["locations"][0]["id"], identity)

    def test_advanced_tools_cannot_create_a_second_spouse_room(self):
        self.page.add_location()
        self.page.world["locations"][0].update(spouse_room=True, interior=new_interior("spouse"))
        self.page.add_location()
        self.assertFalse(self.page.location_fields["spouse_room"].isEnabled())
        self.page.location_fields["spouse_room"].setChecked(True)
        self.assertFalse(self.page.world["locations"][1]["spouse_room"])
        self.window.ensure_saved()
        before = self.page.dump()
        with patch("pixelheart.map_workshop.MapWorkshop") as constructor:
            dialog = constructor.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_reference = "assets/maps/unused/map.tmx"
            dialog.result_is_spouse_room = True
            self.page.create_map()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(self.window.errors[-1][0], "Spouse room already exists")

    def test_issue_navigation_reveals_advanced_dependency_editor(self):
        self.page.add_dependency()
        self.page.open_issue("world.dependencies.0.minimum_version")
        self.assertTrue(self.page.tabs.isTabVisible(1))
        self.assertEqual(self.page.tabs.currentIndex(), 1)
        self.assertEqual(self.page.dependency_list.currentRow(), 0)

    def test_build_home_creates_unique_safe_identity_and_preserves_existing_place(self):
        self.window.document["character"].update(name="Loki", internal_name="Loki")
        self.window.identity.load(self.window.document["character"])
        self.page.add_location()
        existing = self.page.world["locations"][0]
        existing.update(name="Existing home", internal_name="lokihome")
        before = deepcopy(existing)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = new_interior()
            self.page.build_home()
        self.assertEqual(self.page.world["locations"][0], before)
        self.assertEqual(self.page.world["locations"][1]["internal_name"], "LokiHome2")

    def test_place_limit_blocks_new_home_but_existing_spouse_room_can_be_edited(self):
        records = []
        for index in range(32):
            record = new_location()
            record.update(name=f"Place {index}", internal_name=f"Place{index}")
            records.append(record)
        records[3].update(spouse_room=True, interior=new_interior("spouse"))
        self.page.load({**self.page.dump(), "locations": records})
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            self.page.build_home()
            constructor.assert_not_called()
            self.assertIn("32 places", self.window.errors[-1][1])
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.page.design_spouse_room()
            constructor.assert_called_once()
        self.assertEqual(len(self.page.world["locations"]), 32)
        self.assertEqual(self.page.location_index, 3)


if __name__ == "__main__":
    unittest.main()
