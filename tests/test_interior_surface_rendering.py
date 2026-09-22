"""Applied finishes must agree in the room preview, exported map, and history."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_surface_design import apply_surface
from pixelheart_core.interiors import (
    InteriorDraft, InteriorError, compile_interior, import_atlas, interior_tmx,
    map_layers, new_interior, normalize_interior, render_interior,
)


class InteriorSurfaceRenderingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.colors = [(index * 15, 255 - index * 13, index * 11, 255) for index in range(16)]
        with Image.new("RGBA", (256, 16)) as image:
            for index, color in enumerate(self.colors):
                image.paste(color, (index * 16, 0, index * 16 + 16, 16))
            image.save(self.root / "tiles.png")

    def design(self, kind="residence", study=True):
        data = new_interior(kind)
        data["atlas"] = import_atlas(self.root / "tiles.png", self.root)
        data["surfaces"] = [
            {"id": "(FL)Main", "name": "Main flooring", "kind": "floor", "width": 2,
             "height": 2, "tiles": [0, 1, 2, 3], "dependency": "Example.MainFloor"},
            {"id": "(FL)Study", "name": "Study flooring", "kind": "floor", "width": 2,
             "height": 2, "tiles": [4, 5, 6, 7], "dependency": "Example.StudyFloor"},
            {"id": "(WP)Main", "name": "Main wallpaper", "kind": "wall", "width": 1,
             "height": 3, "tiles": [8, 9, 10], "dependency": "Example.MainWall"},
            {"id": "(WP)Study", "name": "Study wallpaper", "kind": "wall", "width": 1,
             "height": 3, "tiles": [11, 12, 13], "dependency": "Example.StudyWall"},
            {"id": "(WP)Unused", "name": "Unused wallpaper", "kind": "wall", "width": 1,
             "height": 3, "tiles": [14, 14, 14], "dependency": "Example.Unused"},
        ]
        data = apply_surface(apply_surface(data, "(FL)Main"), "(WP)Main")
        if kind == "residence" and study:
            data["rooms"].append({"id": "study", "name": "Study", "x": 12, "y": 6,
                                  "width": 4, "height": 4, "optional": True, "enabled": True})
            data = apply_surface(apply_surface(data, "(FL)Study", "study"), "(WP)Study", "study")
        return normalize_interior(data)

    @staticmethod
    def gids(root, name):
        return [int(value.strip()) for value in root.find(f"layer[@name='{name}']/data").text.split(",")]

    def test_floor_pattern_repeats_from_each_rooms_own_origin(self):
        data = self.design()
        back, width = map_layers(data)["Back"], data["width"]
        expected = {(2, 5): 0, (3, 5): 1, (2, 6): 2, (3, 6): 3,
                    (4, 5): 0, (10, 6): 2,
                    (12, 6): 4, (13, 6): 5, (12, 7): 6, (13, 7): 7,
                    (14, 8): 4, (15, 9): 7}
        for (x, y), tile in expected.items():
            with self.subTest(x=x, y=y):
                self.assertEqual(back[y * width + x], tile + 1)

    def test_each_wall_uses_all_three_rows_and_its_rooms_override(self):
        data = self.design()
        back, width = map_layers(data)["Back"], data["width"]
        for x, y, expected in ((3, 2, 8), (3, 3, 9), (3, 4, 10),
                               (13, 3, 11), (13, 4, 12), (13, 5, 13)):
            with self.subTest(x=x, y=y):
                self.assertEqual(back[y * width + x], expected + 1)

    def test_preview_pixels_and_exported_map_agree_for_every_rendered_tile(self):
        data = self.design()
        layers = map_layers(data)
        exported = ET.fromstring(interior_tmx(data))
        for layer, values in layers.items():
            self.assertEqual(self.gids(exported, layer), values)
        with render_interior(data, self.root) as preview:
            for index, floor_gid in enumerate(layers["Back"]):
                if not floor_gid:
                    continue
                building_gid = layers["Buildings"][index]
                gid = building_gid if 0 < building_gid <= data["atlas"]["tile_count"] else floor_gid
                x, y = index % data["width"], index // data["width"]
                self.assertEqual(preview.getpixel((x * 16 + 8, y * 16 + 8)), self.colors[gid - 1], (x, y))

    def test_spouse_marker_keeps_the_exact_floor_pattern_tile_and_animation(self):
        data = self.design("spouse")
        # Relative position (3, 2) in the floor's 2 × 2 repeat selects index 1,
        # unlike the legacy floor fallback, which remains index 0.
        self.assertEqual(data["spouse_stand"], [3, 5])
        data["animations"] = [{"tile_id": 1, "frames": [
            {"tile_id": 2, "duration_ms": 100}, {"tile_id": 3, "duration_ms": 100}]}]
        exported = ET.fromstring(interior_tmx(data, design_id="Example.SpouseRoom"))
        marker = exported.find("tileset[@name='pixelheart_spouse_marker']")
        tile = marker.find("tile[@id='1']")
        self.assertIsNotNone(tile)
        self.assertEqual(tile.find("properties/property").attrib["value"], "Example.SpouseRoom")
        self.assertEqual([frame.attrib["tileid"] for frame in tile.findall("animation/frame")], ["2", "3"])
        back = self.gids(exported, "Back")
        marker_gid = int(marker.attrib["firstgid"]) + 1
        self.assertEqual(back[5 * 6 + 3], marker_gid)
        self.assertEqual(back.count(marker_gid), 1)

    def test_removing_a_decorated_room_and_undo_restores_its_finishes(self):
        draft = InteriorDraft(self.design())
        before = draft.snapshot()
        draft.remove_room("study")
        self.assertNotIn("study", draft.data["room_styles"])
        self.assertEqual([room["id"] for room in draft.data["rooms"]], ["main"])
        self.assertTrue(draft.undo())
        self.assertEqual(draft.snapshot(), before)
        self.assertTrue(draft.redo())
        self.assertNotIn("study", draft.data["room_styles"])

    def test_export_requires_only_used_finish_providers(self):
        data = self.design()
        compiled = compile_interior(data, "Example.Home", "Example.NPC", self.root, "assets/")
        self.assertEqual(compiled["dependencies"], ["Example.MainFloor", "Example.MainWall",
                                                   "Example.StudyFloor", "Example.StudyWall"])
        data = apply_surface(apply_surface(data, "(FL)Main"), "(WP)Main")
        compiled = compile_interior(data, "Example.Home", "Example.NPC", self.root, "assets/")
        self.assertEqual(compiled["dependencies"], ["Example.MainFloor", "Example.MainWall"])

    def test_malformed_patterns_and_unknown_room_ids_fail_without_mutation(self):
        good = self.design()
        cases = [
            {"room_styles": {"missing": {}}}, {"room_styles": []},
            {"room_styles": {"main": None}},
            {"style": {**good["style"], "floor_pattern": {"width": 2, "height": 2, "tiles": [0]}}},
            {"style": {**good["style"], "wall_pattern": {"width": 1, "height": 3, "tiles": [8, True, 10]}}},
            {"style": {**good["style"], "floor_pattern": {"width": 2, "height": 2, "tiles": [0, 1, 2, 16]}}},
            {"surfaces": [*good["surfaces"], deepcopy(good["surfaces"][0])]},
        ]
        for changes in cases:
            data = {**deepcopy(good), **changes}
            before = deepcopy(data)
            with self.subTest(changes=changes), self.assertRaises(InteriorError):
                normalize_interior(data)
            self.assertEqual(data, before)

    def test_full_length_qualified_pattern_ids_survive_application_and_export(self):
        data = self.design(study=False)
        identity = "(WP)" + "x" * 256
        data["surfaces"][-1]["id"] = identity
        applied = apply_surface(data, identity)
        self.assertEqual(applied["style"]["wall_pattern"]["surface_id"], identity)
        compiled = compile_interior(applied, "Example.Home", "Example.NPC", self.root, "assets/")
        self.assertIn("Example.Unused", compiled["dependencies"])


if __name__ == "__main__":
    unittest.main()
