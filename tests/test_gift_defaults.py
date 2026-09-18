"""Vanilla baselines preserve game exceptions without becoming assignments."""

import json
import unittest
from collections import Counter
from pathlib import Path

from pixelheart_core.gift_defaults import vanilla_gift_tastes


class VanillaGiftDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tastes = vanilla_gift_tastes()

    def test_complete_bundled_scope_except_special_stardrop_tea(self):
        path = Path(__file__).resolve().parent.parent / "pixelheart_core/data/vanilla_items.json"
        ids = {item["id"] for item in json.loads(path.read_text())["items"]}
        self.assertEqual(set(self.tastes), ids - {"StardropTea"})
        self.assertNotIn("StardropTea", self.tastes)
        self.assertNotIn("Example.ModObject", self.tastes)
        self.assertNotIn("(O)74", self.tastes)
        self.assertEqual(Counter(self.tastes.values()), {
            "love": 5, "like": 151, "dislike": 309, "hate": 115, "neutral": 82,
        })

    def test_universal_loves_and_specific_category_exceptions(self):
        self.assertEqual({key for key, value in self.tastes.items() if value == "love"},
                         {"74", "446", "797", "373", "279"})
        expected = {
            "24": "like",       # Parsnip: vegetable
            "591": "like",      # Tulip: flower
            "376": "hate",      # Poppy overrides the flower category
            "194": "neutral",   # Fried Egg overrides cooking
            "216": "neutral",   # Bread overrides cooking
            "262": "neutral",   # Wheat overrides vegetables
            "304": "neutral",   # Hops overrides vegetables
            "815": "neutral",   # Tea Leaves overrides vegetables
            "203": "hate",      # Strange Bun overrides cooking
            "265": "hate",      # Seafoam Pudding overrides cooking
            "169": "dislike",   # Driftwood overrides trash
            "769": "dislike",   # Void Essence follows monster-loot category
            "766": "hate",      # Slime overrides monster-loot category
        }
        for item_id, taste in expected.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], taste)

    def test_category_zero_is_hated_before_explicit_item_overrides(self):
        expected = {
            "245": "hate", "SeaJelly": "hate", "791": "hate",
            "688": "hate", "874": "hate",  # Warp Totem: Farm, Bug Steak
            "395": "like", "166": "like",  # Coffee, Treasure Chest
            "246": "dislike", "535": "dislike",  # Flour, Geode
            "373": "love", "797": "love",  # Golden Pumpkin, Pearl
        }
        for item_id, taste in expected.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], taste)

    def test_artifacts_override_even_specific_universal_hates(self):
        for item_id in ("96", "100", "105", "110", "111", "112", "579", "589"):
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], "dislike")
        # Ginger Island fossils have Basic type, not Arch.
        self.assertEqual(self.tastes["820"], "hate")

    def test_price_and_edibility_fallbacks_and_ordinary_neutrals(self):
        self.assertEqual(self.tastes["283"], "hate")  # Holly is poisonous
        for item_id in ("296", "399", "889"):
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], "dislike")
        for item_id in ("16", "18", "176", "184", "410", "440"):
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], "neutral")

    def test_books_remain_neutral_except_price_catalogue(self):
        for item_id in ("PurpleBook", "SkillBook_0", "SkillBook_4", "Book_Horse", "Book_Void"):
            with self.subTest(item_id=item_id):
                self.assertEqual(self.tastes[item_id], "neutral")
        self.assertEqual(self.tastes["Book_PriceCatalogue"], "dislike")

    def test_caller_edits_cannot_mutate_shared_baseline(self):
        self.tastes["74"] = "hate"
        del self.tastes["24"]
        self.tastes["Example.ModObject"] = "love"
        fresh = vanilla_gift_tastes()
        self.assertEqual(fresh["74"], "love")
        self.assertEqual(fresh["24"], "like")
        self.assertNotIn("Example.ModObject", fresh)


if __name__ == "__main__":
    unittest.main()
