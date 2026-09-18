import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.projects import new_project, load_project
from pixelheart_core.exporting import build_mod_archive, validate_character, ExportValidationError
from pixelheart_core.world import new_world, new_companion, new_location, import_map, exported_location_id
from pixelheart_core.story import new_event, new_beat, exported_npc_id, event_game_id
from pixelheart_core.life import new_life_record
try:
    from .test_world import make_map
except ImportError:
    from test_world import make_map


class CreatorExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.character = new_project()["character"]
        self.character.update(id="story-world", name="Mira", internal_name="Mira", romanceable=False)
        self.world = new_world()
        self.portrait = self.root / "portrait.png"
        self.sprite = self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(self.portrait)
        Image.new("RGBA", (64, 128)).save(self.sprite)

    def archive(self):
        return zipfile.ZipFile(io.BytesIO(build_mod_archive(self.character, self.portrait, self.sprite, world=self.world, project_root=self.root)))

    def read(self, archive, name):
        return json.loads(archive.read("[CP] Mira/" + name))

    def companion(self):
        entry = new_companion("Grogu", "child")
        entry["artwork"] = {"portrait": "portrait.png", "sprite": "sprite.png"}
        self.world["characters"].append(entry)
        return entry

    def test_companion_only_scene_guide_uses_parent_pack_identity(self):
        companion = self.companion()
        event = new_event(companion["character"])
        event["story"].update(stage="ready", beats=[{**new_beat(), "text": "Welcome.$h"}])
        companion["character"]["events"] = [event]
        with self.archive() as archive:
            guide = archive.read("[CP] Mira/STORY_TESTING.txt").decode()
            mod_id = self.read(archive, "manifest.json")["UniqueID"]
            self.assertIn(event_game_id(event, companion["character"]).replace("{{ModId}}", mod_id), guide)
            self.assertIn("Grogu", archive.read("[CP] Mira/WORLD_TESTING.txt").decode())

    def location(self, spouse=False):
        location = new_location()
        location.update(name="A hidden workshop", internal_name="Workshop", spouse_room=spouse)
        location["map"] = import_map(make_map(self.root / "map-source"), self.root / "character.json")
        self.world["locations"].append(location)
        return location

    def test_one_archive_contains_real_nonromance_child_companion_and_resolved_cast(self):
        companion = self.companion()
        event = new_event(self.character)
        event["story"].update(stage="ready", actors=event["story"]["actors"] + [{"id": "guest", "name": "Grogu", "x": 33, "y": 62, "facing": 3}],
                              beats=[{**new_beat(), "actor": "Grogu", "text": "An unlikely friend.$h"}])
        self.character["events"] = [event]
        original = copy.deepcopy((self.character, self.world))
        with self.archive() as archive:
            content = self.read(archive, "content.json")
            characters = {key: value for patch in content["Changes"] if patch["Target"] == "Data/Characters" for key, value in patch["Entries"].items()}
            child_id = exported_npc_id(companion["character"])
            self.assertEqual(len(characters), 2)
            self.assertEqual(characters[child_id]["Age"], "Child")
            self.assertFalse(characters[child_id]["CanBeRomanced"])
            events = next(patch for patch in content["Changes"] if patch["Target"] == "Data/Events/Town")
            self.assertIn("speak " + child_id, next(iter(events["Entries"].values())))
            for patch in content["Changes"]:
                if patch["Action"] == "Load":
                    self.assertIn("[CP] Mira/" + patch["FromFile"], archive.namelist())
            self.assertEqual(self.read(archive, "project.json")["character"], self.character)
            archive.extractall(self.root / "reopened")
        reopened = load_project(self.root / "reopened" / "[CP] Mira" / "project.json")
        child = reopened["world"]["characters"][0]
        self.assertTrue((self.root / "reopened" / "[CP] Mira" / child["artwork"]["portrait"]).exists())
        self.assertEqual((self.character, self.world), original)

    def test_imported_location_has_creation_assets_two_way_warps_and_resolved_routines(self):
        location = self.location()
        self.character["home_map"] = "Workshop"
        self.character.update(home_x=2, home_y=2)
        self.character["schedule"][0]["location"] = "Workshop"
        self.character["schedule"][0].update(x=2, y=2)
        routine = new_life_record("routines", self.character)
        routine.update(enabled=True, name="Workshop days")
        self.character["life"] = {"routines": [routine]}
        identity = exported_location_id(location, self.character)
        with self.archive() as archive:
            content = self.read(archive, "content.json")
            changes = content["Changes"]
            created = next(patch for patch in changes if patch["Target"] == "Data/Locations")
            self.assertEqual(created["Entries"][identity]["CreateOnLoad"]["MapPath"], "Maps/" + identity)
            warps = [patch for patch in changes if "AddWarps" in patch]
            self.assertEqual(len(warps), 2)
            self.assertIn(f"32 62 {identity} 2 2", warps[0]["AddWarps"])
            self.assertIn("2 3 Town 32 63", warps[1]["AddWarps"])
            self.assertIn(identity, self.read(archive, "assets/schedule.json")["spring"])
            changed_route = next(patch for patch in changes if patch["Action"] == "EditData" and patch["Target"].startswith("Characters/schedules/"))
            self.assertTrue(all(identity in text for text in changed_route["Entries"].values()))
            map_load = next(patch for patch in changes if patch["Action"] == "Load" and patch["Target"] == "Maps/" + identity)
            parent = Path(map_load["FromFile"]).parent.as_posix()
            for name in ("room.tmx", "room.tsx", "tiles.png"):
                self.assertIn("[CP] Mira/" + parent + "/" + name, archive.namelist())

    def test_spouse_room_uses_documented_map_asset_and_rect_without_separate_location(self):
        self.character["romanceable"] = True
        Image.new("RGBA", (64, 416)).save(self.sprite)
        location = self.location(spouse=True)
        with self.archive() as archive:
            changes = self.read(archive, "content.json")["Changes"]
            npc = next(iter(next(patch for patch in changes if patch["Target"] == "Data/Characters")["Entries"].values()))
            self.assertEqual(npc["SpouseRoom"], {"MapAsset": exported_location_id(location, self.character), "MapSourceRect": {"X": 0, "Y": 0, "Width": 6, "Height": 9}})
            self.assertFalse(any(patch["Target"] == "Data/Locations" or "AddWarps" in patch for patch in changes))

    def test_explicit_dependencies_and_repeat_dependency_are_written_once(self):
        event = new_event(self.character)
        event["story"].update(stage="ready", repeat="daily", beats=[{**new_beat(), "text": "Back again.$h"}])
        self.character["events"] = [event]
        self.world["dependencies"] = [{"id": "Example.CustomMaps", "minimum_version": "1.2.0", "required": True},
                                       {"id": "misscoriel.eventrepeater", "minimum_version": "7.0.0", "required": False}]
        with self.archive() as archive:
            manifest = self.read(archive, "manifest.json")
            dependencies = {entry["UniqueID"]: entry for entry in manifest["Dependencies"]}
            self.assertEqual(dependencies["misscoriel.eventrepeater"]["MinimumVersion"], "7.0.0")
            self.assertTrue(dependencies["misscoriel.eventrepeater"]["IsRequired"])
            self.assertEqual(len(manifest["Dependencies"]), 2)
            ids = self.read(archive, "content.json")["RepeatEvents"]
            self.assertEqual(ids, [event_game_id(event, self.character).replace("{{ModId}}", manifest["UniqueID"])])

    def test_invalid_companion_artwork_or_minor_romance_blocks_the_entire_pack(self):
        companion = self.companion()
        companion["artwork"]["sprite"] = None
        with self.assertRaises(ExportValidationError) as raised:
            self.archive()
        self.assertTrue(any(issue["field"].startswith("world.characters.0") and issue["level"] == "error" for issue in raised.exception.issues))
        companion["artwork"]["sprite"] = "sprite.png"
        companion["character"]["romanceable"] = True
        with self.assertRaisesRegex(ExportValidationError, "adult only"):
            self.archive()

    def test_unsaved_world_assets_report_errors_without_path_none_failure(self):
        self.companion()
        issues = validate_character(self.character, self.portrait, self.sprite, world=self.world)
        self.assertTrue(any(issue["level"] == "error" and issue["field"].startswith("world.characters") for issue in issues))

    def test_complete_project_backup_preserves_creator_metadata_and_reopens_selected_art(self):
        document = {**new_project(), "character": self.character, "world": self.world,
                    "creator": {"brief": "An unlikely family", "chapters": [{"title": "Trust"}], "playtests": [{"result": "Needs revision"}]},
                    "custom": {"preserve": [1, 2, 3]}}
        blob = build_mod_archive(self.character, self.portrait, self.sprite, world=self.world, project_root=self.root, project_document=document)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            backup = self.read(archive, "project.json")
            self.assertEqual(backup["creator"], document["creator"])
            self.assertEqual(backup["custom"], document["custom"])
            self.assertEqual(backup["artwork"]["portrait"], "assets/portraits.png")
            archive.extractall(self.root / "backup")
        reopened = load_project(self.root / "backup" / "[CP] Mira" / "project.json")
        self.assertEqual(reopened["creator"]["brief"], "An unlikely family")


if __name__ == "__main__":
    unittest.main()
