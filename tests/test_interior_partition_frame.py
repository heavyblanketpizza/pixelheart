"""Derived partition trim stays portable and matches rendered/exported walls."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_furniture import ROOM_FRAME_TILES
from pixelheart_core.interior_layout import partition_candidate
from pixelheart_core.interior_surface_design import stage_partition_frame
from pixelheart_core.interiors import import_atlas, interior_tmx, map_layers, new_interior, render_interior
from pixelheart_core.projects import copy_project, load_project, new_project, save_project
from pixelheart_core.world import asset_path, new_location, new_world


class InteriorPartitionFrameTests(unittest.TestCase):
    OUTSIDE = (5, 3, 4, 255)
    LEFT_WOOD = (224, 112, 32, 255)
    RIGHT_WOOD = (140, 66, 24, 255)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project_root = self.root / "author"
        self.project_root.mkdir()

    def design(self):
        data = new_interior()
        roles = dict(zip(ROOM_FRAME_TILES, range(len(ROOM_FRAME_TILES))))
        source = self.project_root / "source-frame.png"
        with Image.new("RGBA", (128, 32)) as sheet:
            for index in range(16):
                x, y = index % 8 * 16, index // 8 * 16
                sheet.paste((index * 11, 70 + index * 3, 180-index * 4, 255), (x, y, x+16, y+16))
            for role, inner_color, inner_x in (("right", self.LEFT_WOOD, 0), ("left", self.RIGHT_WOOD, 8)):
                index = roles[role]
                x, y = index % 8 * 16, index // 8 * 16
                sheet.paste(self.OUTSIDE, (x, y, x+16, y+16))
                sheet.paste(inner_color, (x+inner_x, y, x+inner_x+8, y+16))
            sheet.save(source)
        data["atlas"] = import_atlas(source, self.project_root)
        data["room_frame"] = roles
        data["style"].update(floor=10, wall_top=11, wall_middle=12, wall_bottom=13)
        data["style"]["floor_pattern"] = {"width": 2, "height": 2, "tiles": [10, 10, 10, 10]}
        data["room_styles"] = {"main": {"wall_pattern": {"width": 1, "height": 3, "tiles": [11, 12, 13]}}}
        return data

    def tile(self, data, index, root=None):
        with Image.open(asset_path(data["atlas"]["asset"], root or self.project_root)) as atlas:
            columns = data["atlas"]["columns"]
            x, y = index % columns * 16, index // columns * 16
            return atlas.crop((x, y, x+16, y+16)).convert("RGBA")

    def test_native_inner_halves_make_trim_without_black_exterior_pixels(self):
        data = stage_partition_frame(self.design(), self.project_root)
        with self.tile(data, data["room_frame"]["partition_vertical"]) as body:
            self.assertEqual(body.getpixel((0, 8)), self.LEFT_WOOD)
            self.assertEqual(body.getpixel((7, 8)), self.LEFT_WOOD)
            self.assertEqual(body.getpixel((8, 8)), self.RIGHT_WOOD)
            self.assertEqual(body.getpixel((15, 8)), self.RIGHT_WOOD)
            self.assertNotIn(self.OUTSIDE, {color for _, color in body.getcolors()})
        with self.tile(data, data["room_frame"]["partition_cap"]) as cap:
            self.assertEqual(cap.getpixel((8, 0)), self.RIGHT_WOOD)
            self.assertEqual(cap.getpixel((8, 15)), self.LEFT_WOOD)
            self.assertNotIn(self.OUTSIDE, {color for _, color in cap.getcolors()})

    def test_staging_preserves_original_pixels_styles_and_source_document(self):
        original = self.design()
        before = deepcopy(original)
        reference = asset_path(original["atlas"]["asset"], self.project_root)
        source_bytes = reference.read_bytes()
        data = stage_partition_frame(original, self.project_root)
        self.assertEqual(original, before)
        self.assertEqual(reference.read_bytes(), source_bytes)
        self.assertEqual(data["style"], before["style"])
        self.assertEqual(data["room_styles"], before["room_styles"])
        for role, tile in before["room_frame"].items():
            self.assertEqual(data["room_frame"][role], tile)
        with Image.open(reference) as source, Image.open(asset_path(data["atlas"]["asset"], self.project_root)) as staged:
            with staged.crop((0, 0, source.width, source.height)) as preserved:
                self.assertEqual(preserved.tobytes(), source.tobytes())

    def test_repeated_staging_is_idempotent_without_reappending_assets(self):
        first = stage_partition_frame(self.design(), self.project_root)
        files = {path.relative_to(self.project_root) for path in self.project_root.rglob("*.png")}
        second = stage_partition_frame(first, self.project_root)
        self.assertEqual(second, first)
        self.assertEqual({path.relative_to(self.project_root) for path in self.project_root.rglob("*.png")}, files)

    def test_complete_supplied_partition_art_is_respected(self):
        original = self.design()
        original["room_frame"].update(partition_vertical=14, partition_cap=15)
        files = {path.relative_to(self.project_root) for path in self.project_root.rglob("*.png")}
        staged = stage_partition_frame(original, self.project_root)
        self.assertEqual(staged, original)
        self.assertEqual({path.relative_to(self.project_root) for path in self.project_root.rglob("*.png")}, files)

    def test_partial_supplied_partition_art_is_preserved_when_missing_role_is_added(self):
        original = self.design()
        original["room_frame"]["partition_vertical"] = 14
        staged = stage_partition_frame(original, self.project_root)
        self.assertEqual(staged["room_frame"]["partition_vertical"], 14)
        self.assertIn("partition_cap", staged["room_frame"])
        self.assertEqual(original["room_frame"].get("partition_cap"), None)

    def test_partition_art_survives_save_copy_without_original_source(self):
        data = stage_partition_frame(self.design(), self.project_root)
        data = partition_candidate(data, "main", "horizontal", 2, 9, 10, opening_width=2)
        document = new_project()
        location = new_location()
        location.update(name="Their home", internal_name="TheirHome", interior=data,
                        entry_x=data["entry"][0], entry_y=data["entry"][1], exit_x=4, exit_y=11)
        document["world"] = {**new_world(), "locations": [location]}
        original_file = self.project_root / "character.json"
        save_project(document, original_file)
        source_bytes = asset_path(data["atlas"]["asset"], self.project_root).read_bytes()
        with render_interior(data, self.project_root) as rendered:
            expected_pixels = rendered.tobytes()
        destination = self.root / "copy" / "character.json"
        copy_project(document, original_file, destination)
        shutil.rmtree(self.project_root)
        copied = load_project(destination)["world"]["locations"][0]["interior"]
        self.assertEqual(copied, data)
        self.assertEqual(asset_path(copied["atlas"]["asset"], destination.parent).read_bytes(), source_bytes)
        with render_interior(copied, destination.parent) as rendered:
            self.assertEqual(rendered.tobytes(), expected_pixels)
        self.assertEqual(interior_tmx(copied), interior_tmx(data))

    def assert_front_tile(self, data, x, y, role):
        expected = data["room_frame"][role]
        layers = map_layers(data)
        self.assertEqual(layers["Front"][y*data["width"]+x], expected+1)
        xml = ET.fromstring(interior_tmx(data))
        exported = [int(value) for value in xml.find("layer[@name='Front']/data").text.split(",")]
        self.assertEqual(exported[y*data["width"]+x], expected+1)
        with self.tile(data, expected) as tile, render_interior(data, self.project_root) as rendered:
            with rendered.crop((x*16, y*16, (x+1)*16, (y+1)*16)) as actual:
                self.assertEqual(actual.tobytes(), tile.tobytes())

    def test_horizontal_jambs_and_top_trim_match_render_and_tmx(self):
        data = stage_partition_frame(self.design(), self.project_root)
        data = partition_candidate(data, "main", "horizontal", 2, 9, 10, opening_width=2)
        for x in (2, 5, 8, 11):
            for y in (7, 8, 9):
                self.assert_front_tile(data, x, y, "partition_vertical")
        for x in (3, 4, 9, 10):
            self.assert_front_tile(data, x, 7, "partition_cap")
        layers = map_layers(data)
        for x in (6, 7):
            self.assertEqual(layers["Buildings"][9*data["width"]+x], 0)
            for y in (7, 8, 9):
                self.assertEqual(layers["Front"][y*data["width"]+x], 0)

    def test_vertical_endcaps_and_body_match_render_and_tmx(self):
        data = stage_partition_frame(self.design(), self.project_root)
        data = partition_candidate(data, "main", "vertical", 8, 5, 8, opening_width=2)
        for y in (5, 7, 10, 12):
            self.assert_front_tile(data, 8, y, "partition_cap")
        for y in (6, 11):
            self.assert_front_tile(data, 8, y, "partition_vertical")
        layers = map_layers(data)
        for y in (8, 9):
            self.assertEqual(layers["Buildings"][y*data["width"]+8], 0)
            self.assertEqual(layers["Front"][y*data["width"]+8], 0)


if __name__ == "__main__":
    unittest.main()
