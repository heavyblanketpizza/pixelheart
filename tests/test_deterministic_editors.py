"""Exercise deterministic fields through their real authoring pages."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
import unittest

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.editors import IdentityPage, SchedulePage
from pixelheart.location_picker import MapSelector
from pixelheart.schedule_time import ScheduleTime, game_minutes
from pixelheart_core.projects import new_project


def stop(time="600", location="Town"):
    return {"id": "stable-stop", "time": time, "location": location,
            "x": 32, "y": 62, "facing": "down", "activity": "Read a book"}


class DeterministicEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def identity(self):
        page = self.keep(IdentityPage())
        page.load(new_project()["character"])
        return page

    def schedule(self, records=None):
        page = self.keep(SchedulePage())
        page.load([stop()] if records is None else records)
        return page

    def test_identity_exposes_only_explicit_game_gender_choices(self):
        page = self.identity()
        picker = page.fields["gender"]
        self.assertFalse(picker.isEditable())
        self.assertEqual([(picker.itemText(i), picker.itemData(i)) for i in range(picker.count())],
                         [("Woman", "Female"), ("Man", "Male"), ("Unspecified", "Undefined")])
        self.assertNotIn("pronouns", page.fields)
        self.assertEqual(page.dump()["gender"], "Undefined")
        spy = QSignalSpy(page.changed)
        picker.setCurrentIndex(picker.findData("Female"))
        self.assertEqual(page.dump()["gender"], "Female")
        self.assertEqual(spy.count(), 1)

    def test_legacy_identity_load_uses_exact_pronoun_migration_without_editing(self):
        page = self.identity()
        spy = QSignalSpy(page.changed)
        for pronouns, expected in (("she/her", "Female"), ("he/him", "Male"),
                                   ("they/them", "Undefined"), ("she/they", "Undefined")):
            with self.subTest(pronouns=pronouns):
                source = new_project()["character"]
                source.pop("gender")
                source["pronouns"] = pronouns
                saved = deepcopy(source)
                page.load(source)
                self.assertEqual(page.dump()["gender"], expected)
                self.assertEqual(source, saved)
        self.assertEqual(spy.count(), 0)

    def test_explicit_gender_takes_precedence_over_legacy_pronouns(self):
        page = self.identity()
        source = new_project()["character"]
        source.update(gender="Male", pronouns="she/her")
        page.load(source)
        self.assertEqual(page.dump()["gender"], "Male")

    def test_identity_stores_game_id_after_friendly_location_selection(self):
        page = self.identity()
        picker = page.fields["home_map"]
        self.assertIsInstance(picker, MapSelector)
        spy = QSignalSpy(page.changed)
        picker.combo.setCurrentIndex(picker.combo.findData("SeedShop"))
        self.assertEqual(page.dump()["home_map"], "SeedShop")
        self.assertEqual(spy.count(), 1)
        picker.combo.setEditText("Whatever the user searches")
        self.assertEqual(page.dump()["home_map"], "SeedShop")
        self.assertEqual(spy.count(), 1)

    def test_identity_custom_home_round_trips_without_name_guessing(self):
        page = self.identity()
        source = new_project()["character"]
        source["home_map"] = "Author.Mod_Cottage-West"
        page.load(source)
        picker = page.fields["home_map"]
        self.assertEqual(picker.combo.currentIndex(), picker.custom_index)
        self.assertEqual(page.dump()["home_map"], "Author.Mod_Cottage-West")
        picker.custom.setText("AnotherAuthor.Cottage_2")
        self.assertEqual(page.dump()["home_map"], "AnotherAuthor.Cottage_2")

    def test_time_picker_contains_only_ten_minute_game_clock_choices(self):
        picker = self.keep(ScheduleTime())
        values = [picker.itemData(i) for i in range(picker.count())]
        self.assertFalse(picker.isEditable())
        self.assertEqual(len(values), 121)
        self.assertEqual([game_minutes(value) for value in values], list(range(360, 1561, 10)))
        self.assertEqual(picker.itemText(picker.findData("2400")), "24:00 (+1 day)")
        self.assertEqual(picker.itemText(picker.findData("2600")), "26:00 (+1 day)")

    def test_time_choices_update_actual_stop_and_keep_other_record_fields(self):
        page = self.schedule()
        original = page.dump()[0]
        picker = page.table.cellWidget(0, 0)
        self.assertIsInstance(picker, ScheduleTime)
        spy = QSignalSpy(page.changed)
        picker.setCurrentIndex(picker.findData("2410"))
        self.assertEqual(page.dump()[0], {**original, "time": "2410"})
        self.assertEqual(spy.count(), 1)

    def test_valid_legacy_clock_text_is_displayed_as_a_known_choice(self):
        page = self.schedule([stop("06:10")])
        picker = page.table.cellWidget(0, 0)
        self.assertEqual(picker.currentData(), "610")
        self.assertEqual(picker.currentText(), "06:10")
        # Loading only changes presentation; a new selection writes canonical HHMM.
        self.assertEqual(page.dump()[0]["time"], "06:10")
        picker.setCurrentIndex(picker.findData("620"))
        self.assertEqual(page.dump()[0]["time"], "620")

    def test_invalid_legacy_clock_is_retained_until_user_chooses_valid_time(self):
        page = self.schedule([stop("645")])
        picker = page.table.cellWidget(0, 0)
        self.assertEqual(picker.currentData(), "645")
        self.assertIn("review", picker.currentText())
        self.assertEqual(page.dump()[0]["time"], "645")
        picker.setCurrentIndex(picker.findData("650"))
        self.assertEqual(page.dump()[0]["time"], "650")

    def test_add_stop_uses_increasing_time_and_copies_exact_location(self):
        page = self.schedule([stop("2310", "Author.Home")])
        page.add()
        page.add()
        rows = page.dump()
        self.assertEqual([row["time"] for row in rows], ["2310", "2410", "2510"])
        self.assertEqual([row["location"] for row in rows], ["Author.Home"] * 3)
        self.assertEqual(len({row["id"] for row in rows}), 3)
        self.assertEqual(rows[0]["activity"], "Read a book")
        self.assertEqual(rows[1]["activity"], "")

    def test_empty_schedule_starts_at_six_and_late_last_stop_caps_at_twenty_six(self):
        page = self.schedule([])
        page.add()
        self.assertEqual(page.dump()[0]["time"], "600")
        page.load([stop("2550")])
        page.add()
        self.assertEqual([row["time"] for row in page.dump()], ["2550", "2600"])
        self.assertFalse(page.add_button.isEnabled())
        spy = QSignalSpy(page.changed)
        page.add()
        self.assertEqual(len(page.dump()), 2)
        self.assertEqual(spy.count(), 0)

    def test_editing_last_stop_earlier_reenables_add_and_at_limit_disables_it(self):
        page = self.schedule([stop("2600")])
        self.assertFalse(page.add_button.isEnabled())
        picker = page.table.cellWidget(0, 0)
        picker.setCurrentIndex(picker.findData("2500"))
        self.assertTrue(page.add_button.isEnabled())
        self.assertNotIn("last stop is at 26:00", page.add_button.toolTip())
        picker.setCurrentIndex(picker.findData("2600"))
        self.assertFalse(page.add_button.isEnabled())
        self.assertIn("last stop is at 26:00", page.add_button.toolTip())

    def test_custom_schedule_location_expands_row_and_updates_exact_saved_id(self):
        page = self.schedule()
        picker = page.table.cellWidget(0, 1)
        self.assertIsInstance(picker, MapSelector)
        known_height = page.table.rowHeight(0)
        picker.combo.setCurrentIndex(picker.custom_index)
        self.app.processEvents()
        self.assertFalse(picker.custom.isHidden())
        self.assertGreater(page.table.rowHeight(0), known_height)
        picker.custom.setText("Author.Mod_Loft-2")
        self.assertEqual(page.dump()[0]["location"], "Author.Mod_Loft-2")
        self.assertEqual(page.dump()[0]["id"], "stable-stop")
        picker.combo.setCurrentIndex(picker.combo.findData("IslandSouth"))
        self.assertEqual(page.dump()[0]["location"], "IslandSouth")
        self.assertEqual(page.table.rowHeight(0), known_height)

    def test_custom_schedule_load_preserves_exact_value_and_is_silent(self):
        page = self.schedule()
        spy = QSignalSpy(page.changed)
        source = [stop("600", "Author.Preserved_CASE")]
        page.load(source)
        self.assertEqual(page.dump(), source)
        self.assertEqual(spy.count(), 0)
        picker = page.table.cellWidget(0, 1)
        self.assertEqual(picker.custom.text(), "Author.Preserved_CASE")
        self.assertGreaterEqual(page.table.rowHeight(0), picker.sizeHint().height())


if __name__ == "__main__":
    unittest.main()
