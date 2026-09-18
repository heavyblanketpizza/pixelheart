"""Exercise the actual desktop journey from a brief to an installed revision."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow
from pixelheart.creator_page import ProposalDialog
from pixelheart.theme import apply_theme
from pixelheart_core.creator import build_proposal, content_fingerprint, export_is_current, playtest_cases
from pixelheart_core.projects import load_project
from pixelheart_core.story import exported_npc_id


class CreatorDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pixelheart-creator-test-')
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

    def create(self, romance=True):
        creator = self.window.creator
        creator.fields['name'].setText('Rowan')
        creator.fields['concept'].setPlainText('A traveller learns that belonging is a shared task.')
        if not romance:
            creator.fields['relationship'].setCurrentIndex(1)
        with patch.object(ProposalDialog, 'exec', return_value=QDialog.DialogCode.Accepted):
            creator.preview_proposal()
        self.assertFalse(creator.brief_notice.isVisible(), creator.brief_notice.text())
        self.errors.assert_not_called()
        return creator

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

    def test_cancel_preview_keeps_authored_content_unchanged(self):
        self.window.creator.fields['name'].setText('Rowan')
        before = deepcopy(self.window.document)
        with patch.object(ProposalDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            self.window.creator.preview_proposal()
        self.assertEqual(self.window.document, before)
        self.assertEqual(self.window.events.records, [])

    def test_friendship_brief_creates_editable_chapters_and_preserves_ids_when_reapplied(self):
        creator = self.create(romance=False)
        self.assertFalse(self.window.document['character']['romanceable'])
        self.assertEqual(self.window.identity.fields['name'].text(), 'Rowan')
        self.assertEqual(len(self.window.events.records), 5)
        self.assertEqual(self.window.navigation.currentRow(), 7)
        self.assertEqual(creator.tabs.currentIndex(), 1)
        self.assertEqual(creator.chapters.count(), 5)
        self.window.events.fields['name'].setText('My own beginning')
        self.window.events.beats.fields['text'].setPlainText('A line I wrote myself.')
        self.window.collect()
        before = deepcopy(self.window.document)
        proposal = build_proposal(before, before['creator']['brief'])
        creator.accept_proposal(proposal)
        self.assertEqual(self.window.document['character']['events'], before['character']['events'])
        self.assertEqual(proposal['additions'], [])

    def test_first_chapter_exports_installs_and_records_actual_revision_without_later_drafts(self):
        creator = self.create()
        creator.chapters.setCurrentRow(0)
        creator.edit_chapter()
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
        with patch('pixelheart.creator_page.QFileDialog.getExistingDirectory', return_value=str(mods)):
            creator.install_mod()
        self.errors.assert_not_called()
        installed = self.window.document['creator']['last_install']
        self.assertTrue(Path(installed['path']).is_dir())
        self.assertEqual(installed['fingerprint'], content_fingerprint(self.window.document))
        creator.tabs.setCurrentIndex(3)
        creator.refresh_testing()
        creator.tests.setCurrentRow(0)
        self.assertTrue(creator.pass_button.isEnabled())
        creator.test_notes.setPlainText('Observed clean startup in the test save.')
        creator.save_test('passed')
        self.assertEqual(self.window.document['creator']['tests']['load']['status'], 'passed')
        self.assertTrue(self.window.save())
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.project_file))
        self.assertTrue(export_is_current(reopened.document, archive.read_bytes()))
        self.assertEqual(reopened.document['creator']['tests']['load']['status'], 'passed')
        self.assertFalse(next(case for case in playtest_cases(reopened.document) if case['id'] == 'load')['stale'])
        reopened.dialogue.fields['text'].setPlainText('Changed for the next version.')
        reopened.creator.refresh_testing()
        self.assertFalse(export_is_current(reopened.document))
        self.assertFalse(reopened.creator.install_button.isEnabled())
        self.assertFalse(reopened.creator.pass_button.isEnabled())
        self.assertTrue(next(case for case in playtest_cases(reopened.document) if case['id'] == 'load')['stale'])
        self.errors.assert_not_called()

    def test_all_chapters_can_be_reviewed_in_order_including_choices_and_marriage(self):
        self.create()
        page = self.window.events
        for index in range(8):
            page.list.setCurrentRow(index)
            page.mark_ready()
            self.assertEqual(page.records[index]['story']['stage'], 'ready', page.records[index]['name'])
        self.artwork()
        archive = self.export()
        self.assertTrue(archive.is_file())
        self.assertEqual(self.window.events.records[-1]['story']['relationship'], 'married')
        self.assertEqual(self.window.events.records[-1]['story']['beats'][-1]['kind'], 'choice')
        self.assertEqual(len([case for case in playtest_cases(self.window.document) if case.get('event_id')]), 8)
        self.errors.assert_not_called()

    def test_optional_companion_exports_under_renamed_id_and_deletion_blocks_export(self):
        creator = self.window.creator
        creator.load_example()
        self.window.collect()
        creator.accept_proposal(build_proposal(self.window.document, self.window.document['creator']['brief']))
        self.window.events.list.setCurrentRow(0)
        self.window.events.mark_ready()
        self.artwork()
        world = self.window.world
        world.cast_fields['internal_name'].setText('PipRenamed')
        for kind in ('portrait', 'sprite'):
            with patch('pixelheart.world_page.QFileDialog.getOpenFileName', return_value=(str(self.root / (kind + '.png')), '')):
                world.import_cast_artwork(kind)
        archive = self.export()
        companion = self.window.document['world']['characters'][0]['character']
        with zipfile.ZipFile(archive) as zipped:
            text = zipped.read(next(name for name in zipped.namelist() if name.endswith('/content.json'))).decode()
        self.assertIn(exported_npc_id(companion), text)
        self.assertNotIn('PixelheartCast.', text)
        world.remove_companion()
        issues = self.window.validate_project()
        self.assertTrue(any(issue['level'] == 'error' and 'supporting character' in issue['message'].lower() for issue in issues), issues)
        self.errors.assert_not_called()

    def test_observations_before_install_survive_navigation_and_save_without_passing_tests(self):
        creator = self.create()
        creator.tabs.setCurrentIndex(3)
        creator.tests.setCurrentRow(0)
        self.assertFalse(creator.pass_button.isEnabled())
        creator.test_notes.setPlainText('Need to ask a friend to check this on their save.')
        creator.tests.setCurrentRow(1)
        creator.tests.setCurrentRow(0)
        self.assertEqual(creator.test_notes.toPlainText(), 'Need to ask a friend to check this on their save.')
        self.assertEqual(self.window.document['creator']['tests']['load']['status'], 'untested')
        self.assertTrue(self.window.save_to(self.project_file))
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.project_file))
        reopened.creator.tabs.setCurrentIndex(3)
        reopened.creator.tests.setCurrentRow(0)
        self.assertEqual(reopened.creator.test_notes.toPlainText(), 'Need to ask a friend to check this on their save.')
        self.assertFalse(reopened.creator.pass_button.isEnabled())
        self.errors.assert_not_called()

    def test_preview_includes_choices_routines_spouse_content_and_optional_companion(self):
        creator = self.window.creator
        creator.load_example()
        self.window.collect()
        proposal = build_proposal(self.window.document, self.window.document['creator']['brief'])
        dialog = ProposalDialog(proposal, self.window)
        titles = [title for title, text in dialog.entries]
        text = '\n'.join(text for title, text in dialog.entries)
        self.assertIn('Gifts and daily routine', titles)
        self.assertTrue(any(title.startswith('Supporting cast') for title in titles))
        self.assertIn('Stay as long as you like.', text)
        self.assertIn('Return home to the farmhouse', '\n'.join(stop.get('activity', '') for rule in proposal['document']['character']['life']['routines'] for stop in rule['stops']))
        self.assertTrue(any('A morning together' in title for title in titles))
        dialog.deleteLater()
        creator.accept_proposal(proposal)
        self.assertTrue(self.window.save_to(self.project_file))
        saved = load_project(self.project_file)
        self.assertEqual(len(saved['world']['characters']), 1)
        self.assertEqual(saved['world']['characters'][0]['character']['name'], 'Pip')
        self.assertEqual(len(saved['character']['events'][0]['story']['actors']), 3)
        self.errors.assert_not_called()


if __name__ == '__main__':
    unittest.main()
