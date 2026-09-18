"""Checks for portable, isolated gift-preset authoring data."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from pixelheart_core.gift_presets import (
    GIFT_PRESETS,
    RECOMMENDED_GIFT_PRESET_ID,
    TASTES,
    GiftPreset,
    get_gift_preset,
)


class GiftPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent.parent / "pixelheart_core" / "data" / "vanilla_items.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        cls.items = {item["id"]: item for item in catalog["items"]}

    def test_recommended_profile_is_first_and_every_profile_has_a_unique_id(self):
        self.assertEqual(GIFT_PRESETS[0].id, RECOMMENDED_GIFT_PRESET_ID)
        self.assertEqual(
            [preset.id for preset in GIFT_PRESETS],
            ["everyday", "botanist", "baker", "angler", "miner", "artist"],
        )
        self.assertEqual(len({preset.id for preset in GIFT_PRESETS}), len(GIFT_PRESETS))

    def test_profiles_have_exclusive_tastes_using_only_bundled_gift_items(self):
        for preset in GIFT_PRESETS:
            with self.subTest(preset=preset.id):
                self.assertTrue(preset.name.strip())
                self.assertTrue(preset.description.strip())
                self.assertEqual(tuple(preset.gifts), TASTES)
                self.assertTrue(all(preset.gifts.values()))
                all_ids = [item_id for values in preset.gifts.values() for item_id in values]
                self.assertEqual(len(all_ids), len(set(all_ids)))
                self.assertTrue(set(all_ids) <= self.items.keys())
                self.assertTrue(all(isinstance(item_id, str) for item_id in all_ids))

    def test_everyday_profile_has_the_expected_familiar_favorites(self):
        preset = get_gift_preset(RECOMMENDED_GIFT_PRESET_ID)
        self.assertEqual(
            [self.items[item_id]["name"] for item_id in preset.gifts["love"]],
            ["Coffee", "Pink Cake", "Diamond"],
        )

    def test_shared_profiles_and_assignments_cannot_be_mutated(self):
        preset = get_gift_preset("everyday")
        with self.assertRaises(FrozenInstanceError):
            preset.name = "Changed"
        with self.assertRaises(TypeError):
            preset.gifts["love"] = ("66",)
        with self.assertRaises(TypeError):
            preset.gifts["love"][0] = "66"

    def test_editable_copy_is_independent_between_projects_and_presets(self):
        preset = get_gift_preset("everyday")
        first = preset.as_gifts()
        second = preset.as_gifts()
        first["love"].clear()
        first["hate"].append("66")
        first["like"] = []
        self.assertEqual(second, preset.as_gifts())
        self.assertEqual(second["love"], ["395", "221", "72"])
        self.assertNotIn("66", second["hate"])
        self.assertTrue(second["like"])

    def test_constructor_detaches_mutable_input_assignments(self):
        values = {taste: [str(index)] for index, taste in enumerate(TASTES)}
        preset = GiftPreset("custom", "Custom", "A test profile.", values)
        values["love"].append("55")
        values["like"] = ["66"]
        self.assertEqual(preset.gifts["love"], ("0",))
        self.assertEqual(preset.gifts["like"], ("1",))

    def test_invalid_profile_shapes_and_duplicate_assignments_are_rejected(self):
        with self.assertRaises(ValueError):
            GiftPreset("custom", "Custom", "A test profile.", {"love": ("66",)})
        with self.assertRaises(ValueError):
            GiftPreset("custom", "Custom", "A test profile.", {
                "love": ("66",), "like": ("66",), "dislike": (), "hate": (),
            })

    def test_unknown_preset_does_not_silently_apply_a_different_profile(self):
        for preset_id in ("missing", "", None):
            with self.subTest(preset_id=preset_id), self.assertRaises(ValueError):
                get_gift_preset(preset_id)


if __name__ == "__main__":
    unittest.main()
