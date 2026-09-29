"""Routine times read like the game's clock; saved values don't change."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from pixelheart.schedule_time import ScheduleTime, clock_label


class ClockLabelTests(unittest.TestCase):
    def test_twelve_hour_labels(self):
        self.assertEqual(clock_label(360), "6:00 AM")
        self.assertEqual(clock_label(9 * 60 + 30), "9:30 AM")
        self.assertEqual(clock_label(12 * 60), "12:00 PM")
        self.assertEqual(clock_label(13 * 60 + 10), "1:10 PM")
        self.assertEqual(clock_label(23 * 60 + 50), "11:50 PM")
        self.assertEqual(clock_label(24 * 60), "12:00 AM (next day)")
        self.assertEqual(clock_label(26 * 60), "2:00 AM (next day)")


class ScheduleTimeLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_menu_shows_the_clock_and_keeps_game_times(self):
        picker = ScheduleTime()
        self.addCleanup(picker.deleteLater)
        self.assertEqual(picker.itemText(picker.findData("600")), "6:00 AM")
        self.assertEqual(picker.itemText(picker.findData("1330")), "1:30 PM")
        self.assertEqual(picker.itemText(picker.findData("2600")), "2:00 AM (next day)")
        picker.setText("14:30")
        self.assertEqual(picker.text(), "1430")

    def test_invalid_saved_time_is_kept(self):
        picker = ScheduleTime()
        self.addCleanup(picker.deleteLater)
        picker.setText("615")
        self.assertEqual(picker.text(), "615")
        self.assertEqual(picker.currentText(), "615 · review")


if __name__ == "__main__":
    unittest.main()
