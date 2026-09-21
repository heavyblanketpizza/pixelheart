"""Exercise authored interiors at project, Content Patcher, and archive boundaries."""

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from pixelheart_core.exporting import ExportValidationError, build_mod_archive
from pixelheart_core.interior_furniture import attach_texture, validate_definition
from pixelheart_core.interiors import InteriorDraft, import_atlas, interior_asset_references, new_interior
from pixelheart_core.projects import ProjectError, copy_project, load_project, new_project, save_project
from pixelheart_core.world import (
    WorldError, compile_world, exported_location_id, exported_npc_id, new_location,
    new_world, normalize_world, world_issues,
)


class InteriorExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "original"
        self.project_root.mkdir()
        self.project_file = self.project_root / "character.json"
        self.document = new_project()
        self.character = self.document["character"]
        self.character.update(id="interior-export-test", name="Mira", internal_name="Mira",
                              home_map="MiraHome", home_x=6, home_y=10)
        self.character["schedule"] = [
            {"id": "home", "time": "600", "location": "MiraHome", "x": 7, "y": 10, "facing": "down"},
        ]
        for kind, size in (("portrait", (128, 192)), ("sprite", (64, 416))):
            path = self.project_root / f"{kind}.png"
            Image.new("RGBA", size, (64, 96, 128, 255)).save(path)
            setattr(self, kind, path)
            self.document["artwork"][kind] = path.name
        atlas = self.root / "original-tiles.png"
        with Image.new("RGBA", (64, 16), (25, 35, 45, 255)) as image:
            for index, color in enumerate(("slategray", "darkblue", "gray", "black")):
                image.paste(color, (index * 16, 0, index * 16 + 16, 16))
            image.save(atlas)
        self.atlas = import_atlas(atlas, self.project_root)
        preview = self.root / "original-preview.png"
        Image.new("RGBA", (32, 16), (170, 30, 70, 255)).save(preview)
        self.modded = attach_texture(validate_definition({
            "id": "(F)Example.Furniture_Chair", "name": "Mod chair", "kind": "chair",
            "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 2,
            "dependency": "Example.Furniture", "texture": "Mods/Example/Furniture",
            "mod_data": {"Example/Variant": "violet"},
            "frames": [{"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100},
                       {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 100}],
        }), preview, self.project_root)
        self.residence = self.location("residence", "MiraHome")
        self.spouse = self.location("spouse", "MiraSpouseRoom")
        self.document["world"] = {**new_world(), "locations": [self.residence, self.spouse]}

    def location(self, kind, name):
        data = new_interior(kind)
        data["atlas"] = deepcopy(self.atlas)
        data["style"].update(floor=0, wall_top=1, wall_middle=2, wall_bottom=3)
        data["catalog"] = [
            validate_definition({"id": "(F)0", "name": "Oak Chair", "kind": "chair",
                                 "footprint": [1, 1], "sprite_size": [1, 2], "rotations": 4}),
            deepcopy(self.modded),
            validate_definition({"id": "(F)Unused.Lamp", "name": "Unused lamp", "kind": "lamp",
                                 "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1,
                                 "dependency": "Unused.Furniture"}),
        ]
        draft = InteriorDraft(data)
        if kind == "residence":
            draft.add_room("Study", 12, 5, 4, 4)
            draft.place_furniture("(F)0", 5, 6)
            draft.place_furniture(self.modded["id"], 7, 6)
        else:
            draft.place_furniture(self.modded["id"], 1, 5)
        location = new_location()
        location.update(id=name + "-stable-id", name=name, internal_name=name,
                        spouse_room=kind == "spouse", interior=draft.snapshot(), map=None,
                        entry_x=data["entry"][0], entry_y=data["entry"][1], exit_x=4, exit_y=11)
        return location

    def compile(self, world=None, root=None):
        return compile_world(world or self.document["world"], self.character, root or self.project_root)

    def archive(self, document=None, root=None):
        document = document or self.document
        root = root or self.project_root
        return zipfile.ZipFile(io.BytesIO(build_mod_archive(
            document["character"], self.portrait, self.sprite,
            project_document=document, project_root=root,
        )))

    @staticmethod
    def runtime(compiled):
        return next(patch["Entries"] for patch in compiled["patches"]
                    if patch["Target"] == "Pixelheart.Interiors/Designs")

    def test_project_normalization_save_and_copy_preserve_interior_assets_and_metadata(self):
        self.residence["interior"]["author_extension"] = {"notes": ["Keep this design note."]}
        original = deepcopy(self.document)
        normalized = normalize_world(self.document["world"])
        normalized["locations"][0]["interior"]["rooms"][0]["name"] = "Detached"
        self.assertEqual(self.document, original)
        save_project(self.document, self.project_file)
        reopened = load_project(self.project_file)
        self.assertEqual(reopened["world"], self.document["world"])
        references = set(reference for location in self.document["world"]["locations"]
                         for reference in interior_asset_references(location["interior"]))
        destination = self.root / "copy" / "character.json"
        copy_project(self.document, self.project_file, destination)
        copied = load_project(destination)
        self.assertEqual(copied["world"], self.document["world"])
        for reference in references:
            self.assertEqual((destination.parent / reference).read_bytes(), (self.project_root / reference).read_bytes())
        self.assertEqual(self.document, original)
        self.assertFalse(list(self.project_root.rglob("*.tmx")))

    def test_world_validation_accepts_complete_atlas_designs_without_imported_maps(self):
        issues = world_issues(self.document["world"], self.character, self.project_root)
        self.assertEqual([issue for issue in issues if issue["level"] == "error"], [])
        self.assertTrue(all(location["map"] is None for location in self.document["world"]["locations"]))
        self.assertTrue(any("companion" in issue["message"] for issue in issues))

    def test_compilation_keeps_real_furniture_out_of_structural_maps(self):
        compiled = self.compile()
        runtime = self.runtime(compiled)
        identity = exported_location_id(self.residence, self.character)
        items = runtime[identity]["furniture"]
        self.assertEqual({item["item_id"] for item in items}, {"(F)0", self.modded["id"]})
        self.assertEqual(next(item for item in items if item["item_id"] == self.modded["id"])["mod_data"],
                         {"Example/Variant": "violet"})
        empty = deepcopy(self.document["world"])
        for location in empty["locations"]:
            location["interior"]["furniture"] = []
        empty_compiled = self.compile(empty)
        maps = {name: payload for name, payload in compiled["files"].items() if name.endswith(".tmx")}
        self.assertTrue(maps)
        self.assertEqual(maps, {name: payload for name, payload in empty_compiled["files"].items() if name.endswith(".tmx")})
        self.assertFalse(any(patch["Target"] == "Data/Furniture" for patch in compiled["patches"]))

    def test_residence_is_decoratable_and_each_room_variant_keeps_its_return_warp(self):
        compiled = self.compile()
        identity = exported_location_id(self.residence, self.character)
        runtime = self.runtime(compiled)[identity]
        entry = next(patch["Entries"][identity] for patch in compiled["patches"]
                     if patch["Target"] == "Data/Locations" and identity in patch["Entries"])
        self.assertEqual(entry["CreateOnLoad"]["Type"], "StardewValley.Locations.DecoratableLocation")
        self.assertEqual(entry["DefaultArrivalTile"], {"X": 4, "Y": 10})
        self.assertEqual(len(runtime["variants"]), 2)
        expected_targets = {"Maps/" + identity, *(variant["map_asset"] for variant in runtime["variants"])}
        expected_warp = "4 11 Town 32 63"
        targets = {target.strip() for patch in compiled["patches"]
                   if patch["Action"] == "EditMap" and expected_warp in patch.get("AddWarps", [])
                   for target in patch["Target"].split(",")}
        self.assertEqual(targets, expected_targets)
        self.assertIn([6, 10], runtime["protected_tiles"])
        self.assertIn([7, 10], runtime["protected_tiles"])
        self.assertIn([4, 11], runtime["protected_tiles"])
        for filename, payload in compiled["files"].items():
            if not filename.endswith(".tmx"):
                continue
            xml = ET.fromstring(payload)
            props = {prop.attrib["name"]: prop.attrib["value"] for prop in xml.findall("properties/property")}
            self.assertEqual(props["AllowBeds"], "true")
            self.assertEqual(props["AllowMiniFridges"], "true")

    def test_spouse_room_exports_six_by_nine_section_and_unique_position_marker(self):
        compiled = self.compile()
        identity = exported_location_id(self.spouse, self.character)
        spouse = compiled["npc_fields"]["SpouseRoom"]
        self.assertEqual(spouse, {"MapAsset": identity, "MapSourceRect": {"X": 0, "Y": 0, "Width": 6, "Height": 9}})
        self.assertFalse(any(identity in patch.get("Entries", {}) for patch in compiled["patches"]
                             if patch["Target"] == "Data/Locations"))
        runtime = self.runtime(compiled)[identity]
        self.assertEqual(runtime["spouse_npc"], exported_npc_id(self.character))
        self.assertTrue(all(0 <= tile[0] < 6 and 0 <= tile[1] < 9 for tile in runtime["protected_tiles"]))
        self.assertEqual([runtime["spouse_marker_x"], runtime["spouse_marker_y"]], self.spouse["interior"]["spouse_stand"])
        load = next(patch for patch in compiled["patches"] if patch["Target"] == "Maps/" + identity)
        xml = ET.fromstring(compiled["files"][load["FromFile"]])
        self.assertEqual((xml.attrib["width"], xml.attrib["height"]), ("6", "9"))
        markers = [(tileset, tile) for tileset in xml.findall("tileset") for tile in tileset.findall("tile")
                   if tile.find("properties/property[@name='Pixelheart.Interiors/SpouseRoom']") is not None]
        self.assertEqual(len(markers), 1)
        tileset, tile = markers[0]
        self.assertEqual(tile.find("properties/property[@name='Pixelheart.Interiors/SpouseRoom']").attrib["value"], identity)
        gid = int(tileset.attrib["firstgid"]) + int(tile.attrib["id"])
        cells = [int(value.strip()) for value in xml.find("layer[@name='Back']/data").text.split(",")]
        self.assertEqual(cells.count(gid), 1)
        x, y = self.spouse["interior"]["spouse_stand"]
        self.assertEqual(cells[y * 6 + x], gid)

    def test_required_runtime_and_placed_mod_dependencies_reach_archive_manifest(self):
        with self.archive() as archive:
            manifest = json.loads(archive.read("[CP] Mira/manifest.json"))
            dependencies = {entry["UniqueID"]: entry for entry in manifest["Dependencies"]}
            self.assertTrue(dependencies["Pixelheart.Interiors"]["IsRequired"])
            self.assertEqual(dependencies["Pixelheart.Interiors"]["MinimumVersion"], "0.1.0")
            self.assertTrue(dependencies["Example.Furniture"]["IsRequired"])
            self.assertNotIn("Unused.Furniture", dependencies)
            self.assertFalse(any(name.endswith(".dll") for name in archive.namelist()))

    def test_authored_optional_dependency_cannot_disable_required_runtime_or_furniture(self):
        self.document["world"]["dependencies"] = [
            {"id": "Pixelheart.Interiors", "required": False, "minimum_version": "0.0.1"},
            {"id": "Example.Furniture", "required": False, "minimum_version": "2.0.0"},
        ]
        dependencies = {entry["UniqueID"]: entry for entry in self.compile()["dependencies"]}
        self.assertTrue(dependencies["Pixelheart.Interiors"]["IsRequired"])
        self.assertEqual(dependencies["Pixelheart.Interiors"]["MinimumVersion"], "0.1.0")
        self.assertTrue(dependencies["Example.Furniture"]["IsRequired"])
        self.assertEqual(dependencies["Example.Furniture"]["MinimumVersion"], "2.0.0")

    def test_extracted_editable_backup_reexports_without_original_asset_folder(self):
        original = deepcopy(self.document)
        extracted = self.root / "extracted"
        with self.archive() as archive:
            archive.extractall(extracted)
        backup_root = extracted / "[CP] Mira"
        backup = load_project(backup_root / "project.json")
        self.assertEqual(backup["world"], self.document["world"])
        for location in backup["world"]["locations"]:
            for reference in interior_asset_references(location["interior"]):
                self.assertTrue(reference.startswith("world_assets/"))
                self.assertEqual((backup_root / reference).read_bytes(), (self.project_root / reference).read_bytes())
        original_compiled = self.compile()
        self.project_root.rename(self.root / "original-unavailable")
        regenerated = compile_world(backup["world"], backup["character"], backup_root)
        self.assertEqual(regenerated["files"], original_compiled["files"])
        archive_bytes = build_mod_archive(backup["character"], backup_root / "assets/portraits.png",
                                          backup_root / "assets/sprites.png", project_document=backup,
                                          project_root=backup_root)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            self.assertTrue(any(patch["Target"] == "Pixelheart.Interiors/Designs" for patch in content["Changes"]))
        self.assertEqual(self.document, original)

    def test_future_interior_version_and_bad_metadata_block_save_load_and_export(self):
        save_project(self.document, self.project_file)
        original_bytes = self.project_file.read_bytes()
        cases = [lambda design: design.update(version=2),
                 lambda design: design["furniture"][0].update(mod_data={"Example/Variant": {"not": "text"}})]
        for change in cases:
            invalid = deepcopy(self.document)
            change(invalid["world"]["locations"][0]["interior"])
            with self.subTest(change=change), self.assertRaises(ProjectError):
                save_project(invalid, self.project_file)
            self.assertEqual(self.project_file.read_bytes(), original_bytes)
            self.assertTrue(any(issue["level"] == "error" for issue in world_issues(invalid["world"], self.character, self.project_root)))
            with self.assertRaises(WorldError):
                compile_world(invalid["world"], self.character, self.project_root)
            with self.assertRaises(ExportValidationError):
                build_mod_archive(self.character, self.portrait, self.sprite, world=invalid["world"], project_root=self.project_root)
            raw = self.project_root / "unsupported.json"
            raw.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ProjectError):
                load_project(raw)

    def test_variable_tile_animation_remains_editable_but_cannot_export_invalid_game_timing(self):
        self.residence["interior"]["animations"] = [{"tile_id": 0, "frames": [
            {"tile_id": 0, "duration_ms": 100}, {"tile_id": 1, "duration_ms": 200},
        ]}]
        save_project(self.document, self.project_file)
        self.assertEqual(load_project(self.project_file)["world"], self.document["world"])
        self.assertTrue(any(issue["level"] == "error" for issue in world_issues(self.document["world"], self.character, self.project_root)))
        with self.assertRaises(WorldError):
            self.compile()


if __name__ == "__main__":
    unittest.main()
