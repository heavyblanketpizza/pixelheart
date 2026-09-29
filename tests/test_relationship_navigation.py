"""Legacy relationship checks route clearly without altering preserved data."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.theme import apply_theme
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_relationship
from tests.qt_support import QtTestCase


class RelationshipNavigationTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def test_legacy_issue_routes_to_visible_notice_without_changing_data_at_minimum_size(self):
        settings = SimpleNamespace(value=lambda *_: "")
        with patch("pixelheart.game_import.game_import_settings", return_value=settings):
            window = MainWindow(preload_story=False)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            document = new_project()
            arc = new_relationship()
            arc["name"] = "A shared creative life"
            arc["description"] = "Existing relationship notes"
            arc["story"].update(desire="Keep their own ambitions", boundaries="Time alone", married="Shared daily rituals")
            arc["extension"] = {"keep": ["legacy relationship metadata"]}
            document["character"]["relationships"] = [arc]
            document["character"]["storyline"]["extension"] = {"keep": True}
            window.load_document(document)
            window.resize(1020, 700)
            window.show()
            window.open_section("story")
            self.application.processEvents()
            before = window.project_snapshot()
            preserved = deepcopy(before["character"]["relationships"])
            self.assertFalse(hasattr(window, "relationships"))
            for field in ("description", "desire", "boundaries", "married"):
                with self.subTest(field=field):
                    window.open_section("export")
                    path = f"relationships.0.{field}" if field == "description" else f"relationships.0.story.{field}"
                    item = QListWidgetItem("Review this preserved relationship setting")
                    item.setData(Qt.ItemDataRole.UserRole, {"field": path})
                    window.export_page.open_issue(item)
                    self.application.processEvents()
                    self.application.processEvents()
                    notice = window.story.events.notice
                    self.assertEqual(window.stack.currentIndex(), SECTION_INDEX["story"])
                    self.assertTrue(notice.isVisible())
                    self.assertIn("remain in your project", notice.text())
                    self.assertIn("no editor in Romance", notice.text())
                    self.assertTrue(window.rect().contains(notice.mapTo(window, notice.rect().center())))
                    self.assertEqual(window.story.dump()["relationships"], preserved)
            self.assertEqual(window.project_snapshot(), before)
            self.assertFalse(window.dirty)
