"""The Overview page and heart-marked sidebar show what's done and what's next."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow, PROGRESS_ROLE, SECTION_INDEX, SECTIONS
from tests.qt_support import QtTestCase


class OverviewTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()

    def test_sidebar_uses_player_language_with_overview_first(self):
        titles = [self.window.navigation.item(row).text() for row in range(self.window.navigation.count())]
        self.assertEqual(titles, ["Overview", "About them", "Conversations", "Daily routine", "Gifts",
                                  "Heart events", "Portraits & sprites", "Home", "Play in Stardew"])
        self.assertEqual(SECTIONS[0][0], "overview")
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["overview"])

    def test_nav_hearts_follow_progress(self):
        row = SECTION_INDEX["identity"]
        self.assertIs(self.window.navigation.item(row).data(PROGRESS_ROLE), False)
        self.assertIsNone(self.window.navigation.item(SECTION_INDEX["overview"]).data(PROGRESS_ROLE))
        self.window.identity.fields["name"].setText("Mira")
        self.window.refresh_progress()
        self.assertIs(self.window.navigation.item(row).data(PROGRESS_ROLE), True)
        self.assertIn("Meet Mira", self.window.navigation.item(row).toolTip())

    def test_typing_refreshes_progress_after_a_pause(self):
        self.window.identity.fields["name"].setText("Theo")
        self.assertTrue(self.window._progress_timer.isActive())

    def test_overview_lists_steps_and_opens_them(self):
        overview = self.window.overview
        self.assertEqual(overview.meter.filled(), 0)
        self.assertEqual(len(overview.step_buttons), 8)
        self.assertIn("name", overview.next_title.text().lower())
        overview.step_buttons["gifts"].click()
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["gifts"])
        self.window.open_section("overview")
        overview.next_button.click()
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["identity"])

    def test_overview_shows_blockers_from_checks(self):
        self.window.open_section("gifts")
        self.window.open_section("overview")
        self.assertIn("to fix", self.window.overview.blockers.text())
        self.window.overview.review_button.click()
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["export"])


if __name__ == "__main__":
    unittest.main()
