"""Workspace interactions stay usable without requiring local game assets."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart.schedule_time import EventTime
from pixelheart_core.story import new_relationship
from pixelheart_core.projects import new_project
from pixelheart_core.story_planning import new_chapter
from tests.qt_support import QtTestCase


class StoryWorkspaceTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = patch("pixelheart.game_import.game_import_settings", return_value=SimpleNamespace(value=lambda *_: self.temporary.name))
        self.settings.start()
        self.window = MainWindow()
        self.window.resize(1440, 900)
        self.window.show()
        self.window.open_section("story")
        self.page = self.window.events
        self.page.phases.setCurrentIndex(self.page.SCENE)
        self.settle()

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.settings.stop()
        self.temporary.cleanup()

    def test_scene_and_sequence_share_the_workspace_without_page_scrolling(self):
        for size, orientation in [((1440, 900), Qt.Orientation.Horizontal), ((1020, 700), Qt.Orientation.Horizontal)]:
            with self.subTest(size=size):
                self.window.resize(*size)
                self.settle()
                scroll = self.page.phases.currentWidget()
                self.assertEqual(self.page.scene_split.orientation(), orientation)
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                for widget in (self.page.actors.canvas, self.page.beats.list):
                    self.assertTrue(widget.isVisible())
                    center = widget.mapTo(scroll.viewport(), widget.rect().center())
                    self.assertTrue(scroll.viewport().rect().contains(center))
        self.assertFalse(hasattr(self.page, "template"))
        self.assertTrue(self.page.next_button.isHidden())
        self.assertFalse(hasattr(self.page, "rail"))
        self.assertTrue(self.page.compact_browser.isHidden())
        self.window.story.milestones.buttons[6].click()
        self.assertEqual(self.page.current, 2)

    def test_event_removal_unlinks_saved_chapter_and_global_undo_restores_everything(self):
        document = self.window.project_snapshot()
        chapter = new_chapter('A saved chapter')
        removed_id = document['character']['events'][0]['id']
        chapter['event_ids'] = [removed_id]
        document['character']['storyline']['chapters'] = [chapter]
        self.window.load_document(document)
        self.window.open_section("story")
        before = self.window.project_snapshot()
        self.page.remove()
        self.assertEqual(len(self.page.records), 5)
        self.assertTrue(all(removed_id not in chapter['event_ids'] for chapter in self.window.story.legacy_storyline['chapters']))
        self.assertTrue(self.page.reference_issues.isHidden())
        self.window.project_history.undo()
        self.assertEqual(self.window.project_snapshot(), before)

    def test_hiding_scene_gives_the_sequence_the_available_width_and_reopens(self):
        before = self.window.project_snapshot()
        self.page.actors.toggle_preview()
        self.settle()
        self.assertGreater(self.page.scene_inspector.width(), self.page.scene_workspace.width() - 30)
        self.assertLessEqual(self.page.actors.height(), 44)
        self.page.actors.toggle_preview()
        self.settle()
        self.assertEqual(self.page.scene_split.orientation(), Qt.Orientation.Horizontal)
        self.assertGreater(self.page.actors.canvas.height(), 200)
        self.assertEqual(self.window.project_snapshot(), before)

    def test_a_real_prerequisite_blocks_removal_with_a_working_editor_link(self):
        self.page.records[1]["story"]["previous_event_id"] = self.page.records[0]["id"]
        self.window.story.on_change()
        before = self.window.project_snapshot()
        self.page.remove()
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertEqual(self.page.reference_issues.count(), 1)
        item = self.page.reference_issues.item(0)
        self.page.open_reference_issue(item)
        self.settle()
        self.assertEqual(self.page.current, 1)
        self.assertEqual(self.page.phases.currentIndex(), self.page.TRIGGER)
        self.assertTrue(self.page.reference_issues.isHidden())

    def test_saved_relationship_issue_explains_preservation_without_mutating_data(self):
        document = self.window.project_snapshot()
        relationship = new_relationship()
        relationship['story']['boundaries'] = 'An authored boundary.'
        document['character']['relationships'] = [relationship]
        self.window.load_document(document)
        self.window.open_section("story")
        before = deepcopy(self.window.project_snapshot())
        self.window.resize(1020, 700)
        self.window.story.open_issue('relationships.0.story.boundaries')
        self.settle()
        self.assertTrue(self.page.notice.isVisible())
        self.assertIn('remain in your project', self.page.notice.text())
        self.assertEqual(self.window.project_snapshot(), before)

    def test_empty_milestone_creates_a_scene_only_after_explicit_request(self):
        self.window.load_document(new_project())
        self.window.open_section("story")
        self.window.story.milestones.buttons[8].click()
        self.settle()
        self.assertEqual(self.page.records, [])
        self.assertTrue(self.window.story.empty.isVisible())
        self.window.story.create_scene_button.click()
        self.assertEqual(len(self.page.records), 1)
        self.assertEqual(self.page.records[0]['hearts'], 8)
        self.assertEqual(self.page.phases.currentIndex(), self.page.SCENE)
        self.assertTrue(self.page.editor.isVisible())
        self.assertTrue(self.window.story.empty.isHidden())

    def test_event_clock_steps_across_hours_and_midnight_without_invalid_minutes(self):
        clock = self.page.story_fields["time_start"]
        self.assertIsInstance(clock, EventTime)
        for initial, steps, expected in [(650, 1, 700), (700, -1, 650), (2350, 1, 2400), (2600, 1, 2600), (600, -1, 600)]:
            clock.setValue(initial)
            clock.stepBy(steps)
            self.assertEqual(clock.value(), expected)
            self.assertEqual(clock.text(), f"{expected // 100:02d}:{expected % 100:02d}")
