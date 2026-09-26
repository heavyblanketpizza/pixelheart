"""Install and playtest the authored NPC from Review & export."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart_core.playtesting import content_fingerprint, export_is_current, playtest_cases
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_beat, new_event
from tests.qt_support import QtTestCase


class PlaytestDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pixelheart-playtest-test-')
        self.root = Path(self.temporary.name)
        self.project_file = self.root / 'project' / 'character.json'
        self.windows = []
        self.errors_patch = patch.object(MainWindow, 'show_error')
        self.errors = self.errors_patch.start()
        self.window = self.make_window()

    def make_window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def tearDown(self):
        for window in reversed(self.windows):
            window.dirty = False
            window.close()
            window.deleteLater()
        self.application.processEvents()
        self.errors_patch.stop()
        self.temporary.cleanup()

    def create(self):
        document = new_project()
        character = document['character']
        character.update(name='Rowan', internal_name='Rowan')
        for index in range(2):
            event = new_event(character, 'first_meeting')
            event.update(name=f'Chapter {index + 1}', hearts=index * 2)
            event['story'].update(stage='scene', beats=[{**new_beat(), 'text': 'Welcome to the valley.$h'}])
            if index:
                event['story']['previous_event_id'] = character['events'][0]['id']
            character['events'].append(event)
        self.window.load_document(document)
        return self.window.playtest

    def artwork(self):
        self.assertTrue(self.window.save_to(self.project_file))
        for kind, dimensions in [('portrait', (128, 192)), ('sprite', (64, 416))]:
            path = self.root / (kind + '.png')
            with Image.new('RGBA', dimensions, (150, 90, 130, 255)) as image:
                image.save(path)
            with patch('pixelheart.artwork_page.QFileDialog.getOpenFileName', return_value=(str(path), '')):
                self.window.artwork.upload(kind)
        self.errors.assert_not_called()

    def export(self):
        path = self.root / 'Rowan.zip'
        with patch('pixelheart.app.QFileDialog.getSaveFileName', return_value=(str(path), '')), \
             patch('pixelheart.app.QMessageBox.information'):
            self.assertTrue(self.window.export_project(), self.window.validate_project())
        self.errors.assert_not_called()
        return path

    def test_first_chapter_exports_installs_and_records_actual_revision_without_later_drafts(self):
        playtest = self.create()
        self.window.events.list.setCurrentRow(0)
        self.window.events.mark_ready()
        self.assertEqual(self.window.events.records[0]['story']['stage'], 'ready')
        self.assertEqual(self.window.events.records[1]['story']['stage'], 'scene')
        self.artwork()
        archive = self.export()
        self.assertTrue(export_is_current(self.window.document, archive.read_bytes()))
        with zipfile.ZipFile(archive) as zipped:
            content_name = next(name for name in zipped.namelist() if name.endswith('/content.json'))
            content = json.loads(zipped.read(content_name))
        scenes = [entry for entry in content['Changes'] if entry.get('Target') == 'Data/Events/Town']
        self.assertEqual(sum('/Friendship ' in key for entry in scenes for key in entry['Entries']), 1)
        mods = self.root / 'Mods'
        mods.mkdir()
        with patch('pixelheart.playtest_page.QFileDialog.getExistingDirectory', return_value=str(mods)):
            playtest.install_mod()
        self.errors.assert_not_called()
        installed = self.window.document['creator']['last_install']
        self.assertTrue(Path(installed['path']).is_dir())
        self.assertEqual(installed['fingerprint'], content_fingerprint(self.window.document))
        playtest.refresh_testing()
        playtest.tests.setCurrentRow(0)
        self.assertTrue(playtest.pass_button.isEnabled())
        playtest.test_notes.setPlainText('Observed clean startup in the test save.')
        playtest.save_test('passed')
        self.assertEqual(self.window.document['creator']['tests']['load']['status'], 'passed')
        self.assertTrue(self.window.save())
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.project_file))
        self.assertTrue(export_is_current(reopened.document, archive.read_bytes()))
        self.assertEqual(reopened.document['creator']['tests']['load']['status'], 'passed')
        self.assertFalse(next(case for case in playtest_cases(reopened.document) if case['id'] == 'load')['stale'])
        reopened.dialogue.fields['text'].setPlainText('Changed for the next version.')
        reopened.playtest.refresh_testing()
        self.assertFalse(export_is_current(reopened.document))
        self.assertFalse(reopened.playtest.install_button.isEnabled())
        self.assertFalse(reopened.playtest.pass_button.isEnabled())
        self.assertTrue(next(case for case in playtest_cases(reopened.document) if case['id'] == 'load')['stale'])
        self.errors.assert_not_called()

    def test_observations_before_install_survive_navigation_and_save_without_passing_tests(self):
        playtest = self.create()
        playtest.tests.setCurrentRow(0)
        self.assertFalse(playtest.pass_button.isEnabled())
        playtest.test_notes.setPlainText('Need to ask a friend to check this on their save.')
        playtest.tests.setCurrentRow(1)
        playtest.tests.setCurrentRow(0)
        self.assertEqual(playtest.test_notes.toPlainText(), 'Need to ask a friend to check this on their save.')
        self.assertEqual(self.window.document['creator']['tests']['load']['status'], 'untested')
        self.assertTrue(self.window.save_to(self.project_file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.project_file))
        reopened.playtest.tests.setCurrentRow(0)
        self.assertEqual(reopened.playtest.test_notes.toPlainText(), 'Need to ask a friend to check this on their save.')
        self.assertFalse(reopened.playtest.pass_button.isEnabled())
        self.errors.assert_not_called()

    def test_playtest_repairs_open_the_related_editor_by_key(self):
        page = self.create()
        self.window.events.list.setCurrentRow(0)
        self.window.events.mark_ready()
        page.refresh()
        with patch.object(self.window, 'open_section', wraps=self.window.open_section) as open_section:
            page.tests.setCurrentRow(0)
            page.fix_test()
            open_section.assert_called_with('export')
            event_index = next(index for index, case in enumerate(page.cases) if case.get('event_id'))
            page.tests.setCurrentRow(event_index)
            page.fix_test()
            open_section.assert_called_with('story')
            self.assertEqual(self.window.events.list.currentRow(), 0)
        marriage_index = next(index for index, case in enumerate(page.cases) if case['id'] == 'marriage')
        page.tests.setCurrentRow(marriage_index)
        with patch.object(self.window, 'open_life_editor', wraps=self.window.open_life_editor) as open_life:
            page.fix_test()
            open_life.assert_called_once_with('spouse_dialogue')
        self.errors.assert_not_called()


if __name__ == '__main__':
    unittest.main()
