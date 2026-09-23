"""Headless coverage for birthday selection without accidental NPC conflicts."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QGridLayout

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.theme import apply_theme
from pixelheart_core.birthdays import SEASONS, birthdays_for_season
from pixelheart_core.projects import load_project


class BirthdayCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-birthday-test-")
        self.project_file = Path(self.temporary.name) / "character.json"
        self.windows = []
        self.error_patch = patch.object(MainWindow, "show_error")
        self.errors = self.error_patch.start()
        self.window = self.make_window()
        self.calendar = self.window.identity.birthday

    def tearDown(self):
        for window in reversed(self.windows):
            window.dirty = False
            window.close()
            window.deleteLater()
        self.application.processEvents()
        self.error_patch.stop()
        self.temporary.cleanup()

    def make_window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def browse(self, season):
        index = self.calendar.season_picker.findData(season)
        self.assertGreaterEqual(index, 0)
        self.calendar.season_picker.setCurrentIndex(index)

    def selected_date(self):
        return self.calendar.selected_season, self.calendar.selected_day

    def test_browsing_does_not_change_the_character(self):
        before = deepcopy(self.window.document)
        selected = self.selected_date()
        signals = QSignalSpy(self.calendar.date_changed)

        self.browse("summer")
        self.calendar.next_button.click()
        self.assertEqual(self.calendar.season_picker.currentData(), "fall")
        self.calendar.previous_button.click()
        self.assertEqual(self.calendar.season_picker.currentData(), "summer")

        self.assertEqual(self.selected_date(), selected)
        self.assertEqual(self.window.document, before)
        self.assertFalse(self.window.dirty)
        self.assertEqual(signals.count(), 0)

    def test_season_navigation_wraps_without_selecting_a_different_date(self):
        selected = self.selected_date()
        self.browse("spring")
        self.calendar.previous_button.click()
        self.assertEqual(self.calendar.season_picker.currentData(), "winter")
        self.calendar.next_button.click()
        self.assertEqual(self.calendar.season_picker.currentData(), "spring")
        self.assertEqual(self.selected_date(), selected)
        self.assertFalse(self.window.dirty)

    def test_occupied_days_are_disabled_in_every_season_without_an_override(self):
        original = self.selected_date()
        signals = QSignalSpy(self.calendar.date_changed)

        self.assertFalse(hasattr(self.calendar, "allow_shared"))
        self.assertEqual(self.calendar.findChildren(QCheckBox), [])
        for season in SEASONS:
            self.browse(season)
            occupied = birthdays_for_season(season)
            for day, day_button in self.calendar.day_buttons.items():
                with self.subTest(season=season, day=day):
                    self.assertEqual(day_button.isEnabled(), day not in occupied)
                    if day in occupied:
                        for name in occupied[day]:
                            self.assertIn(name, day_button.toolTip())
                        self.assertNotIn("Allow shared birthdays", day_button.toolTip())
                        day_button.click()

        self.assertEqual(self.selected_date(), original)
        self.assertEqual(signals.count(), 0)
        self.assertFalse(self.window.dirty)

    def test_select_rejects_occupied_dates_even_when_called_directly(self):
        before = deepcopy(self.window.document)
        original = self.selected_date()
        signals = QSignalSpy(self.calendar.date_changed)

        for season in SEASONS:
            self.browse(season)
            for day in birthdays_for_season(season):
                with self.subTest(season=season, day=day):
                    self.calendar._select(day)
                    self.assertEqual(self.selected_date(), original)

        self.assertEqual(self.window.document, before)
        self.assertEqual(signals.count(), 0)
        self.assertFalse(self.window.dirty)

    def test_selecting_an_open_day_updates_the_document_and_card_once(self):
        self.browse("summer")
        signals = QSignalSpy(self.calendar.date_changed)
        identity_changes = QSignalSpy(self.window.identity.changed)
        self.assertTrue(self.calendar.day_buttons[2].isEnabled())

        self.calendar.day_buttons[2].click()

        self.assertEqual(self.selected_date(), ("summer", 2))
        self.assertEqual(signals.count(), 1)
        self.assertEqual(signals.at(0), ["summer", 2])
        self.assertEqual(identity_changes.count(), 1)
        self.assertEqual(self.window.document["character"]["season"], "summer")
        self.assertEqual(self.window.document["character"]["day"], 2)
        self.assertIn("Summer 2", self.window.identity.profile_birthday.text())
        self.assertIn("Summer 2", self.calendar.selection_label.text())
        self.assertTrue(self.window.dirty)

    def test_loading_a_conflicting_birthday_preserves_it_without_signals(self):
        document = deepcopy(self.window.document)
        document["character"].update(season="spring", day=4)
        signals = QSignalSpy(self.calendar.date_changed)

        self.window.load_document(document)

        self.assertEqual(self.selected_date(), ("spring", 4))
        self.assertEqual(self.calendar.season_picker.currentData(), "spring")
        self.assertTrue(self.calendar.day_buttons[4].isChecked())
        self.assertFalse(self.calendar.day_buttons[4].isEnabled())
        self.assertIn("Spring 4", self.calendar.selection_label.text())
        self.assertIn("Kent", self.calendar.conflict_label.text())
        self.assertIn("Shared birthdays are not allowed.", self.calendar.conflict_label.text())
        self.assertIn("Choose an open day before exporting.", self.calendar.conflict_label.text())
        self.assertIn("Spring 4", self.window.identity.profile_birthday.text())
        self.assertEqual(self.window.document, document)
        self.assertEqual(signals.count(), 0)
        self.assertFalse(self.window.dirty)

        self.calendar.day_buttons[2].click()
        self.assertEqual(self.selected_date(), ("spring", 2))
        self.assertNotIn("Kent", self.calendar.conflict_label.text())
        self.assertEqual(signals.count(), 1)
        self.assertTrue(self.window.dirty)

    def test_set_date_is_silent(self):
        signals = QSignalSpy(self.calendar.date_changed)

        self.calendar.set_date("winter", 28)

        self.assertEqual(self.selected_date(), ("winter", 28))
        self.assertEqual(self.calendar.season_picker.currentData(), "winter")
        self.assertTrue(self.calendar.day_buttons[28].isChecked())
        self.assertEqual(signals.count(), 0)

    def test_save_reopen_preserves_an_open_birthday(self):
        self.browse("summer")
        self.calendar.day_buttons[2].click()
        self.assertTrue(self.window.save_to(self.project_file))
        self.assertFalse(self.window.dirty)

        saved = load_project(self.project_file)["character"]
        self.assertEqual((saved["season"], saved["day"]), ("summer", 2))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.project_file))
        calendar = reopened.identity.birthday

        self.assertEqual((calendar.selected_season, calendar.selected_day), ("summer", 2))
        self.assertEqual(calendar.season_picker.currentData(), "summer")
        self.assertTrue(calendar.day_buttons[2].isChecked())
        self.assertTrue(calendar.day_buttons[2].isEnabled())
        self.assertFalse(calendar.conflict_label.property("conflict"))
        self.assertIn("Summer 2", reopened.identity.profile_birthday.text())
        self.assertFalse(reopened.dirty)
        self.errors.assert_not_called()

    def test_review_birthday_error_opens_calendar_for_repair(self):
        document = deepcopy(self.window.document)
        document["character"].update(season="spring", day=4)
        self.window.load_document(document)
        self.window.show()
        self.window.open_section("export")
        self.application.processEvents()
        self.browse("winter")

        issues = self.window.export_page.refresh()

        self.assertTrue(any(issue["field"] == "day" and issue["level"] == "error" for issue in issues))
        self.assertFalse(self.window.export_page.export_button.isEnabled())
        items = self.window.export_page.list
        birthday_item = next(items.item(index) for index in range(items.count())
                             if items.item(index).data(Qt.ItemDataRole.UserRole)["field"] == "day")
        self.window.export_page.open_issue(birthday_item)
        self.application.processEvents()

        self.assertEqual(self.window.navigation.currentRow(), 0)
        self.assertEqual(self.calendar.season_picker.currentData(), "spring")
        self.assertIs(self.application.focusWidget(), self.calendar.season_picker)
        self.assertFalse(self.calendar.day_buttons[4].isEnabled())
        self.assertEqual(self.selected_date(), ("spring", 4))
        self.assertFalse(self.window.dirty)

        self.calendar.day_buttons[2].click()

        self.assertFalse(any(issue["field"] == "day" for issue in self.window.export_page.refresh()))
        self.assertEqual(self.selected_date(), ("spring", 2))

    def test_calendar_has_four_weeks_of_seven_days(self):
        self.assertEqual(set(self.calendar.day_buttons), set(range(1, 29)))
        grids = [layout for layout in self.calendar.findChildren(QGridLayout)
                 if layout.indexOf(self.calendar.day_buttons[1]) >= 0]
        self.assertEqual(len(grids), 1)
        grid = grids[0]
        first_row, first_column, _, _ = grid.getItemPosition(grid.indexOf(self.calendar.day_buttons[1]))

        for day, button in self.calendar.day_buttons.items():
            with self.subTest(day=day):
                row, column, row_span, column_span = grid.getItemPosition(grid.indexOf(button))
                self.assertEqual((row, column), (first_row + (day - 1) // 7, first_column + (day - 1) % 7))
                self.assertEqual((row_span, column_span), (1, 1))

    def test_keyboard_navigation_skips_reserved_dates_before_selecting(self):
        self.window.show()
        self.application.processEvents()
        self.browse("spring")
        signals = QSignalSpy(self.calendar.date_changed)
        self.calendar.day_buttons[3].setFocus()

        QTest.keyClick(self.calendar.day_buttons[3], Qt.Key.Key_Right)

        self.assertIs(self.application.focusWidget(), self.calendar.day_buttons[5])
        self.assertEqual(self.selected_date(), ("spring", 1))
        self.assertEqual(signals.count(), 0)
        self.assertFalse(self.window.dirty)

        QTest.keyClick(self.calendar.day_buttons[5], Qt.Key.Key_Space)

        self.assertEqual(self.selected_date(), ("spring", 5))
        self.assertEqual(signals.count(), 1)
        self.assertTrue(self.window.dirty)

        # Activating the selected day again must not clear it or add an edit.
        QTest.keyClick(self.calendar.day_buttons[5], Qt.Key.Key_Space)
        self.assertTrue(self.calendar.day_buttons[5].isChecked())
        self.assertEqual(signals.count(), 1)


if __name__ == "__main__":
    unittest.main()
