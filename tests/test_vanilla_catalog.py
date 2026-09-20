"""Offline rebuilds and the bundled catalog agree on ordinary vanilla gifts."""

import json
import unittest
from pathlib import Path

from pixelheart_core.catalog import validate_catalog
from scripts.build_vanilla_catalog import build_catalog


class VanillaCatalogTests(unittest.TestCase):
    def test_builder_excludes_non_gifts_without_broad_type_or_name_filters(self):
        excluded = {
            "30": "Lumber", "71": "Trimmed Lucky Purple Shorts", "73": "Golden Walnut",
            "102": "Lost Book", "326": "Dwarvish Translation Guide", "434": "Stardrop",
            "590": "Artifact Spot", "742": "Haley's Lost Bracelet", "803": "Iridium Milk",
            "858": "Qi Gem", "875": "Ectoplasm", "876": "Prismatic Jelly",
            "892": "Warp Totem: Qi's Arena", "GoldCoin": "Gold Coin", "SeedSpot": "Artifact Spot",
            "94": "Spirit Torch", "449": "Stone Base", "461": "Decorative Pot",
            "925": "Slime Crate", "927": "Camping Stove", "929": "Hedge",
            "930": "???", "PetLicense": "Pet License",
            "922": "SupplyCrate", "923": "SupplyCrate", "924": "SupplyCrate",
        }
        retained = {
            "791": "Golden Coconut", "Book_Horse": "Horse: The Book",
            "MysteryBox": "Mystery Box", "GoldenMysteryBox": "Golden Mystery Box",
            "StardropTea": "Stardrop Tea", "166": "Treasure Chest",
            "79": "Secret Note", "842": "Journal Scrap", "GoldenBobber": "Golden Bobber",
            "TroutDerbyTag": "Golden Tag", "ButterflyPowder": "Butterfly Powder",
            "808": "Void Ghost Pendant", "341": "Tea Set",
            "747": "Rotten Plant", "748": "Rotten Plant", "893": "Fireworks (Red)",
            "894": "Fireworks (Purple)", "895": "Fireworks (Green)",
            "Example.Heart": "???", "Example.Pet": "Pet License", "Example.Crate": "SupplyCrate",
        }
        labels = {**excluded, **retained}
        labels.update({f"Fixture_{index}": f"Gift {index}" for index in range(807 - len(labels))})
        objects = {
            "_meta": {"gameVersion": "1.6.15"},
            "objects": [
                {
                    "id": item_id, "canBeGivenAsGift": True, "contextTags": [],
                    "type": "Quest" if item_id == "791" else "Basic", "category": 0,
                    "spriteSheet": None, "spriteIndex": index,
                }
                for index, item_id in enumerate(labels)
            ],
        }
        names = [{"id": item_id, "names": {"data-en-US": name}} for item_id, name in labels.items()]
        catalog = validate_catalog(build_catalog(objects, names))
        items = {item["id"]: item for item in catalog["items"]}
        self.assertEqual(set(items), labels.keys() - excluded.keys())
        for item_id, name in retained.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(items[item_id]["name"], name)

    def test_bundled_catalog_excludes_non_gifts_and_retains_unusual_real_gifts(self):
        path = Path(__file__).resolve().parent.parent / "pixelheart_core/data/vanilla_items.json"
        catalog = validate_catalog(json.loads(path.read_text(encoding="utf-8")))
        ids = {item["id"] for item in catalog["items"]}
        excluded = {
            "30", "71", "73", "102", "326", "434", "590", "742", "803", "858",
            "875", "876", "892", "GoldCoin", "SeedSpot",
            "94", "449", "461", "925", "927", "929",
            "930", "PetLicense", "922", "923", "924",
            "191", "788", "789", "790", "864", "865", "866", "867", "868", "869", "870",
            "277", "458", "460", "809",
        }
        self.assertFalse(excluded & ids)
        self.assertTrue({
            "791", "275", "166", "MysteryBox", "GoldenMysteryBox", "Book_Horse", "StardropTea",
            "79", "842", "GoldenBobber", "TroutDerbyTag", "ButterflyPowder", "808", "341",
            "747", "748", "893", "894", "895",
        } <= ids)

    def test_wiki_icon_manifest_covers_only_the_bundled_catalog(self):
        data = Path(__file__).resolve().parent.parent / "pixelheart_core/data"
        catalog = json.loads((data / "vanilla_items.json").read_text(encoding="utf-8"))
        manifest = json.loads((data / "wiki_item_images.json").read_text(encoding="utf-8"))
        catalog_ids = {item["id"] for item in catalog["items"]}
        available = set(manifest["items"])
        unavailable = {item["id"] for item in manifest["unavailable"]}
        self.assertFalse(available & unavailable)
        self.assertEqual(available | unavailable, catalog_ids)


if __name__ == "__main__":
    unittest.main()
