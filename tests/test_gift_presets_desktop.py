"""Gift starters remain opt-in, additive, undoable, and portable."""

import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.gifts_page import GiftsPage, TASTES
from pixelheart.item_icons import ItemIconStore
from pixelheart_core.gift_presets import (
    GIFT_PRESETS, RECOMMENDED_GIFT_PRESET_ID, get_gift_preset,
)
from pixelheart_core.projects import load_project


class GiftPresetDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-gift-presets-")
        self.root = Path(self.temporary.name)
        self.widgets = []
        self.store = ItemIconStore(self.root / "textures", self.root / "wiki-icons")
        self.icon_patch = patch("pixelheart.gifts_page.ItemIconStore", return_value=self.store)
        self.icon_patch.start()
        self.page = self.keep(GiftsPage())
        self.page.resize(1100, 800)
        self.page.show()
        self.application.processEvents()

    def tearDown(self):
        for widget in reversed(self.widgets):
            if isinstance(widget, MainWindow):
                widget.dirty = False
            widget.close()
            widget.deleteLater()
        self.application.processEvents()
        self.icon_patch.stop()
        self.temporary.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def select_preset(self, preset_id, page=None):
        page = page or self.page
        index = page.preset_picker.findData(preset_id)
        self.assertGreaterEqual(index, 0, f"Missing gift preset {preset_id}")
        page.preset_picker.setCurrentIndex(index)

    @staticmethod
    def expected(preset):
        return {taste: ["(O)" + item_id for item_id in preset.gifts[taste]] for taste in TASTES}

    def restricted_catalog(self, item_ids):
        snapshot = deepcopy(self.page.base_catalog)
        snapshot["items"] = [record for record in snapshot["items"] if record["id"] in item_ids]
        snapshot["label"] = "A smaller game catalog"
        return snapshot

    def test_fresh_project_and_browsing_starters_stay_clean_until_apply(self):
        window = self.keep(MainWindow())
        window.navigation.setCurrentRow(3)
        page = window.gifts
        before = deepcopy(window.document)
        changed = QSignalSpy(page.changed)
        self.assertEqual(page.preset_picker.currentData(), RECOMMENDED_GIFT_PRESET_ID)
        self.assertTrue(page.apply_preset_button.isEnabled())
        self.assertFalse(page.undo_preset_button.isEnabled())
        page.preset_preview_button.click()
        self.assertFalse(page.preset_preview.isHidden())
        for preset in GIFT_PRESETS:
            self.select_preset(preset.id, page)
            self.assertEqual(page.preset_description.text(), preset.description)
            for taste in TASTES:
                for item_id in preset.gifts[taste]:
                    self.assertIn(page.items[item_id]["name"], page.preset_preview_labels[taste].text())
        page.preset_preview_button.click()
        self.assertTrue(page.preset_preview.isHidden())
        page.search.setText("Coffee")
        page.unassigned_only.setChecked(True)
        self.assertEqual(page.dump(), {taste: [] for taste in TASTES})
        self.assertEqual(changed.count(), 0)
        self.assertFalse(window.dirty)
        self.assertEqual(window.document, before)
        self.assertFalse((self.root / "textures").exists())
        self.assertFalse((self.root / "wiki-icons").exists())

    def test_each_starter_applies_qualified_catalog_ids_once(self):
        for preset in GIFT_PRESETS:
            with self.subTest(preset=preset.id):
                self.page.load({taste: [] for taste in TASTES})
                self.select_preset(preset.id)
                changed = QSignalSpy(self.page.changed)
                QTest.mouseClick(self.page.apply_preset_button, Qt.MouseButton.LeftButton)
                self.assertEqual(self.page.dump(), self.expected(preset))
                all_values = [value for taste in TASTES for value in self.page.dump()[taste]]
                self.assertEqual(len(all_values), len(set(all_values)))
                self.assertTrue(all(value.startswith("(O)") and value[3:] in self.page.items for value in all_values))
                self.assertEqual(changed.count(), 1)
                self.assertTrue(self.page.undo_preset_button.isEnabled())
                self.page.apply_preset()
                self.assertEqual(changed.count(), 1)
                self.assertEqual(self.page.dump(), self.expected(preset))
                self.assertTrue(self.page.undo_preset_button.isEnabled())
                self.page.undo_preset()
                self.assertEqual(changed.count(), 2)
                self.assertEqual(self.page.dump(), {taste: [] for taste in TASTES})

    def test_apply_keeps_existing_tastes_mod_references_and_extension_metadata(self):
        metadata = {"note": "The mod gift is intentional", "nested": [1, {"keep": True}]}
        self.page.load({"love": ["id:Example.Mod_Flower"], "like": [],
                        "dislike": [], "hate": ["Coffee"], "extension": metadata})
        original = deepcopy(self.page.original)
        changed = QSignalSpy(self.page.changed)
        self.page.apply_preset()
        result = self.page.dump()
        self.assertIn("id:Example.Mod_Flower", result["love"])
        self.assertIn("(O)395", result["hate"])
        self.assertIn("Coffee", self.page.assignments["hate"])
        self.assertNotIn("(O)395", result["love"])
        self.assertEqual(sum("(O)395" in result[taste] for taste in TASTES), 1)
        self.assertEqual(result["extension"], metadata)
        self.assertEqual(self.page.original, original)
        self.assertEqual(changed.count(), 1)

    def test_partial_catalog_adds_available_items_and_explains_skips(self):
        self.page.load_catalog_snapshot(self.restricted_catalog({"395"}))
        changed = QSignalSpy(self.page.changed)
        self.page.apply_preset()
        self.assertEqual(self.page.dump(), {"love": ["(O)395"], "like": [], "dislike": [], "hate": []})
        self.assertEqual(changed.count(), 1)
        self.assertIn("Skipped 10", self.page.preset_status.text())

    def test_catalog_without_any_preset_items_does_not_change_the_project(self):
        self.page.load_catalog_snapshot(self.restricted_catalog({"66"}))
        before = deepcopy(self.page.dump())
        changed = QSignalSpy(self.page.changed)
        self.page.apply_preset()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(changed.count(), 0)
        self.assertFalse(self.page.undo_preset_button.isEnabled())
        self.assertIn("11 unavailable", self.page.preset_plan_label.text())
        self.assertFalse(self.page.apply_preset_button.isEnabled())

    def test_taste_limit_rejects_entire_preset_without_partial_assignment(self):
        self.page.load({"love": ["Coffee"], "like": [], "dislike": [], "hate": [],
                        "extension": {"keep": ["saved metadata"]}})
        before = deepcopy(self.page.assignments)
        original = deepcopy(self.page.original)
        changed = QSignalSpy(self.page.changed)
        with patch("pixelheart.gifts_page.MAX_GIFTS_PER_TASTE", 2):
            self.page.apply_preset()
        self.assertEqual(self.page.assignments, before)
        self.assertEqual(self.page.original, original)
        self.assertEqual(changed.count(), 0)
        self.assertFalse(self.page.undo_preset_button.isEnabled())
        self.assertIn("exceed 2", self.page.preset_status.text())

    def test_undo_restores_exact_legacy_assignments_and_metadata_once(self):
        gifts = {"love": ["id:66", "(O)Example.Mod_Flower"], "like": [],
                 "dislike": [], "hate": ["Coffee"], "extension": {"nested": [1, 2]}}
        self.page.load(gifts)
        before = deepcopy(self.page.assignments)
        changed = QSignalSpy(self.page.changed)
        self.page.apply_preset()
        self.assertNotEqual(self.page.assignments, before)
        QTest.mouseClick(self.page.undo_preset_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.page.assignments, before)
        self.assertEqual(self.page.original, gifts)
        self.assertEqual(self.page.dump()["extension"], gifts["extension"])
        self.assertEqual(changed.count(), 2)
        self.assertFalse(self.page.undo_preset_button.isEnabled())
        self.page.undo_preset()
        self.assertEqual(changed.count(), 2)
        self.assertEqual(self.page.assignments, before)

    def test_search_preview_and_icon_refresh_preserve_undo(self):
        before = deepcopy(self.page.assignments)
        self.page.apply_preset()
        changed = QSignalSpy(self.page.changed)
        self.page.search.setText("Coffee")
        self.page.unassigned_only.setChecked(True)
        self.page.preset_preview_button.click()
        self.select_preset("artist")
        self.page.refresh_wiki_icons()
        self.assertTrue(self.page.undo_preset_button.isEnabled())
        self.assertEqual(changed.count(), 0)
        self.page.undo_preset()
        self.assertEqual(self.page.assignments, before)
        self.assertEqual(changed.count(), 1)

    def test_manual_edit_invalidates_undo_so_later_work_cannot_be_lost(self):
        self.page.apply_preset()
        self.assertTrue(self.page.assign_items(["395"], "hate"))
        edited = deepcopy(self.page.assignments)
        changed = QSignalSpy(self.page.changed)
        self.assertFalse(self.page.undo_preset_button.isEnabled())
        self.page.undo_preset()
        self.assertEqual(self.page.assignments, edited)
        self.assertEqual(changed.count(), 0)

    def test_loading_a_document_or_catalog_invalidates_undo(self):
        for action in ("document", "catalog"):
            with self.subTest(action=action):
                self.page.load({taste: [] for taste in TASTES})
                self.page.apply_preset()
                if action == "document":
                    self.page.load({"love": ["id:66"], "like": [], "dislike": [], "hate": []})
                else:
                    self.page.load_catalog_snapshot(self.restricted_catalog({"395", "66"}))
                current = deepcopy(self.page.assignments)
                changed = QSignalSpy(self.page.changed)
                self.assertFalse(self.page.undo_preset_button.isEnabled())
                self.page.undo_preset()
                self.assertEqual(self.page.assignments, current)
                self.assertEqual(changed.count(), 0)

    def test_saved_and_reopened_starter_exports_real_content_patcher_tastes(self):
        window = self.keep(MainWindow())
        project_path = self.root / "character.json"
        metadata = {"author_note": "Keep these preferences editable"}
        document = deepcopy(window.document)
        document["character"]["gifts"]["extension"] = metadata
        window.load_document(document)
        preset = get_gift_preset("baker")
        self.select_preset(preset.id, window.gifts)
        window.gifts.apply_preset()
        self.assertTrue(window.dirty)
        expected = {**self.expected(preset), "extension": metadata}
        with patch.object(MainWindow, "show_error") as errors:
            self.assertTrue(window.save_to(project_path))
            self.assertEqual(load_project(project_path)["character"]["gifts"], expected)
            reopened = self.keep(MainWindow())
            self.assertTrue(reopened.open_path(project_path))
            self.assertEqual(reopened.gifts.dump(), expected)
            self.assertFalse(reopened.dirty)
            self.assertFalse(reopened.gifts.undo_preset_button.isEnabled())
            for kind, dimensions in (("portrait", (128, 192)), ("sprite", (64, 416))):
                source = self.root / (kind + ".png")
                Image.new("RGBA", dimensions, (120, 160, 90, 255)).save(source)
                with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
                    reopened.artwork.upload(kind)
            archive_path = self.root / "starter.zip"
            with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(archive_path), "")), \
                    patch("pixelheart.app.QMessageBox.information"):
                self.assertTrue(reopened.export_project())
            errors.assert_not_called()
        with zipfile.ZipFile(archive_path) as archive:
            content = json.loads(archive.read("[CP] NewCharacter/content.json"))
            gift_patch = next(change for change in content["Changes"] if change["Target"] == "Data/NPCGiftTastes")
            fields = next(iter(gift_patch["Entries"].values())).split("/")
            for index, taste in enumerate(TASTES):
                self.assertEqual(fields[index * 2 + 1].split(), list(preset.gifts[taste]))
            backup = json.loads(archive.read("[CP] NewCharacter/project.json"))
            self.assertEqual(backup["character"]["gifts"], expected)


if __name__ == "__main__":
    unittest.main()
