"""Home authoring stays transactional and its tile controls match supplied maps."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.home_editor import HomeDialog
from pixelheart.map_workshop import TileMapDraft, inspect_tilesheet
from pixelheart_core.projects import new_project
from pixelheart_core.world import new_companion, new_location, new_world


class HomeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pixelheart-home-ui-")
        self.root = Path(self.directory.name)
        self.project = self.root / "project.json"
        self.project.write_text("{}")
        sheet = self.root / "tiles.png"
        Image.new("RGBA", (16, 16), "#e8d2ad").save(sheet)
        draft = TileMapDraft("home_interior")
        draft.fill("Back", 1)
        (self.root / "home.tmx").write_bytes(draft.to_tmx(inspect_tilesheet(sheet)))
        self.character = new_project()["character"]
        self.character.update(name="Mira", internal_name="Mira")
        self.world = new_world()
        self.location = new_location()
        self.location.update(name="A quiet home", internal_name="QuietHome", map="home.tmx")
        self.world["locations"].append(self.location)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def dialog(self, **kwargs):
        result = HomeDialog(self.character, self.world, **kwargs)
        self.dialogs.append(result)
        return result

    def test_cancel_discards_assignment_location_creation_and_place_edits(self):
        before_character, before_world = deepcopy(self.character), deepcopy(self.world)
        dialog = self.dialog(project_file=self.project)
        dialog.select_home_map("QuietHome")
        dialog.location_fields["name"].setText("A changed name")
        dialog.create_home()
        dialog.home_x.setValue(7)
        dialog.reject()
        self.assertEqual(self.character, before_character)
        self.assertEqual(self.world, before_world)
        self.assertIsNone(dialog.result_character)
        self.assertIsNone(dialog.result_world)

    def test_assignment_preserves_routes_by_default_and_apply_returns_copies(self):
        dialog = self.dialog(project_file=self.project)
        dialog.select_home_map("QuietHome")
        dialog.home_x.setValue(6)
        dialog.home_y.setValue(6)
        dialog.home_facing.setCurrentIndex(dialog.home_facing.findData("left"))
        dialog.apply()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.result_character["schedule"], self.character["schedule"])
        self.assertEqual(dialog.result_character["home_facing"], "left")
        self.assertEqual(dialog.result_character["home_map"], "QuietHome")
        dialog.result_world["locations"][0]["name"] = "Independent result"
        self.assertEqual(self.world["locations"][0]["name"], "A quiet home")

    def test_opt_in_moves_matching_stops_but_preserves_stop_facing(self):
        self.character["schedule"][0]["facing"] = "right"
        dialog = self.dialog()
        self.assertIn("1 matching stop", dialog.route_hint.text())
        dialog.home_map.setText("Beach")
        dialog.home_x.setValue(8)
        dialog.home_y.setValue(9)
        dialog.move_route_stops.setChecked(True)
        dialog.apply()
        stop = dialog.result_character["schedule"][0]
        self.assertEqual((stop["location"], stop["x"], stop["y"], stop["facing"]), ("Beach", 8, 9, "right"))
        self.assertEqual(self.character["schedule"][0]["location"], "Town")

    def test_new_home_draft_can_apply_without_assets_and_stable_id_follows_edits(self):
        dialog = self.dialog()
        dialog.create_home()
        self.assertFalse(dialog.paint_button.isEnabled())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertIn("Save the project", dialog.map_status.text())
        dialog.location_fields["internal_name"].setText("MiraCottage")
        self.assertEqual(dialog.home_map.value(), "MiraCottage")
        self.assertEqual((dialog.home_x.value(), dialog.home_y.value()), (6, 6))
        self.assertIn("draft", dialog.status.text())
        dialog.apply()
        self.assertEqual(dialog.result_character["home_map"], "MiraCottage")
        self.assertEqual(len(dialog.result_world["locations"]), 2)
        self.assertIsNone(dialog.result_world["locations"][-1]["map"])

    def test_known_map_blocks_invalid_numeric_tiles_and_bounds_keyboard(self):
        dialog = self.dialog(project_file=self.project)
        dialog.select_home_map("QuietHome")
        self.assertEqual(dialog.canvas.map_size, (12, 12))
        self.assertFalse(dialog.apply_button.isEnabled())  # Legacy Town tile lies outside this home.
        self.assertIn("outside", dialog.status.text())
        dialog.home_x.setValue(11)
        dialog.home_y.setValue(11)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Right)
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Down)
        self.assertEqual((dialog.home_x.value(), dialog.home_y.value()), (11, 11))
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Left)
        self.assertEqual(dialog.home_x.value(), 10)
        self.assertTrue(dialog.apply_button.isEnabled())

    def test_click_places_resident_and_door_markers_are_reference_only(self):
        dialog = self.dialog(project_file=self.project)
        dialog.select_home_map("QuietHome")
        dialog.show()
        self.app.processEvents()
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=dialog.canvas.tile_point(4, 5).toPoint())
        self.assertEqual((dialog.home_x.value(), dialog.home_y.value()), (4, 5))
        self.assertEqual(len(dialog.canvas.actors), 1)
        self.assertEqual(len(dialog.canvas.references), 2)
        self.assertEqual(dialog.world["locations"][0]["entry_x"], 2)

    def test_grid_only_viewport_stays_stable_while_placing_and_using_arrows(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        bounds = dialog.canvas.bounds
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Right)
        self.assertEqual(dialog.canvas.bounds, bounds)
        tile = (self.character["home_x"] + 3, self.character["home_y"] + 2)
        point = dialog.canvas.tile_point(*tile).toPoint()
        QTest.mouseClick(dialog.canvas, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(dialog.canvas.bounds, bounds)
        self.assertEqual((dialog.home_x.value(), dialog.home_y.value()), tile)
        self.assertEqual(dialog.canvas.tile_at(point), tile)

    def test_selection_does_not_overwrite_place_details_and_spouse_is_unavailable(self):
        spouse = new_location()
        spouse.update(internal_name="SpouseSection", spouse_room=True)
        self.world["locations"].append(spouse)
        dialog = self.dialog()
        self.assertEqual(dialog.home_map.combo.findData("SpouseSection"), -1)
        dialog.select_home_map("QuietHome")
        self.assertEqual(dialog.world, self.world)
        self.assertTrue(dialog.location_fields["internal_name"].isReadOnly())
        dialog.select_home_map("SpouseSection")
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("spouse-room", dialog.status.text())

    def test_invalid_door_and_new_id_errors_prevent_apply(self):
        dialog = self.dialog(project_file=self.project)
        dialog.create_home()
        dialog.location_fields["internal_name"].setText("Town")
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("different", dialog.status.text())
        dialog.location_fields["internal_name"].setText("Cottage")
        dialog.entrance_fields["map"].setText("Cottage")
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("loop", dialog.status.text())
        dialog.entrance_fields["map"].setText("Beach")
        dialog.location_fields["exit_x"].setValue(6)
        dialog.location_fields["exit_y"].setValue(10)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("different arrival", dialog.status.text())

    def test_invalid_new_id_stays_pending_without_rewriting_an_existing_place(self):
        linked = new_location()
        linked.update(internal_name="Workshop")
        linked["entrance"]["map"] = "QuietHome"
        self.world["locations"].append(linked)
        dialog = self.dialog()
        dialog.create_home()
        created_alias = dialog.home_map.value()
        dialog.location_fields["internal_name"].setText("QuietHome")
        self.assertEqual(dialog.home_map.value(), created_alias)
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.location_fields["internal_name"].setText("MiraCabin")
        self.assertEqual(dialog.world["locations"][1]["entrance"]["map"], "QuietHome")

    def test_switching_away_cannot_hide_invalid_new_home_edits(self):
        dialog = self.dialog()
        dialog.create_home()
        alias = dialog.home_map.value()
        dialog.location_fields["internal_name"].setText("invalid id")
        dialog.select_home_map("Beach")
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn("stable map ID", dialog.status.text())
        dialog.select_home_map(alias)
        self.assertEqual(dialog.location_fields["internal_name"].text(), "invalid id")

    def test_companion_home_uses_primary_location_namespace_and_resident_names(self):
        companion = new_companion("Pip")
        self.world["characters"].append(companion)
        self.character.update(home_map="QuietHome", home_x=4, home_y=5)
        dialog = HomeDialog(companion["character"], self.world, primary=self.character, project_file=self.project)
        self.dialogs.append(dialog)
        dialog.select_home_map("QuietHome")
        self.assertIn("Mira", dialog.residents.text())
        self.assertIn("Pip", dialog.residents.text())

    def test_import_saves_project_on_demand_and_cancel_keeps_world_unmodified(self):
        calls = []
        dialog = self.dialog(ensure_saved=lambda: calls.append("save") or self.project)
        dialog.create_home()
        self.assertTrue(dialog.import_button.isEnabled())
        with patch("pixelheart.home_editor.QFileDialog.getOpenFileName", return_value=(str(self.root / "home.tmx"), "")):
            dialog.import_location()
        self.assertEqual(calls, ["save"])
        self.assertEqual(dialog.canvas.map_size, (12, 12))
        imported = dialog._location()["map"]
        self.assertTrue((self.root / imported).is_file())
        dialog.reject()
        self.assertEqual(len(self.world["locations"]), 1)
        self.assertIsNone(dialog.result_world)

    def test_home_painter_uses_interior_preset_and_blocks_spouse_room(self):
        dialog = self.dialog(project_file=self.project)
        dialog.create_home()
        with patch("pixelheart.map_workshop.MapWorkshop") as constructor:
            workshop = constructor.return_value
            workshop.preset.findData.return_value = 1
            workshop.exec.return_value = QDialog.DialogCode.Accepted
            workshop.result_reference = "home.tmx"
            workshop.result_is_spouse_room = False
            dialog.create_map()
            workshop.set_preset.assert_called_once_with("home_interior")
            workshop.preset.model.return_value.item.return_value.setEnabled.assert_called_once_with(False)
            self.assertEqual(dialog._location()["map"], "home.tmx")
        self.assertEqual(dialog.canvas.map_size, (12, 12))


if __name__ == "__main__":
    unittest.main()
