"""Location IDs remain deterministic while custom-map projects round-trip."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from pixelheart.location_picker import MapSelector
from pixelheart_core.locations import LOCATIONS_BY_ID, VANILLA_LOCATIONS, location_name, location_note


class LocationCatalogTests(unittest.TestCase):
    def test_ids_are_unique_and_are_locations_not_asset_paths(self):
        ids = [location.id for location in VANILLA_LOCATIONS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all("/" not in value and "\\" not in value for value in ids))
        self.assertNotIn("Farm_Standard", ids)
        self.assertNotIn("Island_S", ids)
        self.assertIn("Farm", ids)
        self.assertIn("IslandSouth", ids)

    def test_friendly_labels_do_not_replace_the_game_ids(self):
        self.assertEqual(location_name("SeedShop"), "Pierre's general store")
        self.assertEqual(location_name("JoshHouse"), "Alex, Evelyn & George's house")
        self.assertEqual(location_name("Custom_Alcove"), "Custom_Alcove")
        self.assertIn("farm type", location_note("Farm"))
        self.assertIn("does not create", location_note("Custom_Alcove"))


class MapSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.selector = MapSelector()

    def tearDown(self):
        self.selector.close()
        self.selector.deleteLater()
        self.app.processEvents()

    def test_loading_known_and_unknown_ids_preserves_exact_value_silently(self):
        spy = QSignalSpy(self.selector.changed)
        for value in ("SeedShop", "Author.Mod_Cottage", "town", "  Legacy_Map  ", ""):
            with self.subTest(value=value):
                self.selector.set_value(value)
                self.assertEqual(self.selector.value(), value)
                self.assertEqual(self.selector.combo.currentIndex() == self.selector.custom_index, value not in LOCATIONS_BY_ID)
        self.assertEqual(spy.count(), 0)

    def test_search_text_does_not_become_a_location_on_focus_loss(self):
        spy = QSignalSpy(self.selector.changed)
        self.selector.combo.setEditText("Pierre")
        self.assertEqual(self.selector.value(), "Town")
        self.selector.combo.lineEdit().editingFinished.emit()
        self.assertIn("Pelican Town", self.selector.combo.currentText())
        self.assertEqual(self.selector.value(), "Town")
        self.assertEqual(spy.count(), 0)

    def test_typing_an_unknown_name_and_return_never_inserts_a_choice(self):
        count = self.selector.combo.count()
        self.selector.combo.lineEdit().selectAll()
        QTest.keyClicks(self.selector.combo.lineEdit(), "SomeUnknownMap")
        QTest.keyClick(self.selector.combo.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(self.selector.combo.count(), count)
        self.assertEqual(self.selector.value(), "Town")

    def test_keyboard_completion_commits_the_matching_location_id(self):
        self.selector.show()
        self.app.processEvents()
        self.selector.combo.lineEdit().selectAll()
        QTest.keyClicks(self.selector.combo.lineEdit(), "Pierre")
        self.app.processEvents()
        completion = self.selector.combo.completer()
        self.assertEqual(completion.completionCount(), 1)
        self.assertEqual(self.selector.value(), "Town")
        QTest.keyClick(completion.popup(), Qt.Key.Key_Down)
        QTest.keyClick(completion.popup(), Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.selector.value(), "SeedShop")

    def test_selection_commits_canonical_id_and_emits_once(self):
        spy = QSignalSpy(self.selector.changed)
        self.selector.combo.setCurrentIndex(self.selector.combo.findData("SeedShop"))
        self.assertEqual(self.selector.value(), "SeedShop")
        self.assertTrue(self.selector.custom.isHidden())
        self.assertEqual(spy.count(), 1)

    def test_custom_choice_is_explicit_and_remembers_its_text(self):
        self.selector.combo.setCurrentIndex(self.selector.custom_index)
        self.assertFalse(self.selector.custom.isHidden())
        self.assertEqual(self.selector.value(), "")
        self.selector.custom.setText("Author.Home")
        self.assertEqual(self.selector.value(), "Author.Home")
        self.selector.combo.setCurrentIndex(self.selector.combo.findData("Town"))
        self.assertEqual(self.selector.value(), "Town")
        self.selector.combo.setCurrentIndex(self.selector.custom_index)
        self.assertEqual(self.selector.value(), "Author.Home")

    def test_custom_input_does_not_overwrite_a_known_selected_location(self):
        self.selector.custom.setText("Author.Other")
        self.assertEqual(self.selector.value(), "Town")

    def test_compact_picker_retains_relevant_hint_as_tooltip(self):
        compact = MapSelector("Farm", compact=True)
        try:
            self.assertTrue(compact.hint.isHidden())
            self.assertIn("farm type", compact.toolTip())
        finally:
            compact.deleteLater()

    def test_legacy_text_setter_emits_and_unknown_ids_are_not_truncated(self):
        spy = QSignalSpy(self.selector.changed)
        value = "Author." + "x" * 95
        self.selector.setText(value)
        self.assertEqual(self.selector.text(), value)
        self.assertEqual(self.selector.custom.text(), value)
        self.assertEqual(spy.count(), 1)


if __name__ == "__main__":
    unittest.main()
