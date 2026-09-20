"""Home assignment stays explicit and exports the same location as its routes."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.exporting import build_mod_archive, validate_character
from pixelheart_core.homes import assign_home, home_issues, matching_home_stops, new_home_location
from pixelheart_core.projects import ProjectError, load_project, new_project, save_project
from pixelheart_core.validation import DraftValidationError, validate_draft
from pixelheart_core.world import exported_location_id, new_world, world_issues


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = new_project()
        self.character = self.document["character"]
        self.character.update(name="Mira", internal_name="Mira", romanceable=False)
        self.world = new_world()

    def map(self, properties=""):
        path = self.root / "room.tmx"
        Image.new("RGBA", (16, 16)).save(self.root / "tiles.png")
        layers = "".join(f'<layer name="{name}" width="12" height="12"><data encoding="csv">'
                         + ",".join(["1" if name == "Back" else "0"] * 144) + "</data></layer>"
                         for name in ("Back", "Buildings", "Front"))
        path.write_text('<map width="12" height="12" tilewidth="16" tileheight="16">'
                        + properties + '<tileset firstgid="1" name="tiles" tilewidth="16" tileheight="16" tilecount="1" columns="1">'
                        '<image source="tiles.png" width="16" height="16"/></tileset>' + layers + "</map>")
        location = new_home_location(self.character, self.world)
        location["map"] = path.name
        self.world["locations"].append(location)
        return location

    def test_assignment_is_independent_and_route_changes_are_opt_in(self):
        self.character["schedule"][0].update(activity="Breakfast", facing="left", extension={"keep": True})
        self.character["schedule"].append({**self.character["schedule"][0], "id": "neighbour", "x": 33})
        self.character["life"] = {"routines": [{"id": "routine", "enabled": False, "stops": [
            {**self.character["schedule"][0], "id": "return"},
            {**self.character["schedule"][0], "id": "married", "location": "bed"},
        ]}]}
        original = copy.deepcopy(self.character)
        self.assertEqual(matching_home_stops(self.character), ["schedule.0", "life.routines.0.stops.0"])
        assigned = assign_home(self.character, "MiraHome", 6, 6, "up")
        self.assertEqual(assigned["schedule"], original["schedule"])
        moved = assign_home(self.character, "MiraHome", 6, 6, "up", move_route_stops=True)
        self.assertEqual(moved["schedule"][0], {**original["schedule"][0], "location": "MiraHome", "x": 6, "y": 6})
        self.assertEqual(moved["schedule"][1], original["schedule"][1])
        self.assertEqual(moved["life"]["routines"][0]["stops"][1], original["life"]["routines"][0]["stops"][1])
        self.assertEqual(moved["life"]["routines"][0]["stops"][0]["location"], "MiraHome")
        self.assertEqual(self.character, original)
        moved["schedule"][0]["extension"]["keep"] = False
        self.assertTrue(self.character["schedule"][0]["extension"]["keep"])

    def test_new_home_uses_unique_bounded_ids_and_room_door_coordinates(self):
        first = new_home_location(self.character, self.world)
        self.world["locations"].append(first)
        second = new_home_location(self.character, self.world)
        self.assertEqual((first["internal_name"], second["internal_name"]), ("MiraHome", "MiraHome2"))
        self.assertEqual((first["entrance"]["x"], second["entrance"]["x"]), (32, 33))
        self.assertEqual((first["entry_x"], first["entry_y"], first["exit_x"], first["exit_y"]), (6, 10, 6, 11))
        self.assertIsNone(first["map"])
        long_name = {**self.character, "internal_name": "A" * 64}
        self.assertLessEqual(len(new_home_location(long_name)["internal_name"]), 40)

    def test_direction_migrates_for_legacy_projects_and_survives_save(self):
        self.character.pop("home_facing")
        path = self.root / "project.json"
        save_project(self.document, path)
        self.assertEqual(load_project(path)["character"]["home_facing"], "down")
        self.character["home_facing"] = "left"
        save_project(self.document, path)
        self.assertEqual(load_project(path)["character"]["home_facing"], "left")
        for invalid in (None, "north", [], 2):
            with self.subTest(facing=invalid):
                with self.assertRaises(DraftValidationError):
                    validate_draft({"home_facing": invalid})
                self.character["home_facing"] = invalid
                with self.assertRaises(ProjectError):
                    save_project(self.document, path)

    def test_mapless_home_is_saveable_but_world_export_still_blocks(self):
        location = new_home_location(self.character)
        self.world["locations"].append(location)
        assigned = assign_home(self.character, location["internal_name"], 6, 6)
        issues = home_issues(assigned, self.world, self.root)
        self.assertTrue(any(issue["code"] == "home_map_missing" and issue["level"] == "warning" for issue in issues))
        self.assertFalse(any(issue["level"] == "error" for issue in issues))
        save_project({**self.document, "character": assigned, "world": self.world}, self.root / "project.json")
        self.assertTrue(any(issue["level"] == "error" and issue["field"] == "world.locations.0.map"
                            for issue in world_issues(self.world, assigned, self.root)))

    def test_home_bounds_spouse_sections_and_warps_cover_alias_and_exported_ids(self):
        location = self.map('<properties><property name="NPCWarp" value="3 4 Town 32 62"/></properties>')
        for name in (location["internal_name"], exported_location_id(location, self.character)):
            with self.subTest(name=name):
                assigned = assign_home(self.character, name, 12, 6)
                self.assertIn("home_out_of_bounds", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})
                for x, y in ((6, 11), (3, 4)):
                    assigned = assign_home(self.character, name, x, y)
                    self.assertIn("home_on_warp", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})
                location["spouse_room"] = True
                self.assertIn("home_spouse_room", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})
                location["spouse_room"] = False

    def test_route_warning_uses_resolved_home_alias_and_does_not_rewrite_schedule(self):
        location = self.map()
        assigned = assign_home(self.character, location["internal_name"], 6, 6)
        self.assertIn("home_route_away", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})
        assigned["schedule"][-1]["location"] = exported_location_id(location, self.character)
        self.assertNotIn("home_route_away", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})

    def test_source_collision_advisory_honors_npc_passable_tiles(self):
        import xml.etree.ElementTree as ET
        location = self.map()
        path = self.root / location["map"]
        xml = ET.parse(path)
        layer = next(item for item in xml.getroot().findall("layer") if item.get("name") == "Buildings")
        gids = ["0"] * 144
        gids[6 * 12 + 6] = "1"
        layer.find("data").text = ",".join(gids)
        xml.write(path)
        assigned = assign_home(self.character, location["internal_name"], 6, 6)
        self.assertIn("home_tile_obstacle", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})
        tile = ET.SubElement(xml.getroot().find("tileset"), "tile", {"id": "0"})
        properties = ET.SubElement(tile, "properties")
        ET.SubElement(properties, "property", {"name": "NPCPassable", "value": "T"})
        xml.write(path)
        self.assertNotIn("home_tile_obstacle", {issue["code"] for issue in home_issues(assigned, self.world, self.root)})

    def test_inspection_preserves_legacy_export_coordinate_and_map_ranges(self):
        legacy = {**self.character, "home_map": "Author." + "A" * 110, "home_x": "4095", "home_y": "1001"}
        self.assertFalse([issue for issue in home_issues(legacy) if issue["level"] == "error"])
        self.assertFalse([issue for issue in validate_character(legacy) if issue["level"] == "error" and issue["field"].startswith("home_")])
        for invalid in ("9" * 5000, True, None, [], 2.5, "NaN"):
            with self.subTest(value=repr(invalid)[:30]):
                issues = home_issues({**legacy, "home_x": invalid})
                self.assertTrue(any(issue["field"] == "home_x" and issue["level"] == "error" for issue in issues))

    def test_vanilla_home_playtest_position_and_facing_are_in_readme(self):
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        Image.new("RGBA", (64, 128)).save(sprite)
        assigned = assign_home(self.character, "SeedShop", 8, 12, "up")
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(assigned, portrait, sprite))) as archive:
            readme = archive.read("[CP] Mira/README.txt").decode()
            self.assertIn("Home: SeedShop at 8, 12; facing up.", readme)
            self.assertIn("final\nstop at night", readme)

    def test_custom_home_pack_has_matching_spawn_schedule_map_and_two_way_doors(self):
        location = self.map()
        assigned = assign_home(self.character, location["internal_name"], 6, 6, "right", move_route_stops=True)
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        Image.new("RGBA", (64, 128)).save(sprite)
        blob = build_mod_archive(assigned, portrait, sprite, world=self.world, project_root=self.root)
        identity = exported_location_id(location, self.character)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            changes = content["Changes"]
            npc = next(iter(next(patch for patch in changes if patch["Target"] == "Data/Characters")["Entries"].values()))
            self.assertEqual(npc["Home"], [{"Id": "Default", "Location": identity, "Tile": {"X": 6, "Y": 6}, "Direction": "right"}])
            schedule = json.loads(archive.read("[CP] Mira/assets/schedule.json"))
            self.assertEqual(schedule["spring"], f"600 {identity} 6 6 2")
            location_data = next(patch for patch in changes if patch["Target"] == "Data/Locations")["Entries"][identity]
            self.assertEqual(location_data["CreateOnLoad"], {"MapPath": "Maps/" + identity})
            warps = [warp for patch in changes for warp in patch.get("AddWarps", [])]
            self.assertIn(f"32 62 {identity} 6 10", warps)
            self.assertIn("6 11 Town 32 63", warps)
            guide = archive.read("[CP] Mira/WORLD_TESTING.txt").decode()
            self.assertIn(f"Home: {identity} at 6, 6; facing right.", guide)
            self.assertIn("verify each changed route and its final stop", guide)
        invalid = {**assigned, "home_facing": "north"}
        self.assertTrue(any(issue["level"] == "error" and issue["field"] == "home_facing"
                            for issue in validate_character(invalid, portrait, sprite, world=self.world, project_root=self.root)))


if __name__ == "__main__":
    unittest.main()
