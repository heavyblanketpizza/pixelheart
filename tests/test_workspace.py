"""Cross-page routing and project boundaries in the single-NPC workspace."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import unittest
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart_core.projects import new_project
from tests.qt_support import QtTestCase


class WorkspaceTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def open_issue(self, field):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {'field': field})
        self.window.export_page.open_issue(item)

    def test_validation_opens_the_correct_embedded_editor(self):
        window = self.window
        for kind in ('dialogues', 'routines', 'spouse_dialogue'):
            window.life.editors[kind].add()
        for field, section, tabs, index in (
            ('life.routines.0.name', 'schedule', window.schedule_tabs, 1),
            ('life.spouse_dialogue.0.text', 'dialogue', window.dialogue_tabs, 2),
            ('life.dialogues.0.text', 'dialogue', window.dialogue_tabs, 1),
            ('dialogues.0.text', 'dialogue', window.dialogue_tabs, 0),
            ('schedule.0.time', 'schedule', window.schedule_tabs, 0),
        ):
            with self.subTest(field=field):
                self.open_issue(field)
                self.assertEqual(window.navigation.currentRow(), SECTION_INDEX[section])
                self.assertEqual(tabs.currentIndex(), index)

    def test_switching_projects_resets_context_without_changing_new_project(self):
        window = self.window
        window.identity.fields['name'].setText('First NPC')
        window.open_life_editor('spouse_dialogue')
        window.playtest.tests.setCurrentRow(0)
        window.playtest.test_notes.setPlainText('Notes only for the first NPC')
        window.open_section('export')
        window.export_page.tabs.setCurrentIndex(1)
        second = new_project()
        second['character']['name'] = 'Second NPC'
        original = deepcopy(second)
        window.load_document(second)
        self.assertEqual(window.navigation.currentRow(), SECTION_INDEX['identity'])
        self.assertEqual(window.dialogue_tabs.currentIndex(), 0)
        self.assertEqual(window.schedule_tabs.currentIndex(), 0)
        self.assertIn('SECOND NPC', window.breadcrumb.text())
        self.assertEqual(window.playtest.test_notes.toPlainText(), '')
        self.assertNotIn('creator', window.document)
        self.assertEqual(second, original)
        self.assertFalse(window.dirty)

    def test_all_editors_share_one_character_and_navigation_preserves_content(self):
        window = self.window
        identity = window.document['character']['id']
        window.identity.fields['name'].setText('One NPC')
        window.dialogue.fields['text'].setPlainText('My introduction.')
        for section in SECTION_INDEX:
            window.open_section(section)
        window.collect()
        self.assertEqual(window.document['character']['id'], identity)
        self.assertEqual(window.document['character']['name'], 'One NPC')
        self.assertEqual(window.document['character']['dialogues'][0]['text'], 'My introduction.')
        self.assertEqual(window.document['world']['characters'], [])
        self.assertFalse(hasattr(window, 'creator'))


if __name__ == '__main__':
    unittest.main()
