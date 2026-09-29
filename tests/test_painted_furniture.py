"""Repainted game furniture keeps the game's sheet layout and becomes a new item."""
from copy import deepcopy
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import FurnitureValidationError, definition_assets, validate_definition
from pixelheart_core.interiors import interior_asset_references, new_interior
from pixelheart_core.painted_furniture import (
    LOCAL_PREFIX, PaintedFurnitureError, build_painted, game_extras, painted_front_png, painted_texture_png,
    paintable, painting_box, painting_canvas,
)
from tests.test_game_scene_assets import texture
from tests.test_vanilla_story import dictionary_xnb

WIDTH = 64
SHEET = "world_assets/interiors/textures/sheet.png"
CHAIR = (200, 40, 40, 255)
NEIGHBOR = (20, 200, 20, 255)
LAMP_OFF = (90, 90, 200, 255)
LAMP_ON = (250, 230, 120, 255)


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mirrored(original):
    sheet = Image.new("RGBA", (original.width * 2, original.height))
    sheet.paste(original, (0, 0))
    sheet.paste(original.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (original.width, 0))
    return sheet


def game_sheet():
    """A 64 × 64 test sheet: a neighbor, a four-way chair, and a lamp with a lit frame."""
    original = Image.new("RGBA", (WIDTH, 64))
    original.paste(NEIGHBOR, (0, 16, 16, 48))
    original.paste(CHAIR, (16, 16, 64, 48))
    original.putpixel((33, 17), (1, 2, 3, 255))  # marks rotation 1's top-left corner
    original.paste((200, 200, 200, 255), (0, 0, 64, 16))  # other furniture above
    original.paste(LAMP_OFF, (0, 48, 16, 64))
    original.paste(LAMP_ON, (16, 48, 32, 64))
    return original


def chair(**changes):
    definition = {
        "id": "(F)Test.Chair", "name": "Oak Chair", "kind": "chair",
        "footprint": [1, 1], "sprite_size": [1, 2], "rotations": 4,
        "texture": "TileSheets/furniture", "preview_asset": SHEET,
        "frames": [
            {"rotation": 0, "rect": [16, 16, 16, 32], "duration_ms": 100},
            {"rotation": 1, "rect": [32, 16, 16, 32], "duration_ms": 100},
            {"rotation": 2, "rect": [48, 16, 16, 32], "duration_ms": 100},
            # Facing left: the game flips rotation 1's sprite, found in the mirror half.
            {"rotation": 3, "rect": [2 * WIDTH - 32 - 16, 16, 16, 32], "duration_ms": 100},
        ],
    }
    definition.update(changes)
    return definition


def lamp(**changes):
    definition = {
        "id": "(F)Test.Lamp", "name": "Lamp", "kind": "lamp",
        "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 1,
        "texture": "TileSheets/furniture", "preview_asset": SHEET,
        "frames": [{"rotation": 0, "rect": [0, 48, 16, 16], "duration_ms": 100}],
        "preview_variants": {"night_on": [{"rotation": 0, "rect": [16, 48, 16, 16], "duration_ms": 100}]},
        "preview_lights": [
            {"rotation": 0, "offset": [0.5, 0.5], "radius": 2, "color": "#ffeeaa", "intensity": 0.8,
             "when": "night", "mask_rect": [16, 48, 16, 16]},
            {"rotation": 0, "offset": [0.5, 0.5], "radius": 1, "color": "#ffffff", "intensity": 0.5,
             "when": "night", "mask_rect": [0, 0, 16, 16], "blend": "overlay"},
        ],
    }
    definition.update(changes)
    return definition


class PaintedFurnitureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.write_sheet(mirrored(game_sheet()))

    def write_sheet(self, image):
        path = self.root / SHEET
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def image(self, reference):
        with Image.open(self.root / reference) as image:
            return image.convert("RGBA")

    def painted_canvas(self, definition=None, color=(10, 120, 250, 255), at=(0, 0)):
        canvas = painting_canvas(definition or chair(), self.root)
        canvas.putpixel(at, color)
        return png(canvas)

    def test_box_covers_all_original_frames(self):
        self.assertEqual(painting_box(validate_definition(chair()), WIDTH), (16, 16, 64, 48))
        self.assertEqual(painting_box(validate_definition(lamp()), WIDTH), (0, 48, 32, 64))

    def test_canvas_clears_neighbors_and_keeps_the_piece(self):
        canvas = painting_canvas(lamp(frames=[{"rotation": 0, "rect": [0, 48, 16, 16]}], preview_variants={
            "night_on": [{"rotation": 0, "rect": [32, 48, 16, 16]}]}), self.root)
        self.assertEqual(canvas.size, (48, 16))
        self.assertEqual(canvas.getpixel((0, 0)), LAMP_OFF)
        self.assertEqual(canvas.getpixel((16, 0))[3], 0)  # the lit frame at 16 is not this piece's
        chair_canvas = painting_canvas(chair(), self.root)
        self.assertEqual(chair_canvas.size, (48, 32))
        self.assertEqual(chair_canvas.getpixel((0, 0)), CHAIR)

    def test_band_keeps_width_and_shifts_sprite_index(self):
        painted = build_painted(chair(), self.painted_canvas(), self.root)
        self.assertTrue(painted["id"].startswith(LOCAL_PREFIX))
        self.assertEqual(painted["name"], "Painted Oak Chair")
        # Original index: row 1 of a 4-tile-wide sheet, column 1 → 5; one row cut → 1.
        self.assertEqual(painted["sprite_index"], 1)
        self.assertEqual(painted["painted_from"]["sprite_index"], 5)
        self.assertEqual(painted["painted_from"]["row_offset"], 1)
        self.assertEqual(painted["painted_from"]["id"], "(F)Test.Chair")
        with Image.open(io.BytesIO(painted_texture_png(painted, self.root))) as texture:
            self.assertEqual(texture.size, (WIDTH, 32))
            self.assertEqual(texture.getpixel((16, 0)), (10, 120, 250, 255))
            self.assertEqual(texture.getpixel((17, 0)), CHAIR)
            self.assertEqual(texture.getpixel((0, 0))[3], 0)  # the neighbor is gone
        self.assertEqual([frame["rect"] for frame in painted["frames"]],
                         [[16, 0, 16, 32], [32, 0, 16, 32], [48, 0, 16, 32], [80, 0, 16, 32]])

    def test_flipped_rotation_uses_the_mirror_half(self):
        painted = build_painted(chair(), self.painted_canvas(at=(16, 0)), self.root)
        atlas = self.image(painted["preview_asset"])
        self.assertEqual(atlas.size, (WIDTH * 2, 32))
        # Canvas (16, 0) is sheet column 32: rotation 1's corner, and the right edge of rotation 3.
        self.assertEqual(atlas.getpixel((32, 0)), (10, 120, 250, 255))
        self.assertEqual(atlas.getpixel((2 * WIDTH - 1 - 32, 0)), (10, 120, 250, 255))

    def test_variants_and_lights_are_remapped(self):
        painted = build_painted(lamp(), self.painted_canvas(lamp()), self.root)
        self.assertEqual(painted["frames"][0]["rect"], [0, 0, 16, 16])
        self.assertEqual(painted["preview_variants"]["night_on"][0]["rect"], [16, 0, 16, 16])
        self.assertEqual(len(painted["preview_lights"]), 1)
        self.assertEqual(painted["preview_lights"][0]["mask_rect"], [16, 0, 16, 16])
        self.assertEqual(painted["sprite_index"], 0)
        self.assertNotIn("front_asset", painted)

    def test_same_pixels_same_id(self):
        canvas = self.painted_canvas()
        first = build_painted(chair(), canvas, self.root)
        second = build_painted(chair(), canvas, self.root)
        self.assertEqual(first, second)
        other = build_painted(chair(), self.painted_canvas(color=(1, 1, 1, 255)), self.root)
        self.assertNotEqual(first["id"], other["id"])

    def test_seat_front_masks_painted_pixels(self):
        front = Image.new("RGBA", (WIDTH, 64))
        front.paste((0, 0, 0, 255), (16, 40, 32, 48))  # the chair's front edge
        painted = build_painted(chair(), self.painted_canvas(at=(0, 30)), self.root, front_sheet=front)
        with Image.open(io.BytesIO(painted_front_png(painted, self.root))) as image:
            layer = image.convert("RGBA")
        self.assertEqual(layer.size, (WIDTH, 32))
        self.assertEqual(layer.getpixel((16, 30)), (10, 120, 250, 255))
        self.assertEqual(layer.getpixel((17, 24)), CHAIR)
        self.assertEqual(layer.getpixel((17, 10))[3], 0)

    def test_seat_front_is_transparent_without_front_sheet(self):
        painted = build_painted(chair(), self.painted_canvas(), self.root)
        with Image.open(io.BytesIO(painted_front_png(painted, self.root))) as image:
            self.assertEqual(image.convert("RGBA").getextrema()[3], (0, 0))

    def test_price_is_kept(self):
        painted = build_painted(chair(), self.painted_canvas(), self.root, price=350)
        self.assertEqual(painted["painted_from"]["price"], 350)
        self.assertEqual(build_painted(chair(), self.painted_canvas(), self.root)["painted_from"]["price"], 0)

    def test_repainted_canvas_is_the_band_crop_and_keeps_its_origin(self):
        first = build_painted(chair(), self.painted_canvas(), self.root, price=350)
        canvas = painting_canvas(first, self.root)
        self.assertEqual(canvas.size, (48, 32))
        self.assertEqual(canvas.getpixel((0, 0)), (10, 120, 250, 255))
        canvas.putpixel((1, 0), (9, 9, 9, 255))
        second = build_painted(first, png(canvas), self.root)
        self.assertEqual(second["name"], "Painted Oak Chair")
        self.assertEqual(second["painted_from"], first["painted_from"])
        self.assertEqual(second["sprite_index"], 1)

    def test_unpaintable_reasons(self):
        self.assertIsNone(paintable(chair(), self.root))
        self.assertIn("Beds", paintable(chair(kind="bed"), self.root))
        self.assertIn("Fish tanks", paintable(chair(kind="fishtank"), self.root))
        self.assertIn("Televisions", paintable(chair(id="(F)1466"), self.root))
        self.assertIn("no picture", paintable(chair(frames=[]), self.root))
        self.assertIn("16-pixel", paintable(chair(frames=[{"rotation": 0, "rect": [8, 16, 16, 32]}]), self.root))
        self.assertIn("missing", paintable(chair(preview_asset="world_assets/interiors/textures/gone.png"), self.root))
        self.write_sheet(game_sheet())
        self.assertIn("game library", paintable(chair(), self.root))
        with self.assertRaises(PaintedFurnitureError):
            painting_canvas(chair(), self.root)

    def test_canvas_size_must_match(self):
        with self.assertRaises(PaintedFurnitureError):
            build_painted(chair(), png(Image.new("RGBA", (16, 16))), self.root)

    def test_asset_references_include_front(self):
        painted = build_painted(chair(), self.painted_canvas(), self.root)
        self.assertEqual(definition_assets(painted), [painted["preview_asset"], painted["front_asset"]])
        design = new_interior("residence")
        design["catalog"] = [painted]
        self.assertIn(painted["front_asset"], set(interior_asset_references(design)))

    def test_game_extras_read_the_front_sheet_and_price(self):
        content = self.root / "Content"
        (content / "TileSheets").mkdir(parents=True)
        (content / "Data").mkdir()
        (content / "TileSheets/furnitureFront.xnb").write_bytes(texture((WIDTH, 64)))
        (content / "Data/Furniture.xnb").write_bytes(dictionary_xnb({"Test.Chair": "Oak Chair/chair/1 2/1 1/4/350/-1/Oak Chair"}))
        front, price = game_extras(content, chair())
        self.assertEqual((front.size, price), ((WIDTH, 64), 350))
        painted = build_painted(chair(), self.painted_canvas(), self.root, front_sheet=front, price=price)
        again, repainted_price = game_extras(content, painted)
        self.assertEqual(again.size, (WIDTH, 48))  # cut by the painted piece's row offset
        self.assertIsNone(repainted_price)
        self.assertEqual(game_extras(None, chair()), (None, None))
        self.assertEqual(game_extras(content, lamp()), (None, None))

    def test_validate_definition_accepts_painted_fields(self):
        painted = build_painted(chair(), self.painted_canvas(), self.root)
        self.assertEqual(validate_definition(painted), painted)
        broken = deepcopy(painted)
        broken["painted_from"] = {"id": 5}
        with self.assertRaises(FurnitureValidationError):
            validate_definition(broken)
        broken = deepcopy(painted)
        broken["front_asset"] = "front.gif"
        with self.assertRaises(FurnitureValidationError):
            validate_definition(broken)


if __name__ == "__main__":
    unittest.main()
