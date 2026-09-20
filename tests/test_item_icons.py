"""Sprite pointers and local cache behavior without redistributing game art."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from pixelheart.item_icons import (
    IconImportError, ItemIconStore, _read_texture, default_wiki_cache_dir, export_filename, texture_commands,
)
from pixelheart_core.catalog import validate_catalog
from pixelheart_core.wiki_items import WikiItemError


class ItemIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exports = self.root / "export"
        self.exports.mkdir()
        self.cache = self.root / "cache"
        self.wiki_cache = self.root / "wiki"
        self.store = ItemIconStore(self.cache, self.wiki_cache)
        self.record = {"id": "Mod_Coffee", "name": "Mod Coffee", "category": -7,
                       "icon": {"texture": "Mods/Example/Items", "index": 1}}

    def texture(self, texture="Mods/Example/Items", size=(32, 16), colors=("red", "blue")):
        image = QImage(*size, QImage.Format.Format_ARGB32)
        image.fill(QColor(colors[0]))
        for x in range(16, size[0]):
            for y in range(size[1]):
                image.setPixelColor(x, y, QColor(colors[-1]))
        path = self.exports / export_filename(texture)
        self.assertTrue(image.save(str(path), "PNG"))
        return path

    def color(self):
        return self.store.icon(self.record, 32).pixmap(32, 32).toImage().pixelColor(16, 16).name()

    def test_real_atlas_tile_is_cropped_using_metadata_and_persists_after_source_removed(self):
        path = self.texture()
        result = self.store.import_folder(self.exports, [self.record])
        self.assertEqual((result.imported_textures, result.ready_items), (1, 1))
        self.assertEqual(result.missing_textures, ())
        self.assertEqual(self.color(), "#0000ff")
        path.unlink()
        self.store = ItemIconStore(self.cache, self.wiki_cache)
        self.assertTrue(self.store.has_icon(self.record))
        self.assertEqual(self.color(), "#0000ff")

    def test_import_refreshes_cached_icons_and_missing_folder_preserves_old_ones(self):
        self.texture()
        self.store.import_folder(self.exports, [self.record])
        self.assertEqual(self.color(), "#0000ff")
        path = self.texture(colors=("red", "green"))
        self.store.import_folder(self.exports, [self.record])
        self.assertEqual(self.color(), "#008000")
        path.unlink()
        result = self.store.import_folder(self.exports, [self.record])
        self.assertEqual((result.imported_textures, result.ready_items), (0, 1))
        self.assertEqual(result.missing_textures, ("Mods/Example/Items",))
        self.assertEqual(self.color(), "#008000")

    def test_small_sprite_cache_avoids_redecoding_interleaved_mod_atlases(self):
        records = []
        for atlas in range(8):
            self.texture(f"Mods/Example/Atlas{atlas}", size=(64, 16))
        for index in range(4):
            for atlas in range(8):
                texture = f"Mods/Example/Atlas{atlas}"
                records.append({**self.record, "id": f"Mod_{atlas}_{index}", "icon": {"texture": texture, "index": index}})
        self.store.import_folder(self.exports, records)
        self.store = ItemIconStore(self.cache, self.wiki_cache)
        with patch("pixelheart.item_icons._read_texture", wraps=_read_texture) as read:
            self.store.prepare(records)
            self.assertTrue(all(self.store.has_icon(record) for record in records))
            self.assertEqual(read.call_count, 8)
            for _ in range(3):
                self.assertTrue(all(self.store.has_icon(record) for record in records))
                self.assertTrue(all(not self.store.icon(record).isNull() for record in records))
            self.assertEqual(read.call_count, 8)
        self.assertEqual(len(self.store._sprites), 32)

    def test_missing_sprite_cache_is_bounded_and_invalidated_by_import(self):
        records = [{**self.record, "icon": {"texture": f"Mods/Atlas{index}", "index": 0}}
                   for index in range(8)]
        with patch.object(self.store, "_texture", wraps=self.store._texture) as get_texture:
            self.assertFalse(any(self.store.has_icon(record) for record in records))
            self.assertFalse(any(self.store.has_icon(record) for record in records))
            self.assertEqual(get_texture.call_count, 8)
        self.texture("Mods/Atlas0")
        self.store.import_folder(self.exports, records)
        self.assertTrue(self.store.has_icon(records[0]))
        self.assertFalse(self.store.has_icon(records[1]))
        with patch("pixelheart.item_icons.MAX_CATALOG_ITEMS", 3):
            for record in records:
                self.store.has_icon(record)
            # Existing entries are trimmed when a new crop is inserted.
            self.store.has_icon({**self.record, "icon": {"texture": "Mods/New", "index": 0}})
            self.assertLessEqual(len(self.store._sprites), 3)
    def test_fallback_is_deterministic_nonnull_cached_and_does_not_write(self):
        first = self.store.icon(self.record).pixmap(48).toImage()
        second = ItemIconStore(self.cache, self.wiki_cache).icon(self.record).pixmap(48).toImage()
        self.assertFalse(first.isNull())
        self.assertEqual(first, second)
        self.assertFalse(self.store.has_icon(self.record))
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.store.icon(self.record).cacheKey(), self.store.icon(self.record).cacheKey())

    def wiki_record(self):
        self.store._wiki_sources = {"395": {"url": "https://stardewvalleywiki.com/mediawiki/images/icon.png"}}
        return {"id": "395", "name": "Coffee", "category": -7,
                "icon": {"texture": "Maps/springobjects", "index": 395}}

    def wiki_image(self, color="red"):
        self.wiki_cache.mkdir(exist_ok=True)
        path = self.wiki_cache / "synthetic.png"
        image = QImage(48, 48, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        # A distinct corner proves the 48px image is not cropped to 16px.
        image.setPixelColor(47, 47, QColor("blue"))
        self.assertTrue(image.save(str(path), "PNG"))
        return path

    def test_wiki_icon_is_complete_image_and_local_texture_takes_precedence(self):
        record = self.wiki_record()
        path = self.wiki_image()
        with patch("pixelheart.item_icons.cached_item_icon", return_value=path):
            self.assertFalse(self.store.has_local_icon(record))
            self.assertTrue(self.store.has_wiki_icon(record))
            self.assertTrue(self.store.has_icon(record))
            image = self.store.icon(record, 48).pixmap(48, 48).toImage()
            self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")
            self.assertEqual(image.pixelColor(47, 47).name(), "#0000ff")
            self.texture("Maps/springobjects", size=(256, 400), colors=("green", "green"))
            self.store.import_folder(self.exports, [record])
            self.assertTrue(self.store.has_local_icon(record))
            self.assertEqual(self.store.icon(record, 48).pixmap(48).toImage().pixelColor(20, 20).name(), "#008000")

    def test_wiki_fallback_rejects_unknown_ids_and_changed_mod_icon_metadata(self):
        record = self.wiki_record()
        path = self.wiki_image()
        altered_records = [
            {**record, "id": "Mod_Coffee"},
            {**record, "icon": {"texture": "Mods/Custom/Coffee", "index": 395}},
            {**record, "icon": {"texture": "Maps/springobjects", "index": 396}},
            {**record, "icon": {"texture": "../invalid", "index": 395}},
        ]
        with patch("pixelheart.item_icons.cached_item_icon", return_value=path) as cached:
            for altered in altered_records:
                self.assertFalse(self.store.has_wiki_icon(altered))
            cached.assert_not_called()
            # Old project metadata may have only a vanilla ID and name.
            self.assertTrue(self.store.has_wiki_icon({"id": "395", "name": "Coffee"}))

    def test_wiki_cache_refresh_updates_missing_and_previously_rendered_icons(self):
        record = self.wiki_record()
        with patch("pixelheart.item_icons.cached_item_icon", return_value=None):
            self.assertFalse(self.store.has_icon(record))
            placeholder = self.store.icon(record).cacheKey()
        path = self.wiki_image()
        with patch("pixelheart.item_icons.cached_item_icon", return_value=path):
            self.assertFalse(self.store.has_icon(record))
            self.store.invalidate_wiki_icons(["395"])
            self.assertTrue(self.store.has_icon(record))
            self.assertNotEqual(self.store.icon(record).cacheKey(), placeholder)
            self.assertEqual(self.store.icon(record).pixmap(48).toImage().pixelColor(20, 20).name(), "#ff0000")
            self.wiki_image("green")
            self.store.invalidate_wiki_icons()
            self.assertEqual(self.store.icon(record).pixmap(48).toImage().pixelColor(20, 20).name(), "#008000")

    def test_missing_wiki_ids_excludes_local_cached_unknown_and_modded_records(self):
        record = self.wiki_record()
        with patch("pixelheart.item_icons.cached_item_icon", return_value=None):
            self.assertEqual(self.store.missing_wiki_ids([record, record, self.record]), ["395"])
        self.store.invalidate_wiki_icons()
        with patch("pixelheart.item_icons.cached_item_icon", return_value=self.wiki_image()):
            self.assertEqual(self.store.missing_wiki_ids([record]), [])
        self.store.invalidate_wiki_icons()
        self.texture("Maps/springobjects", size=(256, 400), colors=("green", "green"))
        self.store.import_folder(self.exports, [record])
        with patch("pixelheart.item_icons.cached_item_icon", return_value=None):
            self.assertEqual(self.store.missing_wiki_ids([record]), [])

    def test_unavailable_wiki_cache_keeps_placeholder_and_does_not_create_files(self):
        record = self.wiki_record()
        with patch("pixelheart.item_icons.cached_item_icon", side_effect=WikiItemError("unavailable")):
            self.assertFalse(self.store.has_icon(record))
            self.assertFalse(self.store.icon(record).isNull())
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.wiki_cache.exists())

    def test_default_wiki_cache_is_stable_user_cache_and_constructor_does_not_write(self):
        with patch("pixelheart.item_icons.QStandardPaths.writableLocation", return_value=str(self.root)):
            expected = self.root / "Pixelheart" / "wiki-items"
            self.assertEqual(default_wiki_cache_dir(), expected)
            self.assertEqual(ItemIconStore(self.cache).wiki_cache_dir, expected)
            self.assertFalse(expected.exists())

    def test_out_of_range_index_gets_placeholder_and_warning(self):
        self.texture()
        self.record["icon"]["index"] = 2
        result = self.store.import_folder(self.exports, [self.record])
        self.assertEqual(result.ready_items, 0)
        self.assertFalse(self.store.has_icon(self.record))
        self.assertTrue(any("outside" in warning for warning in result.warnings))

    def test_corrupt_oversized_or_wrong_grid_texture_is_skipped(self):
        path = self.exports / export_filename(self.record["icon"]["texture"])
        path.write_text("not a PNG")
        result = self.store.import_folder(self.exports, [self.record])
        self.assertEqual(result.imported_textures, 0)
        self.assertTrue(result.warnings)
        self.texture(size=(31, 16))
        self.assertEqual(self.store.import_folder(self.exports, [self.record]).imported_textures, 0)
        self.texture()
        with patch("pixelheart.item_icons.MAX_TEXTURE_BYTES", 10):
            self.assertEqual(self.store.import_folder(self.exports, [self.record]).imported_textures, 0)
        with patch("pixelheart.item_icons.MAX_TEXTURE_PIXELS", 32):
            self.assertEqual(self.store.import_folder(self.exports, [self.record]).imported_textures, 0)

    def test_symlinks_cannot_read_beyond_selected_directory(self):
        path = self.texture()
        outside = self.root / "outside.png"
        path.rename(outside)
        path.symlink_to(outside)
        result = self.store.import_folder(self.exports, [self.record])
        self.assertEqual(result.imported_textures, 0)
        self.assertTrue(any("outside" in warning for warning in result.warnings))

    def test_colliding_flattened_paths_are_not_confused(self):
        self.texture("Mods/Example/Items")
        other = {**self.record, "id": "Other", "icon": {"texture": "Mods_Example/Items", "index": 0}}
        result = self.store.import_folder(self.exports, [self.record, other])
        self.assertEqual(result.imported_textures, 0)
        self.assertEqual(len(result.missing_textures), 2)

    def test_commands_are_deduplicated_explicit_image_exports_and_safe(self):
        self.assertEqual(texture_commands([self.record, self.record]), ['patch export "Mods/Example/Items" image'])
        self.assertEqual(export_filename("Maps\\springobjects"), "Maps_springobjects.png")
        for texture in ('../escape', 'bad";command', '/absolute'):
            with self.assertRaises(ValueError):
                export_filename(texture)
        self.assertEqual(texture_commands([{"icon": {"texture": "../bad", "index": 0}}]), [])

    def test_missing_folder_and_cache_failure_are_explicit_errors(self):
        with self.assertRaises(IconImportError):
            self.store.import_folder(self.root / "missing", [self.record])
        self.texture()
        self.cache.write_text("not a directory")
        with self.assertRaises(IconImportError):
            self.store.import_folder(self.exports, [self.record])

    def test_bundled_metadata_uses_exact_indexes_including_nonnumeric_ids(self):
        path = Path(__file__).parents[1] / "pixelheart_core/data/vanilla_items.json"
        catalog = validate_catalog(json.loads(path.read_text()))
        by_id = {record["id"]: record for record in catalog["items"]}
        self.assertEqual(len(by_id), 637)
        self.assertFalse({"191", "788", "789", "790", "864", "865", "866", "867", "868", "869", "870"} & by_id.keys())
        self.assertTrue(all("icon" in record for record in by_id.values()))
        # The Quest type alone does not make a Golden Coconut ineligible.
        self.assertEqual(by_id["791"]["name"], "Golden Coconut")
        self.assertEqual(by_id["791"]["icon"], {"texture": "Maps/springobjects", "index": 791})
        self.assertEqual(by_id["Book_Horse"]["icon"], {"texture": "TileSheets/Objects_2", "index": 141})
        self.assertEqual(by_id["395"]["icon"], {"texture": "Maps/springobjects", "index": 395})
        self.assertEqual(texture_commands(catalog["items"]), [
            'patch export "Maps/springobjects" image', 'patch export "TileSheets/Objects_2" image',
        ])


if __name__ == "__main__":
    unittest.main()
