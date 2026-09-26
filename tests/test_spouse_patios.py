"""A spouse patio remains a portable farm insert, never a routable location."""
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
from pixelheart_core.patios import validate_patio_assets
from pixelheart_core.projects import ProjectError, copy_project, load_project, new_project, save_project
from pixelheart_core.story import exported_npc_id
from pixelheart_core.world import WorldError, compile_world, new_world, normalize_world, world_asset_references, world_issues


class SpousePatioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project_root = self.root / "source"
        self.project_root.mkdir()
        self.document = new_project()
        self.character = self.document["character"]
        self.character.update(id="patio-export-test", name="Mira", internal_name="Mira", romanceable=True)
        for kind, size in (("portrait", (128, 192)), ("sprite", (64, 416))):
            path = self.project_root / f"{kind}.png"
            Image.new("RGBA", size, (64, 96, 128, 255)).save(path)
            setattr(self, kind, path)
            self.document["artwork"][kind] = path.name
        self.assets = self.project_root / "patio"
        self.assets.mkdir()
        Image.new("RGBA", (64, 16), (40, 80, 90, 255)).save(self.assets / "tiles.png")
        Image.new("RGBA", (128, 16), (0, 0, 0, 0)).save(self.assets / "paths.png")
        Image.new("RGBA", (64, 32), (120, 90, 60, 255)).save(self.assets / "pose.png")
        self.patio = {"map": "patio/map.tmx", "source_rect": [0, 0, 4, 4],
                      "animation_frames": [[8, 1500]], "animation_pixel_offset": [0, 0],
                      "pose": {"texture": "patio/pose.png", "frame_width": 32, "frame_height": 32,
                               "frames": [[0, 1500], [1, 100]], "draw_offset_pixels": [-8, -12]}}
        self.document["world"] = {**new_world(), "spouse_patio": self.patio}
        self.write_map()

    def write_map(self, width=4, height=4, marker=(1, 2), external=False):
        xml = ET.Element("map", orientation="orthogonal", width=str(width), height=str(height), tilewidth="16", tileheight="16")
        sheet = ET.Element("tileset", name="art", tilewidth="16", tileheight="16", tilecount="4", columns="4")
        ET.SubElement(sheet, "image", source="tiles.png", width="64", height="16")
        for index, key in ((1, "Passable"), (2, "Water"), (3, "NPCBarrier")):
            tile = ET.SubElement(sheet, "tile", id=str(index))
            ET.SubElement(ET.SubElement(tile, "properties"), "property", name=key, value="T")
        if external:
            ET.ElementTree(sheet).write(self.assets / "tiles.tsx")
            ET.SubElement(xml, "tileset", firstgid="1", source="tiles.tsx")
        else:
            sheet.set("firstgid", "1")
            xml.append(sheet)
        paths = ET.SubElement(xml, "tileset", firstgid="5", name="paths", tilewidth="16", tileheight="16", tilecount="8", columns="8")
        ET.SubElement(paths, "image", source="paths.png", width="128", height="16")
        for name in ("Back", "Buildings", "Front", "Paths"):
            layer = ET.SubElement(xml, "layer", name=name, width=str(width), height=str(height))
            gids = [0] * (width * height)
            if name == "Buildings":
                gids[:width] = [1] * width
            if name == "Paths" and marker is not None:
                gids[marker[1] * width + marker[0]] = 12  # first GID 5 + local tile index 7
            ET.SubElement(layer, "data", encoding="csv").text = ",".join(map(str, gids))
        ET.ElementTree(xml).write(self.assets / "map.tmx")

    def edit_cell(self, layer, x, y, gid):
        path = self.assets / "map.tmx"
        xml = ET.parse(path)
        data = xml.find(f"layer[@name='{layer}']/data")
        gids = data.text.split(",")
        gids[y * int(xml.getroot().get("width")) + x] = str(gid)
        data.text = ",".join(gids)
        xml.write(path)

    def compile(self):
        return compile_world(self.document["world"], self.character, self.project_root)

    def test_native_patio_load_and_actor_contract_without_location_or_warp(self):
        compiled = self.compile()
        field = compiled["npc_fields"]["SpousePatio"]
        self.assertEqual(field["MapSourceRect"], {"X": 0, "Y": 0, "Width": 4, "Height": 4})
        self.assertEqual(field["SpriteAnimationFrames"], [[8, 1500]])
        self.assertEqual(field["SpriteAnimationPixelOffset"], {"X": 0, "Y": 0})
        loads = {p["Target"]: p for p in compiled["patches"] if p["Action"] == "Load"}
        self.assertIn("Maps/" + field["MapAsset"], loads)
        self.assertFalse(any(p["Target"] == "Data/Locations" or "AddWarps" in p for p in compiled["patches"]))
        npc = exported_npc_id(self.character)
        runtime = next(p["Entries"][npc] for p in compiled["patches"] if p["Target"] == "Pixelheart.Interiors/PatioPoses")
        self.assertEqual(runtime["Npc"], npc)
        self.assertEqual(runtime["Frames"], [{"Frame": 0, "Duration": 1500}, {"Frame": 1, "Duration": 100}])
        self.assertEqual(runtime["DrawOffsetPixels"], [-8, -12])
        self.assertEqual(compiled["files"][loads[runtime["Texture"]]["FromFile"]], (self.assets / "pose.png").read_bytes())
        self.assertNotIn("When", loads[runtime["Texture"]])
        self.assertIn({"UniqueID": "Pixelheart.Interiors", "IsRequired": True, "MinimumVersion": "0.2.1"}, compiled["dependencies"])

    def test_patios_without_pose_need_no_companion_or_invented_animation(self):
        self.patio.pop("pose")
        self.patio.pop("animation_frames")
        self.patio.pop("animation_pixel_offset")
        compiled = self.compile()
        self.assertEqual(compiled["dependencies"], [])
        self.assertEqual(len(compiled["patches"]), 1)
        self.assertNotIn("SpriteAnimationFrames", compiled["npc_fields"]["SpousePatio"])
        self.assertNotIn("SpriteAnimationPixelOffset", compiled["npc_fields"]["SpousePatio"])

    def test_dependency_is_required_and_minimum_version_not_downgraded(self):
        for version, expected in (("0.1.0", "0.2.1"), ("0.2.0", "0.2.1"), ("0.2.1-beta", "0.2.1"),
                                  ("0.2.1", "0.2.1"), ("0.3.0", "0.3.0")):
            with self.subTest(version=version):
                self.document["world"]["dependencies"] = [{"id": "pixelheart.interiors", "required": False, "minimum_version": version}]
                entry, = self.compile()["dependencies"]
                self.assertTrue(entry["IsRequired"])
                self.assertEqual(entry["MinimumVersion"], expected)

    def test_project_copy_and_extracted_archive_are_independently_portable(self):
        self.write_map(external=True)
        Image.new("RGBA", (64, 32), (30, 20, 40, 255)).save(self.assets / "winter.png")
        self.patio["pose"]["seasonal_textures"] = {"winter": "patio/winter.png"}
        original = deepcopy(self.document)
        file = self.project_root / "character.json"
        save_project(self.document, file)
        self.assertEqual(load_project(file)["world"], original["world"])
        destination = self.root / "copy" / "character.json"
        copy_project(self.document, file, destination)
        for name in ("map.tmx", "tiles.tsx", "tiles.png", "paths.png", "pose.png", "winter.png"):
            self.assertEqual((destination.parent / "patio" / name).read_bytes(), (self.assets / name).read_bytes())
        self.assertEqual(set(world_asset_references(self.document["world"])), {"patio/map.tmx", "patio/pose.png", "patio/winter.png"})
        compiled = self.compile()
        archive = build_mod_archive(self.character, self.portrait, self.sprite,
                                    project_document=self.document, project_root=self.project_root)
        unpacked = self.root / "unpacked"
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            opened.extractall(unpacked)
            content = json.loads(opened.read("[CP] Mira/content.json"))
            data = next(p["Entries"][exported_npc_id(self.character)] for p in content["Changes"] if p["Target"] == "Data/Characters")
            self.assertEqual(data["SpousePatio"], compiled["npc_fields"]["SpousePatio"])
        root = unpacked / "[CP] Mira"
        backup = load_project(root / "project.json")
        self.project_root.rename(self.root / "source-unavailable")
        regenerated = compile_world(backup["world"], backup["character"], root)
        self.assertEqual(regenerated, compiled)
        self.assertEqual(self.document, original)

    def test_seasonal_pose_loads_cover_every_season_exactly_once(self):
        seasons = ("spring", "summer", "fall", "winter")
        for chosen in ((), ("winter",), ("spring", "fall"), seasons):
            with self.subTest(chosen=chosen):
                self.patio["pose"]["seasonal_textures"] = {season: "patio/pose.png" for season in chosen}
                compiled = self.compile()
                loads = [patch for patch in compiled["patches"] if patch["Action"] == "Load"
                         and patch["Target"].endswith("/SpousePatioPose")]
                for season in seasons:
                    matches = [patch for patch in loads if season in patch.get("When", {}).get("Season", ", ".join(seasons)).split(", ")]
                    self.assertEqual(len(matches), 1)
                    self.assertTrue(matches[0]["FromFile"].endswith(f"pose-{season}.png" if season in chosen else "pose.png"))

    def test_seasonal_pose_rejects_unknown_seasons_unsafe_references_and_mismatched_grid(self):
        for mapping in ({"autumn": "patio/pose.png"}, {"Winter": "patio/pose.png"},
                        {"winter": "../pose.png"}, {"winter": "patio/map.tmx"}, ["winter"]):
            with self.subTest(mapping=mapping):
                self.patio["pose"]["seasonal_textures"] = mapping
                with self.assertRaises(WorldError):
                    self.compile()
        self.patio["pose"]["seasonal_textures"] = {"winter": "patio/winter.png"}
        Image.new("RGBA", (32, 32), (30, 20, 40, 255)).save(self.assets / "winter.png")
        with self.assertRaisesRegex(WorldError, "same dimensions"):
            self.compile()
        Image.new("RGBA", (64, 32), (30, 20, 40, 255)).save(self.assets / "winter.png")
        self.patio["pose"]["frames"] = [[2, 100]]
        with self.assertRaisesRegex(WorldError, "outside"):
            self.compile()

    def test_source_rectangle_marker_is_local_to_crop_and_external_tileset_properties_work(self):
        self.write_map(width=8, marker=(5, 2), external=True)
        self.patio["source_rect"] = [4, 0, 4, 4]
        self.edit_cell("Paths", 1, 2, 12)  # A different atlas section is allowed its own marker.
        self.edit_cell("Buildings", 5, 2, 2)  # Art tile index1 declares Passable.
        self.assertEqual(validate_patio_assets(self.patio, self.project_root)["standing_tile"], [1, 2])

    def test_structural_invalid_values_fail_save_and_export(self):
        cases = [lambda p: p.update(map="../escape.tmx"), lambda p: p.update(map="/tmp/map.tmx"),
                 lambda p: p.update(source_rect=[0, 0, 5, 4]), lambda p: p.update(source_rect=[0, 0, True, 4]),
                 lambda p: p.update(animation_frames=[]), lambda p: p.update(animation_frames=[[8, 0]]),
                 lambda p: p.update(animation_pixel_offset=[0, "0"]),
                 lambda p: p["pose"].update(frame_width=17), lambda p: p["pose"].update(texture="../pose.png"),
                 lambda p: p["pose"].update(frames=[[True, 100]]), lambda p: p["pose"].update(frames=[]),
                 lambda p: p["pose"].update(draw_offset_pixels=[0, 129])]
        for change in cases:
            invalid = deepcopy(self.document)
            change(invalid["world"]["spouse_patio"])
            with self.subTest(change=change):
                with self.assertRaises(WorldError):
                    normalize_world(invalid["world"])
                with self.assertRaises(ProjectError):
                    save_project(invalid, self.project_root / "invalid.json")
                with self.assertRaises(ExportValidationError):
                    build_mod_archive(self.character, self.portrait, self.sprite, world=invalid["world"], project_root=self.project_root)

    def test_missing_duplicate_blocked_water_and_unreachable_markers_are_rejected(self):
        cases = [lambda: self.edit_cell("Paths", 1, 2, 0), lambda: self.edit_cell("Paths", 2, 2, 12),
                 lambda: self.edit_cell("Buildings", 1, 2, 1), lambda: self.edit_cell("Back", 1, 2, 3),
                 lambda: self.edit_cell("Back", 1, 2, 4),
                 lambda: [self.edit_cell("Buildings", x, 3, 1) for x in range(4)]]
        for change in cases:
            self.write_map()
            change()
            with self.subTest(change=change), self.assertRaises(WorldError):
                self.compile()

    def test_missing_or_unsafe_assets_invalid_grid_and_invalid_frames_are_rejected(self):
        self.patio["pose"]["frames"] = [[2, 100]]
        with self.assertRaisesRegex(WorldError, "outside"):
            self.compile()
        self.patio["pose"]["frames"] = [[0, 100]]
        Image.new("RGBA", (33, 32)).save(self.assets / "pose.png")
        with self.assertRaisesRegex(WorldError, "multiples"):
            self.compile()
        for dimensions in ((8192, 32), (32, 8192)):
            Image.new("RGBA", dimensions).save(self.assets / "pose.png")
            with self.subTest(dimensions=dimensions), self.assertRaisesRegex(WorldError, "4096 pixels"):
                self.compile()
        Image.new("RGBA", (32, 32)).save(self.assets / "pose.png")
        self.patio["source_rect"] = [1, 0, 4, 4]
        with self.assertRaisesRegex(WorldError, "outside"):
            self.compile()
        self.patio["source_rect"] = [0, 0, 4, 4]
        outside = self.root / "outside.png"
        (self.assets / "pose.png").rename(outside)
        (self.assets / "pose.png").symlink_to(outside)
        with self.assertRaisesRegex(WorldError, "symlink"):
            self.compile()
        (self.assets / "pose.png").unlink()
        with self.assertRaises(WorldError):
            self.compile()

    def test_invalid_map_grid_missing_paths_bad_gid_and_duplicate_layers_are_rejected(self):
        edits = [lambda xml: xml.find("layer").set("offsetx", "1"),
                 lambda xml: xml.remove(xml.find("layer[@name='Paths']")),
                 lambda xml: xml.find("layer").set("width", "3"),
                 lambda xml: xml.append(deepcopy(xml.find("layer"))),
                 lambda xml: xml.find("tileset").set("tilecount", "99")]
        for edit in edits:
            self.write_map()
            tree = ET.parse(self.assets / "map.tmx")
            edit(tree.getroot())
            tree.write(self.assets / "map.tmx")
            with self.subTest(edit=edit), self.assertRaises(WorldError):
                self.compile()
        self.write_map()
        self.edit_cell("Front", 0, 0, 999)
        with self.assertRaisesRegex(WorldError, "outside"):
            self.compile()

    def test_optional_patio_preserves_existing_worlds_and_requires_adult_romance(self):
        self.assertEqual(normalize_world(new_world()), new_world())
        self.document["world"]["spouse_patio"] = None
        self.assertNotIn("SpousePatio", self.compile()["npc_fields"])
        self.document["world"]["spouse_patio"] = self.patio
        self.character["romanceable"] = False
        self.assertTrue(any(issue["level"] == "error" and "adult romance" in issue["message"]
                            for issue in world_issues(self.document["world"], self.character, self.project_root)))


if __name__ == "__main__":
    unittest.main()
