"""Interface crops from synthetic game textures; no game art is needed."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.game_ui import (
    PIECES, REQUIRED_ROLES, build_fallback_ui, build_game_ui, ui_pieces,
)
from tests.test_game_templates import texture_from


def marked(size, marks):
    image = Image.new("RGBA", size, (240, 200, 120, 255))
    for point, color in marks.items():
        image.putpixel(point, color)
    return image


class GameUiTests(unittest.TestCase):
    def setUp(self):
        self.content = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.content / "Maps").mkdir()
        (self.content / "LooseSprites").mkdir()
        (self.content / "Maps/Town.xnb").write_bytes(b"x")
        # MenuTiles art is pre-scaled ×4, so one art pixel is a 4×4 block.
        block = {(x, 256 + y): (1, 2, 3, 255) for x in range(4) for y in range(4)}
        self.write("Maps/MenuTiles", marked((64, 1088), block))
        self.write("LooseSprites/Cursors", marked((448, 480), {(432, 439): (9, 8, 7, 255)}))
        self.write("LooseSprites/textBox", marked((192, 48), {}))
        self.write("LooseSprites/font_bold", marked((128, 592), {}))

    def write(self, asset, image):
        (self.content / (asset + ".xnb")).write_bytes(texture_from(image))

    def test_game_pieces_are_cropped_and_scaled_to_two_pixels_per_art_pixel(self):
        pieces = build_game_ui(self.content, self.cache)
        with Image.open(pieces["button"].path) as button:
            self.assertEqual(button.size, (18, 18))
            self.assertEqual(button.getpixel((0, 0)), (9, 8, 7, 255))
            self.assertEqual(button.getpixel((1, 1)), (9, 8, 7, 255))
        with Image.open(pieces["panel"].path) as panel:
            self.assertEqual(panel.size, (30, 30))
            self.assertEqual(panel.getpixel((0, 0)), (1, 2, 3, 255))
        self.assertEqual(pieces["panel"].margin, PIECES["panel"][3])
        self.assertTrue(pieces["font"].path.is_file())
        self.assertTrue(pieces["cursors"].path.is_file())
        for role in ("button_hover", "button_pressed", "button_disabled", "button_primary", "button_danger", "tab_idle", "textbox_focus"):
            self.assertTrue(pieces[role].path.is_file(), role)

    def test_cache_is_reused_and_refreshed_when_the_game_changes(self):
        first = build_game_ui(self.content, self.cache)
        with patch("pixelheart_core.game_ui.load_texture", side_effect=AssertionError("must reuse cache")):
            self.assertEqual(build_game_ui(self.content, self.cache), first)
        self.write("LooseSprites/Cursors", marked((448, 481), {(432, 439): (50, 50, 50, 255)}))
        changed = build_game_ui(self.content, self.cache)
        self.assertNotEqual(changed["button"].path.parent, first["button"].path.parent)

    def test_out_of_bounds_crop_uses_original_piece(self):
        self.write("LooseSprites/Cursors", marked((100, 100), {}))
        pieces, source = ui_pieces(self.content, self.cache)
        self.assertEqual(source, "mixed")
        self.assertIn("original", pieces["button"].path.parent.name)
        self.assertIn("game-", pieces["panel"].path.parent.name)

    def test_fallback_draws_every_required_role(self):
        pieces = build_fallback_ui(self.cache)
        self.assertEqual(set(REQUIRED_ROLES) - set(pieces), set())
        self.assertNotIn("font", pieces)
        with Image.open(pieces["heart_full"].path) as heart:
            self.assertEqual(heart.size, (14, 12))
            self.assertGreater(sum(1 for pixel in heart.getdata() if pixel[3]), 20)

    def test_primary_and_danger_buttons_are_calm_not_neon(self):
        import colorsys
        pieces = build_fallback_ui(self.cache)
        for role in ("button_primary", "button_danger"):
            with Image.open(pieces[role].path) as image:
                r, g, b, _ = image.convert("RGBA").getpixel((image.width // 2, image.height // 2))
                _, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                self.assertLessEqual(value, 0.8, role)
                self.assertLessEqual(saturation, 0.85, role)

    def test_without_a_game_everything_is_original(self):
        pieces, source = ui_pieces(None, self.cache)
        self.assertEqual(source, "original")
        self.assertEqual(set(REQUIRED_ROLES) - set(pieces), set())

    def test_nothing_is_written_to_the_game_folder(self):
        before = sorted(p.relative_to(self.content) for p in self.content.rglob("*"))
        ui_pieces(self.content, self.cache)
        self.assertEqual(sorted(p.relative_to(self.content) for p in self.content.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
