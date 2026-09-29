"""Villager references read directly from a synthetic installed game."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from pixelheart_core.local_templates import (
    GAME_SOURCE, LocalTemplateError, game_villagers, load_game_artwork, load_game_dialogue,
)
from tests.test_game_scene_assets import container
from tests.test_vanilla_story import dictionary_xnb


def texture_from(image):
    image = image.convert("RGBA")
    data = struct.pack("<5i", 0, image.width, image.height, 1, len(image.tobytes())) + image.tobytes()
    return container("Microsoft.Xna.Framework.Content.Texture2DReader", data)


class GameTemplateTests(unittest.TestCase):
    def setUp(self):
        self.content = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dialogue = self.content / "Characters/Dialogue"
        dialogue.mkdir(parents=True)
        (dialogue / "Abigail.xnb").write_bytes(dictionary_xnb({"Introduction": "Oh, hi.$h", "Mon": "Mondays...$s"}))
        (dialogue / "Abigail.de-DE.xnb").write_bytes(b"locale")
        (dialogue / "MarriageDialogueAbigail.xnb").write_bytes(b"marriage")
        (dialogue / "rainy.xnb").write_bytes(b"rainy")
        (dialogue / "Lewis.xnb").write_bytes(dictionary_xnb({"Introduction": "Welcome!"}))

    def test_villagers_are_listed_by_base_name_only(self):
        self.assertEqual(game_villagers(self.content), ["Abigail", "Lewis"])
        self.assertEqual(game_villagers(self.content / "missing"), [])

    def test_dialogue_reads_every_entry_without_local_paths(self):
        result = load_game_dialogue(self.content, "Abigail")
        self.assertEqual([row["trigger"] for row in result["examples"]], ["Introduction", "Mon"])
        self.assertEqual(result["examples"][0]["text"], "Oh, hi.$h")
        self.assertEqual(result["source"]["provider"], GAME_SOURCE["provider"])
        self.assertEqual(result["source"]["asset"], "Characters/Dialogue/Abigail")
        self.assertNotIn(str(self.content), json.dumps(result, default=str))

    def test_unsupported_vanilla_keys_are_skipped_not_fatal(self):
        (self.content / "Characters/Dialogue/Robin.xnb").write_bytes(dictionary_xnb({
            "Introduction": "Hi there!", "structureBuilt_Fish Pond": "Nice pond.", "Tue": "   "}))
        result = load_game_dialogue(self.content, "Robin")
        self.assertEqual([row["trigger"] for row in result["examples"]], ["Introduction"])
        self.assertEqual(result["skipped"], 2)

    def test_four_expression_portraits_are_accepted_as_references(self):
        (self.content / "Portraits").mkdir()
        (self.content / "Portraits/Lewis.xnb").write_bytes(texture_from(Image.new("RGBA", (128, 128), (1, 1, 1, 255))))
        (self.content / "Characters/Lewis.xnb").write_bytes(texture_from(Image.new("RGBA", (64, 224), (2, 2, 2, 255))))
        destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.assertTrue(Path(load_game_artwork(self.content, "Lewis", destination)["portrait"]).is_file())

    def test_unknown_or_unreadable_villager_is_friendly(self):
        with self.assertRaisesRegex(LocalTemplateError, "isn't in your game"):
            load_game_dialogue(self.content, "Haley")
        with self.assertRaisesRegex(LocalTemplateError, "Choose a villager"):
            load_game_dialogue(self.content, "../Abigail")
        (self.content / "Characters/Dialogue/Lewis.xnb").write_bytes(b"XNBw broken")
        with self.assertRaisesRegex(LocalTemplateError, "couldn't be read"):
            load_game_dialogue(self.content, "Lewis")

    def test_artwork_is_decoded_to_png_sheets_with_provenance(self):
        (self.content / "Portraits").mkdir()
        (self.content / "Portraits/Abigail.xnb").write_bytes(texture_from(Image.new("RGBA", (128, 192), (200, 10, 10, 255))))
        (self.content / "Characters/Abigail.xnb").write_bytes(texture_from(Image.new("RGBA", (64, 128), (10, 10, 200, 255))))
        destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        result = load_game_artwork(self.content, "Abigail", destination)
        with Image.open(result["portrait"]) as portrait:
            self.assertEqual(portrait.size, (128, 192))
            self.assertEqual(portrait.getpixel((0, 0)), (200, 10, 10, 255))
        self.assertEqual(Path(result["sprite"]).parent, destination)
        self.assertEqual(result["sprite_source"]["asset"], "Characters/Abigail")
        # Importing verifies the copied PNG against this hash.
        import hashlib
        for kind in ("portrait", "sprite"):
            self.assertEqual(result[f"{kind}_source"]["sha256"],
                             hashlib.sha256(Path(result[kind]).read_bytes()).hexdigest())

    def test_incomplete_artwork_sheet_is_rejected(self):
        (self.content / "Portraits").mkdir()
        (self.content / "Portraits/Abigail.xnb").write_bytes(texture_from(Image.new("RGBA", (100, 50))))
        destination = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with self.assertRaisesRegex(LocalTemplateError, "complete portrait sheet"):
            load_game_artwork(self.content, "Abigail", destination)


if __name__ == "__main__":
    unittest.main()
