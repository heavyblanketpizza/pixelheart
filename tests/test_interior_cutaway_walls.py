"""Closed room walls preserve passages, native shell geometry, and export behavior.

All artwork is a synthetic test atlas; no game assets are used here.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from pixelheart_core.interior_furniture import (
    ROOM_FRAME_JOINS, ROOM_FRAME_TILES, validate_definition,
)
from pixelheart_core.interior_layout import (
    opening_approaches, partition_candidate, partition_cells, partition_footprint,
    partition_opening_rectangle, partition_rectangle, resize_room_candidate,
    shell_floor_cells, shell_wall_regions,
)
from pixelheart_core.interiors import (
    InteriorError, compile_interior, floor_cells, import_atlas, interior_background,
    interior_tmx, map_layers, new_interior, normalize_interior, reachable_tiles,
    render_interior,
)
from pixelheart_core.world import asset_path


class InteriorCutawayWallTests(unittest.TestCase):
    OUTSIDE = (5, 3, 4, 255)
    FLOOR = (70, 170, 220, 255)
    FACE_COLORS = ((180, 40, 200, 255), (130, 35, 180, 255), (80, 30, 160, 255))
    LEFT_EDGE = (240, 90, 50, 255)
    RIGHT_EDGE = (70, 230, 80, 255)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def design(self):
        data = new_interior()
        data.update(width=24, height=24, entry=[4, 18])
        data["rooms"][0].update(width=16, height=16)
        roles = (*ROOM_FRAME_TILES, *ROOM_FRAME_JOINS,
                 "partition_vertical", "partition_cap")
        frame = {role: index for index, role in enumerate(roles)}
        first_surface = len(frame)
        source = self.root / "synthetic.png"
        with Image.new("RGBA", (32 * 16, 16), self.OUTSIDE) as sheet:
            for role, index in frame.items():
                x = index * 16
                sheet.paste((20 + index * 10, 220 - index * 8, 80, 255), (x, 0, x+16, 16))
                if role == "right":
                    sheet.paste(self.OUTSIDE, (x, 0, x+16, 16))
                    sheet.paste(self.LEFT_EDGE, (x, 0, x+8, 16))
                elif role == "left":
                    sheet.paste(self.OUTSIDE, (x, 0, x+16, 16))
                    sheet.paste(self.RIGHT_EDGE, (x+8, 0, x+16, 16))
                elif role.startswith("bottom"):
                    sheet.paste((0, 0, 0, 0), (x, 0, x+16, 8))
            for offset, color in enumerate((self.FLOOR, *self.FACE_COLORS)):
                x = (first_surface + offset) * 16
                sheet.paste(color, (x, 0, x+16, 16))
            sheet.save(source)
        data["atlas"] = import_atlas(source, self.root)
        data["room_frame"] = frame
        data["style"].update(zip(("floor", "wall_top", "wall_middle", "wall_bottom"),
                                  range(first_surface, first_surface + 4)))
        return normalize_interior(data)

    def vertical(self):
        return partition_candidate(self.design(), "main", "vertical", 9, 5, 16,
                                   thickness=2, openings=[{"offset": 10, "width": 3}])

    def horizontal(self):
        return partition_candidate(self.design(), "main", "horizontal", 2, 10, 16,
                                   thickness=5, openings=[{"offset": 7, "width": 2}])

    def value(self, data, layers, layer, x, y):
        return layers[layer][y * data["width"] + x]

    def test_vertical_wall_has_opposing_edges_black_center_and_wall_face_above_passage(self):
        data = self.vertical()
        layers = map_layers(data)
        frame = data["room_frame"]
        for x, role in ((9, "right"), (10, "left")):
            self.assertEqual(self.value(data, layers, "Back", x, 8), 0)
            self.assertEqual(self.value(data, layers, "Front", x, 8), frame[role]+1)
        for x, role in ((9, "top_join_left"), (10, "top_join_right")):
            self.assertEqual(self.value(data, layers, "Front", x, 11), frame[role]+1)
            for y, style in ((12, "wall_top"), (13, "wall_middle"), (14, "wall_bottom")):
                self.assertEqual(self.value(data, layers, "Back", x, y), data["style"][style]+1)
        with render_interior(data, self.root) as image:
            self.assertEqual(image.getpixel((9*16+3, 8*16+8)), self.LEFT_EDGE)
            self.assertEqual(image.getpixel((9*16+12, 8*16+8)), self.OUTSIDE)
            self.assertEqual(image.getpixel((10*16+3, 8*16+8)), self.OUTSIDE)
            self.assertEqual(image.getpixel((10*16+12, 8*16+8)), self.RIGHT_EDGE)
            for x in (9, 10):
                for y, color in zip((12, 13, 14), self.FACE_COLORS):
                    self.assertEqual(image.getpixel((x*16+8, y*16+8)), color)
                self.assertEqual(image.getpixel((x*16+8, 15*16+8)), self.FLOOR)

    def test_horizontal_wall_has_void_cap_three_face_rows_and_full_depth_passage(self):
        data = self.horizontal()
        layers = map_layers(data)
        self.assertEqual(self.value(data, layers, "Back", 5, 10), 0)
        self.assertEqual(self.value(data, layers, "Front", 5, 10), 0)
        self.assertEqual(self.value(data, layers, "Front", 5, 11), data["room_frame"]["top"]+1)
        for y, style in ((12, "wall_top"), (13, "wall_middle"), (14, "wall_bottom")):
            self.assertEqual(self.value(data, layers, "Back", 5, y), data["style"][style]+1)
        with render_interior(data, self.root) as image:
            self.assertEqual(image.getpixel((5*16+8, 10*16+8)), self.OUTSIDE)
            # The upper room's trim leaves the underlying floor visible.
            self.assertEqual(image.getpixel((5*16+8, 9*16+3)), self.FLOOR)
            for x in (9, 10):
                for y in range(10, 15):
                    self.assertEqual(image.getpixel((x*16+8, y*16+8)), self.FLOOR)

    def test_every_structural_cell_blocks_and_every_remaining_floor_cell_is_reachable(self):
        for create in (self.vertical, self.horizontal):
            with self.subTest(axis=create.__name__):
                data = create()
                layers = map_layers(data)
                blocked = partition_cells(data)
                walkable = floor_cells(data) - blocked
                self.assertEqual(shell_floor_cells(data), walkable)
                self.assertEqual(reachable_tiles(data), walkable)
                self.assertTrue(opening_approaches(data) <= walkable)
                for x, y in blocked:
                    self.assertTrue(self.value(data, layers, "Buildings", x, y), (x, y))
                for x, y in walkable:
                    self.assertEqual(self.value(data, layers, "Buildings", x, y), 0, (x, y))

    def test_wall_and_opening_rectangles_cover_the_complete_reserved_area(self):
        for create, rectangle, opening in (
                (self.vertical, (9, 5, 2, 16), (9, 15, 2, 3)),
                (self.horizontal, (2, 10, 16, 5), (9, 10, 2, 5))):
            with self.subTest(axis=create.__name__):
                wall = create()["partitions"][0]
                self.assertEqual(partition_rectangle(wall), rectangle)
                self.assertEqual(partition_rectangle(wall, visual=True), rectangle)
                self.assertEqual(partition_opening_rectangle(wall, wall["openings"][0]), opening)
                x, y, width, height = rectangle
                self.assertEqual(partition_footprint(wall, include_openings=True),
                                 {(tx, ty) for tx in range(x, x+width) for ty in range(y, y+height)})
                self.assertEqual(len(partition_footprint(wall)), width*height-opening[2]*opening[3])

    def test_export_regions_exclude_cavity_and_include_new_wall_faces(self):
        for create, wall_top in ((self.vertical, (9, 12)), (self.horizontal, (5, 12))):
            with self.subTest(axis=create.__name__):
                data = create()
                result = compile_interior(data, "TestHome", "TestNpc", self.root, "interior/")
                edit = next(patch for patch in result["patches"] if patch["Action"] == "EditMap")
                def marked(name):
                    return {(tile["Position"]["X"], tile["Position"]["Y"])
                            for tile in edit["MapTiles"] if name in tile["SetProperties"]}
                floor_ids, wall_ids = marked("FloorID"), marked("WallID")
                self.assertEqual(floor_ids, shell_floor_cells(data))
                self.assertTrue(floor_ids.isdisjoint(partition_cells(data)))
                self.assertIn(wall_top, wall_ids)
                walls, caps = shell_wall_regions(data)
                self.assertIn(wall_top, walls["main"])
                self.assertIn((wall_top[0], wall_top[1]-1), caps["main"])

    def test_exported_tiles_composite_to_the_same_pixels_as_the_editor(self):
        for create in (self.vertical, self.horizontal):
            with self.subTest(axis=create.__name__):
                data = create()
                xml = ET.fromstring(interior_tmx(data))
                layers = map_layers(data)
                with Image.open(asset_path(data["atlas"]["asset"], self.root)).convert("RGBA") as atlas:
                    with Image.new("RGBA", (data["width"]*16, data["height"]*16), interior_background(data)) as exported:
                        for name in ("Back", "Buildings", "Front"):
                            values = [int(value) for value in xml.find(f"layer[@name='{name}']/data").text.split(",")]
                            self.assertEqual(values, layers[name])
                            for position, gid in enumerate(values):
                                if not gid or gid == data["atlas"]["tile_count"]+1:
                                    continue
                                x, y = (gid-1) % data["atlas"]["columns"]*16, (gid-1) // data["atlas"]["columns"]*16
                                with atlas.crop((x, y, x+16, y+16)) as tile:
                                    exported.alpha_composite(tile, (position % data["width"]*16, position // data["width"]*16))
                        with render_interior(data, self.root) as preview:
                            self.assertEqual(preview.tobytes(), exported.tobytes())

    def test_second_column_and_last_row_cannot_cover_furniture(self):
        for axis, origin, length, thickness, furniture in (
                ("vertical", (9, 5), 16, 2, (10, 8)),
                ("horizontal", (2, 10), 16, 5, (5, 14))):
            with self.subTest(axis=axis):
                data = self.design()
                data["catalog"] = [validate_definition({"id": "(F)TestChair", "name": "Test chair", "kind": "chair",
                                                       "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1})]
                data["furniture"] = [{"id": "chair", "item_id": "(F)TestChair", "x": furniture[0], "y": furniture[1],
                                      "rotation": 0, "mod_data": {}}]
                before = deepcopy(data)
                with self.assertRaises(InteriorError):
                    partition_candidate(data, "main", axis, *origin, length, thickness=thickness, opening_width=2)
                self.assertEqual(data, before)

    def test_room_resize_cannot_crop_wall_thickness(self):
        original = self.design()
        original["rooms"][0]["width"] = 17
        data = partition_candidate(original, "main", "vertical", 16, 5, 16,
                                   thickness=2, opening_width=3)
        before = deepcopy(data)
        with self.assertRaisesRegex(InteriorError, "entire wall"):
            resize_room_candidate(data, "main", 2, 5, 15, 16)
        self.assertEqual(data, before)

    def test_legacy_dividers_keep_custom_trim_and_original_footprint(self):
        original = self.design()
        data = partition_candidate(original, "main", "vertical", 9, 5, 16,
                                   openings=[{"offset": 10, "width": 3}])
        self.assertNotIn("thickness", data["partitions"][0])
        self.assertEqual(data["room_frame"], original["room_frame"])
        self.assertEqual(shell_floor_cells(data), floor_cells(data))
        self.assertEqual({x for x, _ in partition_cells(data)}, {9})
        layers = map_layers(data)
        self.assertEqual(self.value(data, layers, "Front", 9, 8), data["room_frame"]["partition_vertical"]+1)
        self.assertEqual(self.value(data, layers, "Front", 9, 5), data["room_frame"]["partition_cap"]+1)
        explicit = deepcopy(data)
        explicit["partitions"][0]["thickness"] = 1
        self.assertEqual(map_layers(explicit), layers)
        self.assertEqual(normalize_interior(data), data)


if __name__ == "__main__":
    unittest.main()
