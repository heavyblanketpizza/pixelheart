"""The place a routine stop is at, as a picture with its tile size."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.interiors import ensure_doorway, new_interior
from pixelheart_core.projects import new_project
from pixelheart_core.route_maps import load_route_map
from pixelheart_core.world import new_location, new_world


class RouteMapTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project_file = self.root / "character.json"
        self.document = new_project()
        self.document["world"] = new_world()

    def place(self, **fields):
        record = new_location()
        record.update(name="Bookshop", internal_name="Bookshop", **fields)
        self.document["world"]["locations"].append(record)
        return record

    def test_a_supplied_project_map(self):
        (self.root / "maps").mkdir()
        Image.new("RGBA", (64, 48), (90, 140, 90, 255)).save(self.root / "maps" / "bookshop.png")
        self.place(map="maps/bookshop.png")
        result = load_route_map(self.document, self.project_file, "Bookshop")
        self.assertEqual(result["map_size"], (4, 3))
        self.assertEqual(result["image"].size, (64, 48))
        self.assertEqual(result["label"], "Bookshop")
        self.assertEqual(result["note"], "Your supplied map")

    def test_a_designed_home_room_is_drawn(self):
        design = ensure_doorway(new_interior("residence"))
        self.place(map=None, interior=design)
        result = load_route_map(self.document, self.project_file, "Bookshop")
        self.assertEqual(result["map_size"], (design["width"], design["height"]))
        self.assertEqual(result["image"].size, (design["width"] * 16, design["height"] * 16))
        self.assertEqual(result["note"], "Your Home design")

    def test_a_game_map(self):
        game = {"image": Image.new("RGBA", (32, 32)), "foreground": Image.new("RGBA", (32, 32)),
                "map_size": (2, 2), "path": self.root / "Town.xnb", "cache_key": (), "source": "Stardew Valley game map"}
        with patch("pixelheart_core.scene_preview.load_game_map", return_value=game) as loader:
            result = load_route_map(self.document, self.project_file, "Town", game_root=self.root)
        loader.assert_called_once()
        self.assertEqual(result["map_size"], (2, 2))
        self.assertIsNotNone(result["foreground"])
        self.assertEqual(result["note"], "Stardew Valley game map")

    def test_missing_maps_say_why(self):
        result = load_route_map(self.document, self.project_file, "Town")
        self.assertIsNone(result["image"])
        self.assertIn("Connect your game", result["note"])
        self.place(map=None)
        result = load_route_map(self.document, self.project_file, "Bookshop")
        self.assertIsNone(result["image"])
        self.assertEqual(result["note"], "This place has no map to show yet.")
        with patch("pixelheart_core.scene_preview.load_game_map", side_effect=ValueError("Game asset Maps/Nowhere is unavailable.")):
            result = load_route_map(self.document, self.project_file, "Nowhere", game_root=self.root)
        self.assertIn("isn't in your game", result["note"])
        self.assertEqual(load_route_map(self.document, self.project_file, "")["note"], "Choose a place for this stop.")


if __name__ == "__main__":
    unittest.main()
