"""Gift tiles remain usable inside the themed desktop's available space."""

import os
import tempfile
import unittest
from copy import deepcopy
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.item_icons import ItemIconStore
from pixelheart.theme import apply_theme


class GiftLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-gift-layout-")
        root = Path(self.temporary.name)
        store = ItemIconStore(root / "textures", root / "wiki-icons")
        with patch("pixelheart.gifts_page.ItemIconStore", return_value=store):
            self.window = MainWindow()
        self.page = self.window.gifts
        self.window.navigation.setCurrentRow(3)
        self.window.show()
        self.settle_layout()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temporary.cleanup()

    def settle_layout(self):
        # Resizing a viewport can change its scrollbar, triggering another
        # layout pass before the final tile geometry is available.
        for _ in range(4):
            self.app.processEvents()

    def lists(self):
        return [self.page.library, *self.page.lists.values()]

    def assert_first_rows_fit(self):
        columns = {}
        for listing in self.lists():
            name = listing.accessibleName()
            self.assertGreaterEqual(listing.count(), 2, name)
            listing.scrollToTop()
            self.settle_layout()
            viewport = listing.viewport().rect()
            first = listing.visualItemRect(listing.item(0))
            row = []
            for index in range(listing.count()):
                rect = listing.visualItemRect(listing.item(index))
                if rect.top() != first.top():
                    break
                row.append(rect)
                self.assertTrue(rect.isValid(), name)
                self.assertTrue(
                    viewport.contains(rect),
                    f"{name}: tile {rect.getRect()} exceeds viewport {viewport.getRect()}",
                )
            self.assertGreaterEqual(len(row), 2, f"{name} should fit at least two columns")
            for left, right in combinations(row, 2):
                self.assertFalse(left.intersects(right), f"{name}: first-row tiles overlap")
            self.assertLessEqual(listing.iconSize().width(), 32, name)
            self.assertLessEqual(listing.iconSize().height(), 32, name)
            columns[name] = len(row)
        return columns

    def test_first_rows_fit_default_minimum_expanded_and_short_workspaces(self):
        for dimensions in ((1360, 900), (1020, 700), (1600, 1100), (1600, 700), (1020, 700)):
            with self.subTest(window=dimensions):
                self.window.resize(*dimensions)
                self.settle_layout()
                self.assert_first_rows_fit()

    def test_resize_and_defaults_rerender_preserve_gifts_and_selection(self):
        self.page.load({
            "love": ["66", "74"],
            "like": ["395", "421"],
            "dislike": ["24", "330"],
            "hate": ["168", "170"],
            "extension": {"note": "Keep this project metadata"},
        })
        selected_list = self.page.lists["love"]
        selected_list.item(0).setSelected(True)
        selected_list.item(1).setSelected(True)
        selected = selected_list.selected_values()
        before = deepcopy(self.page.dump())
        dirty = self.window.dirty
        changes = QSignalSpy(self.page.changed)

        for dimensions, show_defaults in (
            ((1020, 700), True),
            ((1600, 1100), False),
            ((1600, 700), True),
            ((1360, 900), False),
            ((1020, 700), True),
        ):
            with self.subTest(window=dimensions, defaults=show_defaults):
                self.window.resize(*dimensions)
                self.page.show_game_defaults.setChecked(show_defaults)
                self.settle_layout()
                self.assert_first_rows_fit()
                self.assertEqual(self.page.dump(), before)
                self.assertEqual(selected_list.selected_values(), selected)
                self.assertIs(self.page.active_list, selected_list)
                self.assertEqual(self.window.dirty, dirty)
                self.assertEqual(changes.count(), 0)


if __name__ == "__main__":
    unittest.main()
