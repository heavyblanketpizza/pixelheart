"""Paper menu pieces, plus hearts and lettering borrowed from synthetic game textures."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageColor

from pixelheart_core.game_ui import (
    INK, PANEL_RULE, REQUIRED_ROLES, build_game_ui, build_paper_ui, ui_pieces,
)
from tests.test_game_templates import texture_from

BORROWED = {"heart_full", "heart_empty", "font", "cursors"}


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
        self.write("LooseSprites/Cursors", marked((448, 480), {(211, 428): (9, 8, 7, 255)}))
        self.write("LooseSprites/font_bold", marked((128, 592), {}))

    def write(self, asset, image):
        (self.content / (asset + ".xnb")).write_bytes(texture_from(image))

    def test_game_hearts_are_cropped_at_two_pixels_per_art_pixel(self):
        pieces = build_game_ui(self.content, self.cache)
        self.assertEqual(set(pieces), BORROWED)
        with Image.open(pieces["heart_full"].path) as heart:
            self.assertEqual(heart.size, (14, 12))
            self.assertEqual(heart.getpixel((0, 0)), (9, 8, 7, 255))
            self.assertEqual(heart.getpixel((1, 1)), (9, 8, 7, 255))
            self.assertEqual(heart.getpixel((2, 0)), (240, 200, 120, 255))
        self.assertTrue(pieces["cursors"].path.is_file())

    def test_bold_font_is_inked_with_a_soft_grey_shadow(self):
        glyph, shadow = (90, 30, 10, 255), (230, 160, 80, 255)
        self.write("LooseSprites/font_bold", marked((128, 592), {(0, 0): glyph, (1, 0): shadow, (2, 0): (0, 0, 0, 0)}))
        with Image.open(build_game_ui(self.content, self.cache)["font"].path) as font:
            self.assertEqual(font.getpixel((0, 0)), ImageColor.getrgb(INK) + (255,))
            self.assertEqual(font.getpixel((1, 0)), ImageColor.getrgb(PANEL_RULE) + (255,))
            self.assertEqual(font.getpixel((2, 0))[3], 0)

    def test_cache_is_reused_and_refreshed_when_the_game_changes(self):
        first = build_game_ui(self.content, self.cache)
        with patch("pixelheart_core.game_ui.load_texture", side_effect=AssertionError("must reuse cache")):
            self.assertEqual(build_game_ui(self.content, self.cache), first)
        self.write("LooseSprites/Cursors", marked((448, 481), {(211, 428): (50, 50, 50, 255)}))
        changed = build_game_ui(self.content, self.cache)
        self.assertNotEqual(changed["heart_full"].path.parent, first["heart_full"].path.parent)

    def test_out_of_bounds_crop_keeps_the_drawn_heart(self):
        self.write("LooseSprites/Cursors", marked((100, 100), {}))
        pieces = ui_pieces(self.content, self.cache)
        self.assertIn("paper-v", pieces["heart_full"].path.parent.name)
        self.assertIn("game-", pieces["cursors"].path.parent.name)

    def test_paper_pieces_are_ink_on_warm_white(self):
        pieces = build_paper_ui(self.cache)
        self.assertEqual(set(pieces), set(REQUIRED_ROLES))
        # Only the hearts and the danger button carry color; everything else is near-neutral.
        for role in set(REQUIRED_ROLES) - {"heart_full", "heart_empty", "button_danger"}:
            with Image.open(pieces[role].path) as image:
                colors = [color for _, color in image.convert("RGBA").getcolors(256) if color[3]]
            for color in colors:
                self.assertLessEqual(max(color[:3]) - min(color[:3]), 24, role)
        with Image.open(pieces["heart_full"].path) as heart:
            self.assertEqual(heart.size, (14, 12))
            self.assertGreater(sum(count for count, color in heart.getcolors(256) if color[3]), 20)

    def test_connected_game_lends_only_hearts_lettering_and_cursors(self):
        pieces = ui_pieces(self.content, self.cache)
        self.assertEqual(set(REQUIRED_ROLES) - set(pieces), set())
        borrowed = {role for role, piece in pieces.items() if piece.path.parent.name.startswith("game-")}
        self.assertEqual(borrowed, BORROWED)

    def test_without_a_game_everything_is_drawn(self):
        self.assertEqual(set(ui_pieces(None, self.cache)), set(REQUIRED_ROLES))

    def test_nothing_is_written_to_the_game_folder(self):
        before = sorted(p.relative_to(self.content) for p in self.content.rglob("*"))
        ui_pieces(self.content, self.cache)
        self.assertEqual(sorted(p.relative_to(self.content) for p in self.content.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
