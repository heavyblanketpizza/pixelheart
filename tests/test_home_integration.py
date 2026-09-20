"""Home edits survive the desktop's collect, save, and companion flows."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow
from pixelheart_core.projects import load_project
from pixelheart_core.world import new_companion, new_world, new_location
from pixelheart_core.homes import assign_home
from pixelheart_core.exporting import build_mod_archive
from pixelheart_core.world import exported_location_id, map_bundle, asset_path


class HomeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_primary_home_and_route_survive_collect_save_and_reload(self):
        window = self.window
        before = deepcopy(window.document["character"])
        edited = assign_home(before, "SeedShop", 4, 5, "left", move_route_stops=True)
        window.apply_home_edit(edited, window.world.dump())
        window.collect()
        self.assertTrue(window.dirty)
        self.assertEqual(window.document["character"]["home_facing"], "left")
        self.assertEqual(window.document["character"]["schedule"][0]["location"], "SeedShop")
        self.assertEqual(window.document["character"]["schedule"][0]["id"], before["schedule"][0]["id"])
        self.assertEqual(window.document["character"]["dialogues"], before["dialogues"])
        target = self.root / "character.json"
        self.assertTrue(window.save_to(target))
        reopened = load_project(target)
        self.assertEqual(reopened["character"]["home_x"], 4)
        self.assertEqual(reopened["character"]["home_facing"], "left")
        window.load_document(reopened, target)
        self.assertEqual(window.identity.fields["home_facing"].currentData(), "left")

    def test_companion_home_does_not_change_primary_or_other_companion(self):
        window = self.window
        world = new_world()
        first, second = new_companion("First"), new_companion("Second")
        world["characters"] = [first, second]
        window.world.load(world)
        window.world.cast_list.setCurrentRow(1)
        window.collect()
        primary_before = deepcopy(window.document["character"])
        edited = assign_home(second["character"], "Saloon", 8, 9, "up", move_route_stops=True)
        window.apply_home_edit(edited, world, second["id"])
        window.collect()
        self.assertEqual(window.document["character"], primary_before)
        self.assertEqual(window.document["world"]["characters"][0], first)
        self.assertEqual(window.document["world"]["characters"][1]["character"], edited)
        self.assertEqual(window.world.cast_index, 1)
        self.assertEqual(window.world.cast_fields["home_facing"].currentData(), "up")
        target = self.root / "companion.json"
        self.assertTrue(window.save_to(target))
        self.assertEqual(load_project(target)["world"]["characters"][1]["character"]["home_facing"], "up")

    def test_cancel_and_unchanged_apply_leave_saved_project_clean(self):
        window = self.window
        self.assertTrue(window.save_to(self.root / "project.json"))
        before = deepcopy(window.document)
        with patch("pixelheart.home_editor.HomeDialog") as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Rejected
            window.open_home_editor()
        self.assertEqual(window.document, before)
        self.assertFalse(window.dirty)
        window.apply_home_edit(before["character"], before["world"])
        self.assertFalse(window.dirty)

    def test_assign_resident_opens_selected_place_and_applies_on_accept(self):
        window = self.window
        world = window.world.dump()
        place = new_location()
        place.update(name="River cottage", internal_name="RiverCottage")
        world["locations"].append(place)
        window.world.load(world)
        window.collect()
        edited = assign_home(window.document["character"], "RiverCottage", 6, 6)
        with patch("pixelheart.home_editor.HomeDialog") as factory:
            dialog = factory.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_character = edited
            dialog.result_world = world
            window.world.assign_resident()
            dialog.select_home_map.assert_called_once_with("RiverCottage")
        self.assertEqual(window.document["character"]["home_map"], "RiverCottage")
        self.assertIn(window.document["character"]["name"], window.world.residents.text())

    def test_identity_home_button_opens_editor_without_requiring_a_save(self):
        with patch("pixelheart.home_editor.HomeDialog") as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.identity.home_button.click()
            factory.assert_called_once()
        self.assertIsNone(self.window.project_file)

    def test_resident_home_cannot_be_removed_and_silently_become_an_external_map(self):
        window = self.window
        place = new_location()
        world = window.world.dump()
        world["locations"].append(place)
        companion = new_companion("Robin's friend")
        companion["character"] = assign_home(companion["character"], place["internal_name"], 2, 2)
        world["characters"].append(companion)
        window.world.load(world)
        window.collect()
        with patch.object(window, "show_error") as error:
            window.world.remove_location()
            self.assertEqual(window.world.dump(), world)
            self.assertIn("Robin's friend", error.call_args.args[1])
        companion["character"] = assign_home(companion["character"], "Town", 32, 62)
        window.world.load(world)
        window.collect()
        window.world.remove_location()
        self.assertEqual(window.document["world"]["locations"], [])

    def test_legacy_home_facing_resets_when_loading_another_character(self):
        window = self.window
        edited = assign_home(window.document["character"], "Town", 32, 62, "up")
        window.identity.load(edited)
        edited.pop("home_facing")
        window.identity.load(edited)
        self.assertEqual(window.identity.fields["home_facing"].currentData(), "down")

    def test_painted_home_survives_first_save_save_as_and_export_reopening(self):
        from pixelheart.home_editor import HomeDialog
        from pixelheart.map_workshop import MapWorkshop
        window = self.window
        source = self.root / "tiles.png"
        Image.new("RGBA", (32, 16), "#aabc90").save(source)
        project = self.root / "first" / "character.json"

        def ensure_saved():
            return project if window.save_to(project) else None

        def paint(workshop):
            self.assertEqual(workshop.preset.currentData(), "home_interior")
            workshop.load_tilesheet(source)
            workshop.select_tile(1)
            workshop.select_room_tile("floor")
            workshop.select_tile(2)
            workshop.select_room_tile("wall")
            workshop.layout_room()
            self.assertTrue(workshop.save_map())
            return QDialog.DialogCode.Accepted

        dialog = HomeDialog(window.document["character"], world=window.world.dump(), ensure_saved=ensure_saved)
        try:
            dialog.create_home()
            dialog.home_facing.setCurrentIndex(dialog.home_facing.findData("right"))
            dialog.move_route_stops.setChecked(True)
            with patch.object(MapWorkshop, "exec", paint):
                dialog.create_map()
            dialog.apply()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            window.apply_home_edit(dialog.result_character, dialog.result_world)
        finally:
            dialog.deleteLater()
        self.assertEqual(window.project_file, project)
        self.assertTrue(window.dirty)
        window.collect()
        first_home = window.document["world"]["locations"][0]
        original_map = asset_path(first_home["map"], project.parent).read_bytes()
        self.assertTrue(window.save_to(self.root / "moved" / "character.json"))
        moved = load_project(window.project_file)
        moved_home = moved["world"]["locations"][0]
        self.assertEqual(asset_path(moved_home["map"], window.project_file.parent).read_bytes(), original_map)
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192), "#bbbb99").save(portrait)
        Image.new("RGBA", (64, 416), "#bbbb99").save(sprite)
        blob = build_mod_archive(moved["character"], portrait, sprite,
                                 project_document=moved, project_root=window.project_file.parent)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            archive.extractall(self.root / "export")
            root = "[CP] " + moved["character"]["internal_name"] + "/"
            content = json.loads(archive.read(root + "content.json"))
            npc = next(iter(next(item for item in content["Changes"] if item["Target"] == "Data/Characters")["Entries"].values()))
            self.assertEqual(npc["Home"][0]["Direction"], "right")
            self.assertEqual(npc["Home"][0]["Location"], exported_location_id(moved_home, moved["character"]))
        reopened_path = self.root / "export" / root / "project.json"
        exported = load_project(reopened_path)
        exported_home = exported["world"]["locations"][0]
        bundle = map_bundle(asset_path(exported_home["map"], reopened_path.parent))
        self.assertEqual((bundle["width"], bundle["height"]), (12, 12))
        self.assertEqual(exported["character"]["home_map"], moved_home["internal_name"])
        self.assertEqual(exported["character"]["home_facing"], "right")
        self.assertEqual(exported["character"]["schedule"][0]["location"], moved_home["internal_name"])


if __name__ == "__main__":
    unittest.main()
