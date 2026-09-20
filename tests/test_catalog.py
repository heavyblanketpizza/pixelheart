"""Catalog imports preserve game identity and fail without partial results."""

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pixelheart_core.catalog import CatalogValidationError, load_catalog, validate_catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "Data_Objects.json"

    def write(self, data):
        self.file.write_text(json.dumps(data), encoding="utf-8")
        return self.file

    def object(self, name="Coffee", **kwargs):
        return {"Name": name, "DisplayName": name, "Category": -7, **kwargs}

    def snapshot(self):
        return {
            "format": "pixelheart-item-catalog", "version": 1,
            "source": "vanilla", "label": "Stardew Valley 1.6.15",
            "game_version": "1.6.15",
            "items": [{"id": "395", "name": "Coffee", "category": -7, "category_name": "Cooking"}],
            "warnings": [],
        }

    def test_import_preserves_numeric_non_numeric_and_mod_ids_without_extra_data(self):
        source = {
            "395": self.object(Description="Do not retain this", Texture="secret/path"),
            "Book_Horse": self.object("Horse: The Book", Category=-102),
            "123author.Mod_Coffee-2": self.object("Mod Coffee", DisplayName="커피", Category=-1234),
            "_PrivateObject": self.object("Hidden Name", ContextTags=None),
        }
        result = load_catalog(self.write(source))
        self.assertEqual(result["source"], "content-patcher-export")
        self.assertEqual(result["label"], "Data_Objects.json")
        self.assertIsNotNone(datetime.fromisoformat(result["imported_at"]).utcoffset())
        self.assertEqual({item["id"] for item in result["items"]}, set(source))
        by_id = {item["id"]: item for item in result["items"]}
        self.assertEqual(by_id["123author.Mod_Coffee-2"]["name"], "커피")
        self.assertEqual(by_id["123author.Mod_Coffee-2"]["category_name"], "Category -1234")
        self.assertEqual(set(by_id["395"]), {"id", "name", "category", "category_name"})
        self.assertNotIn("Texture", json.dumps(result))
        self.assertIn("snapshot", result["warnings"][0])
        self.assertEqual(load_catalog(self.file), result)

    def test_explicit_gift_exclusions_and_default_true_are_respected(self):
        result = load_catalog(self.write({
            "A": self.object("A"),
            "B": self.object("B", CanBeGivenAsGift=False),
            "C": self.object("C", ContextTags=["color_red", "not_giftable"]),
            "D": self.object("D", CanBeGivenAsGift=True),
        }))
        self.assertEqual([item["id"] for item in result["items"]], ["A", "D"])
        self.assertTrue(any("Excluded 2" in item for item in result["warnings"]))

    def test_unresolved_token_names_use_internal_names(self):
        result = load_catalog(self.write({
            "A": self.object("Horse Book", DisplayName="[LocalizedText Strings/Objects:Book_Horse_Name]"),
            "B": self.object("Custom Coffee", DisplayName="{{i18n:coffee}}"),
        }))
        self.assertEqual([item["name"] for item in result["items"]], ["Custom Coffee", "Horse Book"])
        self.assertTrue(any("internal names for 2" in warning for warning in result["warnings"]))

    def test_null_display_name_falls_back_to_valid_internal_name_with_warning(self):
        result = load_catalog(self.write({
            "Example.Mod_Coffee": self.object("Custom Coffee", DisplayName=None),
        }))
        self.assertEqual(result["items"][0]["name"], "Custom Coffee")
        self.assertEqual(result["items"][0]["id"], "Example.Mod_Coffee")
        self.assertTrue(any("internal names for 1" in warning and "unavailable" in warning for warning in result["warnings"]))
        with self.assertRaises(CatalogValidationError):
            load_catalog(self.write({"Example.Mod_Coffee": self.object(Name=None, DisplayName=None)}))

    def test_sprite_metadata_is_factual_normalized_and_optional(self):
        result = load_catalog(self.write({
            "395": self.object(SpriteIndex=395, Texture=None),
            "Mod_Coffee": self.object(SpriteIndex=7, Texture="Mods\\Example.Coffee\\Items"),
            "Legacy": self.object(),
        }))
        items = {item["id"]: item for item in result["items"]}
        self.assertEqual(items["395"]["icon"], {"texture": "Maps/springobjects", "index": 395})
        self.assertEqual(items["Mod_Coffee"]["icon"], {"texture": "Mods/Example.Coffee/Items", "index": 7})
        self.assertNotIn("icon", items["Legacy"])
        self.assertEqual(validate_catalog(result), result)

    def test_unsafe_or_malformed_sprite_metadata_rejects_import_atomically(self):
        for texture in ("../secret", "/absolute", "C:\\secret", "a//b", "a/../b", 'a"b', "a:b", "", False):
            with self.subTest(texture=texture), self.assertRaises(CatalogValidationError):
                load_catalog(self.write({"395": self.object(SpriteIndex=0, Texture=texture)}))
        for index in (-1, 1.5, True, None, 1_000_001):
            with self.subTest(index=index), self.assertRaises(CatalogValidationError):
                load_catalog(self.write({"395": self.object(SpriteIndex=index)}))
        for icon in (None, {}, {"texture": "Maps/springobjects", "index": 0, "path": "/private"}):
            snapshot = self.snapshot()
            snapshot["items"][0]["icon"] = icon
            with self.subTest(icon=icon), self.assertRaises(CatalogValidationError):
                validate_catalog(snapshot)

    def test_ordinary_gift_catalog_excludes_equipment_world_debris_and_special_actions(self):
        result = load_catalog(self.write({
            "Ring": self.object(Type="Ring"),
            "TagRing": self.object(ContextTags=["item_type_ring"]),
            "StoneDebris": self.object(Category=-999),
            "277": self.object("Wilted Bouquet"),
            "458": self.object("Bouquet"),
            "460": self.object("Mermaid's Pendant"),
            "809": self.object("Movie Ticket"),
            # The Type field isn't the per-instance questItem flag.
            "791": self.object("Golden Coconut", Type="Quest"),
            "Book_Horse": self.object("Horse Book", Category=-102),
        }))
        self.assertEqual({item["id"] for item in result["items"]}, {"791", "Book_Horse"})

    def test_import_accepts_utf8_bom(self):
        self.file.write_text(json.dumps({"395": self.object()}), encoding="utf-8-sig")
        self.assertEqual(load_catalog(self.file)["items"][0]["id"], "395")

    def test_import_preserves_eligible_repurposed_vanilla_quest_ids(self):
        quest_ids = {"191", "788", "789", "790", "864", "865", "866", "867", "868", "869", "870"}
        source = {item_id: self.object("Repurposed item " + item_id, Type="Quest", CanBeGivenAsGift=True)
                  for item_id in quest_ids}
        result = load_catalog(self.write(source))
        self.assertEqual({item["id"] for item in result["items"]}, quest_ids)

    def test_import_preserves_mod_replacements_for_curated_vanilla_exclusions(self):
        repurposed_ids = {
            "30", "71", "73", "102", "326", "434", "590", "742", "803", "858",
            "875", "876", "892", "922", "923", "924", "930", "GoldCoin", "PetLicense", "SeedSpot",
            "94", "449", "461", "925", "927", "929",
        }
        source = {
            item_id: self.object("Custom replacement " + item_id, CanBeGivenAsGift=True)
            for item_id in repurposed_ids
        }
        source["Example.Mod_Parcel"] = self.object("SupplyCrate", CanBeGivenAsGift=True)
        result = load_catalog(self.write(source))
        self.assertEqual({item["id"] for item in result["items"]}, set(source))

    def test_no_partial_import_when_any_record_is_malformed_including_excluded_records(self):
        malformed = [None, [], "Coffee/395", {}, {"Name": "Invalid"}, self.object(Category=True),
                     self.object(Category="-7"), self.object(Category=None), self.object(Category=2 ** 32),
                     self.object(Name=None), self.object(DisplayName=4), self.object(Type=4), self.object(Type=None),
                     self.object(CanBeGivenAsGift=None), self.object(CanBeGivenAsGift="false"),
                     self.object(ContextTags="not_giftable"), self.object(ContextTags=[None]),
                     self.object(CanBeGivenAsGift=False, Name=None)]
        for record in malformed:
            with self.subTest(record=record):
                with self.assertRaises(CatalogValidationError):
                    load_catalog(self.write({"395": self.object(), "Invalid": record}))

    def test_wrong_shapes_unsupported_formats_and_empty_candidates_are_rejected(self):
        for value in (None, [], "hello", {}, self.snapshot(),
                      {"Changes": []}, {"Data/Objects": {"395": self.object()}},
                      {"395": self.object(CanBeGivenAsGift=False)}):
            with self.subTest(value=value):
                with self.assertRaises(CatalogValidationError):
                    load_catalog(self.write(value))

    def test_invalid_ids_are_rejected_without_normalizing(self):
        for item_id in ("", "(O)395", " coffee", "coffee ", "-4", "a/b", "a:b", "a b", "{{ModId}}_A", "é", "x" * 101):
            with self.subTest(item_id=item_id):
                with self.assertRaisesRegex(CatalogValidationError, "Item IDs"):
                    load_catalog(self.write({item_id: self.object()}))
        self.assertEqual(load_catalog(self.write({"000395": self.object()}))["items"][0]["id"], "000395")

    def test_duplicate_json_keys_are_rejected_at_every_level(self):
        for raw in ('{"395":{"Name":"A","Category":0},"395":{"Name":"B","Category":0}}',
                    '{"395":{"Name":"A","Name":"B","Category":0}}',
                    '{"395":{"Name":"A","Category":0,"CustomFields":{"a":1,"a":2}}}'):
            self.file.write_text(raw)
            with self.assertRaisesRegex(CatalogValidationError, "repeats"):
                load_catalog(self.file)

    def test_corrupt_json_and_invalid_unicode_are_rejected_even_in_ignored_fields(self):
        for raw in (b"{no", b"\xff", b'{"395":{"Name":"A","Category":NaN}}'):
            self.file.write_bytes(raw)
            with self.assertRaises(CatalogValidationError):
                load_catalog(self.file)
        for value in ("\ud800", "\x00", {"\udfff": None}, float("inf")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(CatalogValidationError):
                    load_catalog(self.write({"395": self.object(CustomFields=value)}))

    def test_depth_size_and_item_limits_are_bounded(self):
        nested = None
        for _ in range(70):
            nested = [nested]
        with self.assertRaisesRegex(CatalogValidationError, "nested"):
            load_catalog(self.write({"395": self.object(CustomFields=nested)}))
        self.write({"395": self.object()})
        with patch("pixelheart_core.catalog.MAX_CATALOG_BYTES", 10):
            with self.assertRaisesRegex(CatalogValidationError, "32 MiB"):
                load_catalog(self.file)
        with patch("pixelheart_core.catalog.MAX_CATALOG_ITEMS", 1):
            with self.assertRaises(CatalogValidationError):
                load_catalog(self.write({"A": self.object(), "B": self.object()}))

    def test_missing_paths_and_directories_have_catalog_errors(self):
        with self.assertRaisesRegex(CatalogValidationError, "Could not read"):
            load_catalog(self.file)
        with self.assertRaisesRegex(CatalogValidationError, "exported"):
            load_catalog(self.file.parent)

    def test_snapshot_is_a_deep_copy_and_optional_vanilla_timestamp_is_not_required(self):
        source = self.snapshot()
        before = copy.deepcopy(source)
        result = validate_catalog(source)
        result["items"][0]["name"] = "Changed"
        result["warnings"].append("New warning")
        self.assertEqual(source, before)
        self.assertNotIn("imported_at", result)

    def test_local_snapshot_requires_timestamp_and_valid_source(self):
        for value in (None, "today", "2026-01-01T00:00:00", 2026, "2026-99-99T00:00:00Z"):
            with self.subTest(value=value):
                catalog = {**self.snapshot(), "source": "content-patcher-export", "imported_at": value}
                with self.assertRaises(CatalogValidationError):
                    validate_catalog(catalog)
        for value in (None, {}, "other"):
            with self.subTest(source=value):
                with self.assertRaises(CatalogValidationError):
                    validate_catalog({**self.snapshot(), "source": value})

    def test_snapshot_rejects_malformed_items_duplicate_ids_and_unbounded_metadata(self):
        for key, value in (("format", "bad"), ("version", True), ("version", 2), ("label", "x" * 161),
                           ("label", "\ud800"), ("items", []), ("warnings", None),
                           ("warnings", ["x"] * 21), ("warnings", ["x" * 1001]), ("unexpected", {})):
            with self.subTest(key=key, value=repr(value)[:80]):
                with self.assertRaises(CatalogValidationError):
                    validate_catalog({**self.snapshot(), key: value})
        for item in (None, {}, {**self.snapshot()["items"][0], "Description": "No"},
                     {**self.snapshot()["items"][0], "id": "(O)395"},
                     {**self.snapshot()["items"][0], "name": "\x00"},
                     {**self.snapshot()["items"][0], "category": False}):
            with self.subTest(item=item):
                with self.assertRaises(CatalogValidationError):
                    validate_catalog({**self.snapshot(), "items": [item]})
        catalog = self.snapshot()
        catalog["items"] *= 2
        with self.assertRaisesRegex(CatalogValidationError, "repeats"):
            validate_catalog(catalog)


if __name__ == "__main__":
    unittest.main()
