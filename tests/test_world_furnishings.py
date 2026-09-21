"""Playable map details survive project copies and ordinary pack exports."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from pixelheart_core.exporting import build_mod_archive
from pixelheart_core.projects import copy_project, load_project, new_project, save_project
from pixelheart_core.world import (
    WorldError, compile_world, exported_location_id, map_bundle,
    new_location, new_world, normalize_world, world_issues, world_structure_issues,
)


def furnished_map(folder, *, external=True, filename="UniqueResidenceTiles.png"):
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), (80, 110, 100, 255)).save(folder / filename)
    tileset = ET.Element("tileset", tilewidth="16", tileheight="16", columns="2", tilecount="4")
    ET.SubElement(tileset, "image", source=filename, width="32", height="32")
    root = ET.Element("map", orientation="orthogonal", width="8", height="8", tilewidth="16", tileheight="16")
    if external:
        ET.ElementTree(tileset).write(folder / "room.tsx")
        ET.SubElement(root, "tileset", firstgid="1", source="room.tsx")
    else:
        tileset.set("firstgid", "1")
        root.append(tileset)
    for name in ("Back", "Buildings", "Front"):
        layer = ET.SubElement(root, "layer", name=name, width="8", height="8")
        cells = [1 if name == "Back" else 0] * 64
        if name == "Buildings":
            cells[2 * 8 + 2] = 4  # sheet tile (1, 1), distinct from the floor
            cells[2 * 8 + 4] = 3
        ET.SubElement(layer, "data", encoding="csv").text = ",".join(map(str, cells))
    path = folder / "room.tmx"
    ET.ElementTree(root).write(path)
    return path


class WorldFurnishingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = new_project()
        self.project["character"].update(name="Mira", internal_name="Mira", romanceable=False)
        self.character = self.project["character"]
        self.location = new_location()
        self.source = furnished_map(self.root / "source")
        self.location.update(
            internal_name="QuietRoom", map="source/room.tmx",
            interactions=[{"id": "Mirror", "x": 4, "y": 2, "text": "The glass is cool."}],
            seats=[{"id": "Chair", "x": 2, "y": 2, "direction": "right"}],
        )
        self.world = {**new_world(), "locations": [self.location]}
        self.project["world"] = self.world

    def errors(self):
        return [issue["message"] for issue in world_issues(self.world, self.character, self.root) if issue["level"] == "error"]

    def test_export_uses_real_tilesheet_coordinates_and_namespaced_messages(self):
        before = copy.deepcopy(self.world)
        result = compile_world(self.world, self.character, self.root)
        patches = result["patches"]
        identity = exported_location_id(self.location, self.character)
        messages = next(patch for patch in patches if patch["Target"] == "Strings/StringsFromMaps")
        self.assertEqual(messages["Entries"], {identity + ".Mirror": "The glass is cool."})
        action = next(patch for patch in patches if "MapTiles" in patch)
        self.assertEqual(action["Target"], "Maps/" + identity)
        self.assertEqual(action["MapTiles"], [{"Layer": "Buildings", "Position": {"X": 4, "Y": 2},
                                               "SetProperties": {"Action": "Message " + identity + ".Mirror"}}])
        seats = next(patch for patch in patches if patch["Target"] == "Data/ChairTiles")
        self.assertEqual(seats["Entries"], {"UniqueResidenceTiles/1/1": "1/1/right/default/-1/-1/false"})
        self.assertEqual(self.world, before)
        self.assertEqual(result["world"]["locations"][0]["interactions"], self.location["interactions"])

    def test_inline_tileset_uses_selected_firstgid_not_a_map_tile_index(self):
        path = furnished_map(self.root / "inline", external=False)
        xml = ET.parse(path)
        xml.getroot().find("tileset").set("firstgid", "17")
        for data in xml.getroot().findall("layer/data"):
            data.text = ",".join(str(int(value) + 16 if int(value) else 0) for value in data.text.split(","))
        xml.write(path)
        self.location["map"] = "inline/room.tmx"
        result = compile_world(self.world, self.character, self.root)
        seats = next(patch for patch in result["patches"] if patch["Target"] == "Data/ChairTiles")
        self.assertIn("UniqueResidenceTiles/1/1", seats["Entries"])

    def test_features_reject_missing_buildings_tiles_outside_bounds_and_overlap(self):
        original = copy.deepcopy(self.location)
        for change, message in (({"x": 0, "y": 0}, "existing Buildings"),
                                ({"x": 8}, "outside"), ({"x": 2}, "different tile")):
            with self.subTest(change=change):
                self.location.update(copy.deepcopy(original))
                self.location["interactions"][0].update(change)
                self.assertTrue(any(message in error for error in self.errors()), self.errors())

    def test_malformed_metadata_cannot_be_saved_or_exported(self):
        for field, value in (("seats", {}), ("seats", [{"id": "Chair", "x": True, "y": 2, "direction": "north"}]),
                             ("interactions", [{"id": "with spaces", "x": 4, "y": 2, "text": ""}]),
                             ("entrance_patch", "../escape.tmx"), ("entrance_patch_x", -1)):
            with self.subTest(field=field, value=value):
                altered = copy.deepcopy(self.world)
                altered["locations"][0][field] = value
                with self.assertRaises(WorldError):
                    normalize_world(altered)
        self.location["seats"] *= 2
        self.assertTrue(any("unique ID" in error for error in self.errors()))

    def test_extended_seating_errors_retain_their_project_field(self):
        self.location["seats"][0].update(draw_x=0, draw_y=0, draw_tilesheet="../outside")
        issues = world_structure_issues(self.world)
        self.assertTrue(any(issue["field"] == "world.locations.0.seats.0.draw_tilesheet" for issue in issues))
        with self.assertRaises(WorldError):
            normalize_world(self.world)

    def test_seat_footprints_fit_and_do_not_overlap_inspections_or_other_seats(self):
        seat = self.location["seats"][0]
        # Only the anchor must contain a Buildings tile; a wider seat's second
        # tile can be empty in the map. The rectangle still reserves that tile.
        seat["width"] = 2
        self.assertFalse(self.errors())
        seat["width"] = 3
        self.assertTrue(any("nonoverlapping footprint" in error for error in self.errors()))
        self.location["interactions"] = []
        self.location["seats"].append({"id": "Other", "x": 4, "y": 2, "direction": "left"})
        self.assertTrue(any("nonoverlapping footprint" in error for error in self.errors()))
        self.location["seats"].reverse()
        self.assertTrue(any("nonoverlapping footprint" in error for error in self.errors()))
        self.location["seats"] = [seat]
        for dimensions in ({"width": 7, "height": 1}, {"width": 1, "height": 7}):
            with self.subTest(dimensions=dimensions):
                seat.update(dimensions)
                self.assertTrue(any("footprint extends outside" in error for error in self.errors()))

    def test_expanded_seating_survives_archive_reopen_without_copying_external_overlay(self):
        seat = self.location["seats"][0]
        seat.update(width=2, height=2, direction="opposite", seat_type="custom",
                    offset_x=.5, offset_y=-.25, extra_height=0, draw_x=3, draw_y=4,
                    is_seasonal=True, draw_tilesheet="TileSheets/Author.SeatFront")
        self.world["dependencies"] = [{"id": "Author.Seating", "required": True}]
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192), (100, 90, 80, 255)).save(portrait)
        Image.new("RGBA", (64, 128), (100, 90, 80, 255)).save(sprite)
        self.project["artwork"] = {"portrait": "portrait.png", "sprite": "sprite.png"}
        self.assertFalse(self.errors())
        issues = world_issues(self.world, self.character, self.root)
        self.assertTrue(any(issue["level"] == "warning" and "not verified or copied" in issue["message"] for issue in issues))
        blob = build_mod_archive(self.character, portrait, sprite, project_document=self.project, project_root=self.root)
        expected = {"UniqueResidenceTiles/1/1": "2/2/opposite/custom 0.5 -0.25 0/3/4/true/TileSheets\\\\Author.SeatFront"}
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            patch = next(p for p in content["Changes"] if p["Target"] == "Data/ChairTiles")
            self.assertEqual(patch["Entries"], expected)
            self.assertFalse(any("SeatFront" in name for name in archive.namelist()))
            archive.extractall(self.root / "expanded")
        folder = self.root / "expanded" / "[CP] Mira"
        reopened = load_project(folder / "project.json")
        self.assertEqual(reopened["world"]["locations"][0]["seats"], [seat])
        rebuilt = build_mod_archive(reopened["character"], folder / "assets/portraits.png", folder / "assets/sprites.png",
                                    project_document=reopened, project_root=folder)
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            patch = next(p for p in content["Changes"] if p["Target"] == "Data/ChairTiles")
            self.assertEqual(patch["Entries"], expected)

    def test_global_seat_tile_cannot_have_conflicting_directions(self):
        second = copy.deepcopy(self.location)
        second.update(id="another-place", internal_name="AnotherRoom")
        second["entrance"]["x"] += 1
        second["seats"][0]["direction"] = "left"
        self.world["locations"].append(second)
        self.assertTrue(any("global" in error for error in self.errors()))
        second["seats"][0]["direction"] = "right"
        self.assertFalse(self.errors())

    def test_flipped_and_padded_chair_tiles_and_ambiguous_filenames_are_rejected(self):
        original = self.source.read_bytes()
        xml = ET.parse(self.source)
        data = xml.getroot().find("layer[@name='Buildings']/data")
        cells = data.text.split(",")
        cells[18] = str(4 | 0x80000000)
        data.text = ",".join(cells)
        xml.write(self.source)
        self.assertTrue(any("flipped" in error for error in self.errors()))
        self.source.write_bytes(original)
        tsx = self.source.parent / "room.tsx"
        xml = ET.parse(tsx)
        xml.getroot().set("spacing", "1")
        xml.write(tsx)
        self.assertTrue(any("margins or spacing" in error for error in self.errors()))
        furnished_map(self.source.parent, filename="Not.Safe.png")
        self.assertTrue(any("no dots" in error for error in self.errors()))

    def test_same_seat_basename_cannot_hide_different_artwork_in_other_folders(self):
        other = furnished_map(self.root / "other")
        Image.new("RGBA", (32, 32), (200, 100, 80, 255)).save(other.parent / "UniqueResidenceTiles.png")
        second = copy.deepcopy(self.location)
        second.update(id="another-place", internal_name="AnotherRoom", map="other/room.tmx")
        second["entrance"]["x"] += 1
        self.world["locations"].append(second)
        self.assertTrue(any("filenames are global" in error for error in self.errors()))

    def test_entrance_patch_is_copied_exported_and_preserved_through_save_as(self):
        patch = furnished_map(self.root / "entry", filename="UniqueEntranceTiles.png")
        self.location.update(entrance_patch="entry/room.tmx", entrance_patch_x=28, entrance_patch_y=60)
        source_file = self.root / "character.json"
        save_project(self.project, source_file)
        copied_file = self.root / "copy" / "character.json"
        copy_project(self.project, source_file, copied_file)
        copied = load_project(copied_file)
        self.assertEqual(copied["world"]["locations"][0]["interactions"], self.location["interactions"])
        self.assertEqual(map_bundle(copied_file.parent / "entry/room.tmx")["files"], map_bundle(patch)["files"])
        compiled = compile_world(copied["world"], self.character, copied_file.parent)
        overlay = next(item for item in compiled["patches"] if item.get("PatchMode") == "Overlay")
        self.assertEqual(overlay["Target"], "Maps/Town")
        self.assertEqual(overlay["ToArea"], {"X": 28, "Y": 60, "Width": 8, "Height": 8})
        self.assertIn(overlay["FromFile"], compiled["files"])
        self.assertEqual(compiled["world"]["locations"][0]["entrance_patch"], overlay["FromFile"])
        overlay_index = compiled["patches"].index(overlay)
        self.assertGreater(next(index for index, item in enumerate(compiled["patches"]) if "AddWarps" in item), overlay_index)

    def test_entrance_patch_cannot_extend_known_custom_destination(self):
        exterior = new_location()
        exterior.update(internal_name="Courtyard", map="source/room.tmx")
        self.world["locations"].append(exterior)
        self.location["entrance"].update(map="Courtyard", x=3, y=3, arrival_x=3, arrival_y=4)
        self.location.update(entrance_patch="source/room.tmx", entrance_patch_x=1)
        self.assertTrue(any("patch extends outside" in error for error in self.errors()))
        self.location["entrance_patch_x"] = 0
        self.assertFalse(self.errors())

    def test_clickable_entrance_has_a_visible_passable_action_and_separate_npc_warp(self):
        self.location.update(entrance_patch="source/room.tmx", entrance_patch_x=30, entrance_patch_y=60, entrance_mode="interact")
        self.location["entrance"].update(arrival_x=32, arrival_y=62)
        result = compile_world(self.world, self.character, self.root)
        entrance = next(patch for patch in result["patches"] if "AddNpcWarps" in patch)
        identity = exported_location_id(self.location, self.character)
        self.assertEqual(entrance["AddNpcWarps"], [f"32 62 {identity} 2 2"])
        self.assertEqual(entrance["MapTiles"][0], {"Layer": "Buildings", "Position": {"X": 32, "Y": 62},
                                                  "SetProperties": {"Passable": "T", "Action": f"Warp 2 2 {identity}"}})
        self.assertFalse(any("AddWarps" in patch and patch["Target"] == "Maps/Town" for patch in result["patches"]))
        self.assertTrue(any("AddWarps" in patch and patch["Target"] == "Maps/" + identity for patch in result["patches"]))

    def test_clickable_entrance_requires_an_unshifted_patch_tile_at_its_position(self):
        self.location["entrance_mode"] = "interact"
        self.assertTrue(any("needs an entrance patch" in error for error in self.errors()))
        self.location.update(entrance_patch="source/room.tmx", entrance_patch_x=0, entrance_patch_y=0)
        self.assertTrue(any("inside its entrance patch" in error for error in self.errors()))
        self.location.update(entrance_patch_x=32, entrance_patch_y=62)
        self.assertTrue(any("existing Buildings tile" in error for error in self.errors()))
        self.location.update(entrance_patch_x=30, entrance_patch_y=60)
        self.assertFalse(self.errors())
        xml = ET.parse(self.source)
        xml.getroot().find("layer[@name='Buildings']").set("offsetx", "16")
        xml.write(self.source)
        self.assertTrue(any("offset Buildings layer" in error for error in self.errors()))

    def test_normal_archive_can_be_reopened_and_reexported_without_losing_features(self):
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192), (100, 90, 80, 255)).save(portrait)
        Image.new("RGBA", (64, 128), (100, 90, 80, 255)).save(sprite)
        self.project["artwork"] = {"portrait": "portrait.png", "sprite": "sprite.png"}
        self.location.update(entrance_patch="source/room.tmx", entrance_patch_x=28, entrance_patch_y=60)
        blob = build_mod_archive(self.character, portrait, sprite, project_document=self.project, project_root=self.root)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            archive.extractall(self.root / "unpacked")
        folder = self.root / "unpacked" / "[CP] Mira"
        reopened = load_project(folder / "project.json")
        self.assertEqual(reopened["world"]["locations"][0]["seats"], self.location["seats"])
        rebuilt = build_mod_archive(reopened["character"], folder / "assets/portraits.png", folder / "assets/sprites.png",
                                    project_document=reopened, project_root=folder)
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            self.assertTrue(any(patch["Target"] == "Data/ChairTiles" for patch in content["Changes"]))
            guide = archive.read("[CP] Mira/WORLD_TESTING.txt").decode()
            self.assertIn("Inspect Mirror at 4, 2", guide)
            self.assertIn("Sit at 2, 2 facing right", guide)
            self.assertIn("visible entrance patch", guide)
