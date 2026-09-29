"""The focused Romance editor keeps old projects intact while authoring scenes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTabWidget

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart_core.projects import new_project, save_project, load_project
from pixelheart_core.romance import ROMANCE_HEARTS, new_romance_event
from pixelheart_core.story import compile_story, new_relationship
from pixelheart_core.story_planning import new_chapter
from tests.qt_support import QtTestCase


class RomanceUITests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.enterContext(patch('pixelheart.game_import.game_import_settings', return_value=settings))
        self.enterContext(patch('pixelheart.interior_editor.game_import_settings', return_value=settings))
        self.enterContext(patch('pixelheart.story_page.EventsPage.update_scene_preview'))
        self.window = MainWindow(auto_download_icons=False)
        self.addCleanup(self.close_window)
        self.errors = self.enterContext(patch.object(self.window, 'show_error'))
        self.window.open_section('story')
        self.page = self.window.story

    def close_window(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def legacy_document(self):
        document = new_project()
        character = document['character']
        character.update(name='Mira', internal_name='Mira')
        first = new_romance_event(character, 2)
        first.update(name='First part', extension={'source': ['keep', {'draft': True}]})
        first['story'].update(premise='An existing premise', aftermath_notes='Existing aftermath',
                              before='Before', after='After', motif='Keep this motif')
        first['story']['beats'][0]['annotation'] = {'color': 'blue'}
        second = new_romance_event(character, 2)
        second['name'] = 'Second part'
        second['story']['previous_event_id'] = first['id']
        odd = new_romance_event(character, 4)
        odd.update(hearts=5, name='Legacy five-heart scene')
        six = new_romance_event(character, 6)
        arc = new_relationship()
        arc.update(name='An existing relationship', extension={'keep': 'arc extension'})
        arc['story'].update(desire='Keep their independent story', motif='Old arc motif')
        first['story']['arc_ids'] = [arc['id']]
        chapter = new_chapter('An existing chapter')
        chapter.update(event_ids=[first['id'], second['id']], arc_ids=[arc['id']], purpose='Keep this chapter writing')
        character['events'] = [odd, first, six, second]
        character['relationships'] = [arc]
        character['storyline']['chapters'] = [chapter]
        character['storyline']['brief']['desire'] = 'Keep the character brief'
        character['storyline']['extension'] = {'private': 'existing planning metadata'}
        document['extension'] = {'unrelated': [1, 2, 3]}
        return document

    def test_new_desktop_project_has_six_blank_events_and_only_three_scene_tabs(self):
        character = self.window.project_snapshot()['character']
        self.assertEqual(tuple(event['hearts'] for event in character['events']), ROMANCE_HEARTS)
        self.assertEqual(character['relationships'], [])
        self.assertEqual(character['storyline']['chapters'], [])
        self.assertTrue(all(event['story']['stage'] == 'outline' for event in character['events']))
        self.assertTrue(all(not event['story']['premise'] for event in character['events']))
        self.assertEqual(self.page.selected_hearts, 2)
        self.assertEqual([self.page.events.phases.tabText(i) for i in range(self.page.events.phases.count())],
                         ['When', 'Scene', 'Preview'])
        labels = [tabs.tabText(i) for tabs in self.page.findChildren(QTabWidget) for i in range(tabs.count())]
        self.assertFalse({'Purpose', 'Aftermath', 'Storyline', 'Relationships'}.intersection(labels))
        for old_ui in ('arc_links', 'aftermath'):
            self.assertFalse(hasattr(self.page.events, old_ui), old_ui)
        for key in ('premise', 'conflict', 'outcome', 'before', 'after', 'motif', 'relationship_id'):
            self.assertNotIn(key, self.page.events.story_fields)
        self.assertFalse(self.window.dirty)

    def test_minimum_window_keeps_dialogue_input_and_stage_in_view(self):
        from pixelheart.theme import apply_theme
        apply_theme(self.app)
        self.window.show()
        for width, height in ((1360, 900), (1020, 700)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                for _ in range(6):
                    self.app.processEvents()
                events = self.page.events
                scroll = events.phases.currentWidget()
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                words = events.beats.fields['text']
                viewport = events.beats.details_scroll.viewport()
                self.assertTrue(viewport.rect().contains(words.mapTo(viewport, words.rect().topLeft())))
                self.assertTrue(viewport.rect().contains(words.mapTo(viewport, words.rect().bottomRight())))
                self.assertTrue(events.actors.canvas.isVisible())

    def test_milestone_navigation_does_not_edit_and_targets_the_chosen_scene(self):
        before = self.window.project_snapshot()
        self.page.milestones.buttons[6].click()
        event = self.page.events.records[self.page.events.current]
        self.assertEqual(self.page.selected_hearts, 6)
        self.assertEqual(event['hearts'], 6)
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertEqual(self.window.project_history.history.undo_count, 0)
        self.page.events.fields['name'].setText('A six-heart conversation')
        edited = self.window.project_snapshot()['character']['events']
        self.assertEqual(next(row for row in edited if row['id'] == event['id'])['name'], 'A six-heart conversation')
        self.assertEqual([row for row in edited if row['id'] != event['id']],
                         [row for row in before['character']['events'] if row['id'] != event['id']])
        self.page.milestones.buttons[10].click()
        self.assertEqual(self.page.events.records[self.page.events.current]['story']['relationship'], 'dating')
        self.page.milestones.buttons[14].click()
        self.assertEqual(self.page.events.records[self.page.events.current]['story']['relationship'], 'married')

    def test_legacy_multipart_and_other_hearts_keep_all_data_and_flat_order(self):
        document = self.legacy_document()
        self.window.load_document(document)
        before = self.window.project_snapshot()
        self.page.milestones.buttons[2].click()
        ids = [self.page.parts.itemData(i) for i in range(self.page.parts.count())]
        expected = [event['id'] for event in document['character']['events'] if event['hearts'] == 2]
        self.assertEqual(ids, expected)
        self.page.parts.setCurrentIndex(1)
        self.assertEqual(self.page.events.records[self.page.events.current]['id'], expected[1])
        odd = document['character']['events'][0]
        self.page.open_event(odd['id'])
        self.assertEqual(self.page.events.records[self.page.events.current]['id'], odd['id'])
        self.assertEqual(self.page.events.records[self.page.events.current]['hearts'], 5)
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertEqual(before['character']['events'], document['character']['events'])
        self.assertEqual(before['character']['relationships'], document['character']['relationships'])
        self.assertEqual(before['character']['storyline'], document['character']['storyline'])
        self.assertEqual(before['extension'], document['extension'])

    def test_saved_empty_project_stays_empty_until_explicit_add_at_selected_hearts(self):
        document = new_project()
        path = self.root / 'saved-empty' / 'character.json'
        save_project(document, path)
        self.assertTrue(self.window.open_path(path))
        self.assertEqual(self.page.events.records, [])
        before = self.window.project_snapshot()
        self.page.milestones.buttons[14].click()
        self.assertEqual(self.page.events.records, [])
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertFalse(self.window.dirty)
        self.page.create_scene_button.click()
        self.assertEqual(len(self.page.events.records), 1)
        self.assertEqual(self.page.events.records[0]['hearts'], 14)
        self.assertEqual(self.page.events.records[0]['story']['relationship'], 'married')
        self.assertEqual(self.window.project_snapshot()['character']['storyline']['chapters'], [])
        self.assertEqual(self.window.project_snapshot()['character']['relationships'], [])
        first_id = self.page.events.records[0]['id']
        self.page.add_scene_button.click()
        self.assertEqual([event['hearts'] for event in self.page.events.records], [14, 14])
        self.assertEqual(self.page.events.records[0]['id'], first_id)
        self.assertNotEqual(self.page.events.records[1]['id'], first_id)
        self.assertEqual(self.page.parts.count(), 2)
        self.window.project_history.undo()
        self.assertEqual([event['id'] for event in self.page.events.records], [first_id])
        self.assertEqual(self.page.selected_hearts, 14)

    def test_blank_draft_becomes_ready_through_editor_and_round_trips(self):
        events = self.page.events
        self.assertFalse(events.mark_ready())
        self.page.milestones.buttons[4].click()
        events.phases.setCurrentIndex(events.SCENE)
        events.beats.fields['text'].setPlainText('I saved this moment for you, @.$h')
        self.assertTrue(events.mark_ready())
        ready = deepcopy(events.records[events.current])
        self.assertEqual(ready['hearts'], 4)
        self.assertEqual(ready['story']['stage'], 'ready')
        patches = compile_story(self.page.character())
        self.assertEqual(len(patches), 1)
        self.assertIn('I saved this moment for you', str(patches))
        path = self.root / 'ready-project' / 'character.json'
        self.assertTrue(self.window.save_to(path))
        saved = load_project(path)
        self.assertEqual(next(event for event in saved['character']['events'] if event['id'] == ready['id']), ready)
        self.assertTrue(self.window.open_path(path))
        self.page.open_event(ready['id'])
        events.fields['name'].setText('A revised title')
        self.assertEqual(events.records[events.current]['story']['stage'], 'scene')
        self.assertEqual(compile_story(self.page.character()), [])
        self.errors.assert_not_called()

    def test_project_undo_preserves_milestone_selection_and_legacy_story_metadata(self):
        self.window.load_document(self.legacy_document())
        self.window.open_section('story')
        self.page.milestones.buttons[6].click()
        baseline = self.window.project_snapshot()
        event_id = self.page.events.records[self.page.events.current]['id']
        self.page.events.fields['name'].setText('Changed six-heart scene')
        edited = self.window.project_snapshot()
        self.assertEqual(self.window.project_history.history.undo_count, 1)
        self.window.project_history.undo()
        self.assertEqual(self.window.project_snapshot(), baseline)
        self.assertEqual(self.window.stack.currentIndex(), SECTION_INDEX['story'])
        self.assertEqual(self.page.selected_hearts, 6)
        self.assertEqual(self.page.events.records[self.page.events.current]['id'], event_id)
        self.window.project_history.redo()
        self.assertEqual(self.window.project_snapshot(), edited)
        self.assertEqual(self.page.selected_hearts, 6)
        self.assertEqual(self.page.character()['relationships'], baseline['character']['relationships'])
        self.assertEqual(self.page.character()['storyline'], baseline['character']['storyline'])


if __name__ == '__main__':
    import unittest
    unittest.main()
