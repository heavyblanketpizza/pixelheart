"""Inherited tastes are visible and editable without becoming saved overrides."""

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.gifts_page import GiftsPage, TASTES
from pixelheart.item_icons import ItemIconStore
from pixelheart_core.projects import load_project
from tests.test_gifts import GiftDrop
from tests.qt_support import QtTestCase


class GiftDefaultsDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pixelheart-gift-defaults-")
        self.root = Path(self.temp.name)
        self.widgets = []
        store = ItemIconStore(self.root / "textures", self.root / "wiki-icons")
        self.icon_patch = patch("pixelheart.gifts_page.ItemIconStore", return_value=store)
        self.icon_patch.start()
        self.page = self.keep(GiftsPage())

    def tearDown(self):
        for widget in reversed(self.widgets):
            if isinstance(widget, MainWindow):
                widget.dirty = False
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.icon_patch.stop()
        self.temp.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def ids(self, taste, page=None):
        page = page or self.page
        target = page.lists[taste]
        return [page.key(target.item(row).data(Qt.ItemDataRole.UserRole))
                for row in range(target.count())]

    def assert_baseline(self, page=None):
        for taste, item_id in (("love", "74"), ("like", "395"),
                               ("dislike", "330"), ("hate", "168")):
            self.assertIn(item_id, self.ids(taste, page))

    def test_new_character_shows_all_four_defaults_without_personal_overrides(self):
        window = self.keep(MainWindow())
        page = window.gifts
        before = deepcopy(window.document)
        changes = QSignalSpy(page.changed)
        self.assertTrue(page.show_game_defaults.isChecked())
        self.assert_baseline(page)
        self.assertEqual(page.dump(), {taste: [] for taste in TASTES})
        self.assertFalse(window.dirty)
        self.assertEqual(window.document, before)
        self.assertEqual(changes.count(), 0)

    def test_non_giftable_vanilla_objects_are_absent_from_library_and_inherited_tastes(self):
        excluded = {"930", "PetLicense", "922", "923", "924"}
        library_ids = {
            self.page.key(self.page.library.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.page.library.count())
        }
        self.assertFalse(excluded & library_ids)
        for taste in TASTES:
            with self.subTest(taste=taste):
                self.assertFalse(excluded & set(self.ids(taste)))
        self.assertTrue({"791", "MysteryBox", "GoldenMysteryBox", "Book_Horse", "StardropTea"} <= library_ids)
        self.assertIn("791", self.ids("hate"))
        self.assertFalse(self.page.assign_items(["(O)930"], "love"))
        self.assertEqual(self.page.dump(), {taste: [] for taste in TASTES})

    def test_saved_removed_objects_are_preserved_with_warning_until_explicitly_reset(self):
        values = ["(O)930", "(O)PetLicense", "(O)922", "(O)923", "(O)924"]
        saved = {**{taste: [] for taste in TASTES}, "love": values, "extension": {"keep": True}}
        changes = QSignalSpy(self.page.changed)
        self.page.load(saved)
        self.assertEqual(self.page.dump(), saved)
        self.assertEqual(changes.count(), 0)
        self.assertIn("5 saved assignment(s) are outside this catalog", self.page.feedback.text())
        for row in range(len(values)):
            item = self.page.lists["love"].item(row)
            self.assertIn("not in catalog", item.text())
            self.assertIn("Saved assignment retained", item.toolTip())
        self.assertTrue(self.page.assign_items(values, None))
        self.assertEqual(self.page.dump(), {**saved, "love": []})
        self.assertEqual(self.page.feedback.text(), "")
        self.assertEqual(changes.count(), 1)
        for taste in TASTES:
            self.assertFalse({"930", "PetLicense", "922", "923", "924"} & set(self.ids(taste)))

    def test_dragging_an_inherited_item_creates_one_override_and_reset_restores_default(self):
        self.assertIn("395", self.ids("like"))
        changes = QSignalSpy(self.page.changed)
        event = GiftDrop(self.page.lists["like"], ["(O)395"])
        self.page.lists["love"].dropEvent(event)
        self.assertTrue(event.accepted)
        self.assertIn("395", self.ids("love"))
        self.assertNotIn("395", self.ids("like"))
        self.assertEqual(self.page.dump(), {"love": ["(O)395"], "like": [], "dislike": [], "hate": []})
        self.page.assign_items(["(O)395"], None)
        self.assertIn("395", self.ids("like"))
        self.assertNotIn("395", self.ids("love"))
        self.assertEqual(self.page.dump(), {taste: [] for taste in TASTES})
        self.assertEqual(changes.count(), 2)

    def test_show_defaults_toggle_preserves_personal_items_metadata_and_clean_state(self):
        self.page.load({"love": ["Coffee"], "extension": {"keep": [1, 2]}})
        before = deepcopy(self.page.dump())
        changes = QSignalSpy(self.page.changed)
        self.page.show_game_defaults.setChecked(False)
        self.assertEqual(self.ids("love"), ["395"])
        self.assertTrue(all(not self.ids(taste) for taste in TASTES if taste != "love"))
        self.page.show_game_defaults.setChecked(True)
        self.assertIn("74", self.ids("love"))
        self.assertNotIn("395", self.ids("like"))
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(changes.count(), 0)

    def test_preset_changes_inherited_tastes_but_keeps_authored_choices_and_undo(self):
        self.page.load({"hate": ["Diamond"]})
        before = deepcopy(self.page.dump())
        self.page.apply_preset()
        self.assertIn("395", self.ids("love"))
        self.assertNotIn("395", self.ids("like"))
        self.assertIn("72", self.ids("hate"))
        self.assertNotIn("72", self.ids("love"))
        self.page.undo_preset()
        self.assertEqual(self.page.dump(), before)
        self.assertIn("395", self.ids("like"))
        self.assertIn("72", self.ids("hate"))

    def test_imported_catalog_does_not_invent_defaults_for_mod_items(self):
        snapshot = deepcopy(self.page.base_catalog)
        snapshot["source"] = "content-patcher-export"
        snapshot["label"] = "My game objects"
        snapshot["imported_at"] = "2026-09-19T00:00:00Z"
        snapshot["items"] = [item for item in snapshot["items"] if item["id"] == "395"]
        snapshot["items"].append({"id": "Example.Mod_Coffee", "name": "Moon coffee",
                                  "category": -7, "category_name": "Cooking"})
        self.page.load_catalog_snapshot(snapshot)
        self.assertEqual(self.ids("like"), ["395"])
        for taste in TASTES:
            self.assertNotIn("Example.Mod_Coffee", self.ids(taste))
        self.assertEqual(self.page.dump(), {taste: [] for taste in TASTES})

    def test_saved_project_contains_only_edits_and_reopens_with_inherited_defaults(self):
        window = self.keep(MainWindow())
        window.gifts.assign_items(["395"], "love")
        expected = {"love": ["(O)395"], "like": [], "dislike": [], "hate": []}
        path = self.root / "character.json"
        self.assertTrue(window.save_to(path))
        self.assertEqual(load_project(path)["character"]["gifts"], expected)
        reopened = self.keep(MainWindow())
        self.assertTrue(reopened.open_path(path))
        self.assertEqual(reopened.gifts.dump(), expected)
        self.assertIn("395", self.ids("love", reopened.gifts))
        self.assertNotIn("395", self.ids("like", reopened.gifts))
        self.assertIn("330", self.ids("dislike", reopened.gifts))
        self.assertFalse(reopened.dirty)


if __name__ == "__main__":
    unittest.main()
