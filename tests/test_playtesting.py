"""Release evidence follows the authored NPC content and survives project saves."""
import copy
import tempfile
from pathlib import Path
import unittest

from pixelheart_core.playtesting import (
    PlaytestError, content_fingerprint, export_is_current, playtest_cases,
    record_export, record_install, record_playtest,
)
from pixelheart_core.projects import load_project, new_project, save_project
from pixelheart_core.story import new_beat, new_event
from pixelheart_core.world import exported_location_id, new_location, new_world


class PlaytestingTests(unittest.TestCase):
    def setUp(self):
        self.document = new_project()
        self.document['character'].update(id='playtest-project', name='Rowan', internal_name='Rowan')

    def create(self):
        document = copy.deepcopy(self.document)
        character = document['character']
        previous = ''
        for index, hearts in enumerate((0, 2, 4, 6, 8, 10, 12, 14)):
            event = new_event(character)
            event.update(name=f'Chapter {index + 1}', hearts=hearts)
            event['story'].update(stage='scene', previous_event_id=previous,
                                  relationship='married' if hearts >= 12 else 'dating' if hearts == 10 else 'any',
                                  time_start=900, beats=[{**new_beat(), 'text': 'An authored scene.'}])
            character['events'].append(event)
            previous = event['id']
        # Older projects may still contain the removed wizard's planning metadata.
        document['creator'] = {'version': 1, 'brief': {'name': 'Rowan'}, 'chapters': []}
        return document

    def ready(self, document):
        result = copy.deepcopy(document)
        for event in result['character']['events']:
            event['story']['stage'] = 'ready'
        return result

    def evidence(self, document):
        return record_install(record_export(document, '/tmp/mod.zip', b'archive'), '/tmp/Mods/Rowan', b'archive')

    def test_revision_evidence_is_distinct_and_does_not_change_content_hash(self):
        result = self.ready(self.create())
        fingerprint = content_fingerprint(result)
        exported = record_export(result, '/tmp/mod.zip', b'archive')
        self.assertNotIn('last_export', result['creator'])
        self.assertEqual(content_fingerprint(exported), fingerprint)
        self.assertTrue(export_is_current(exported, b'archive'))
        self.assertFalse(export_is_current(exported, b'wrong archive'))
        with self.assertRaisesRegex(PlaytestError, 'Install'):
            record_playtest(exported, 'load', 'passed')
        installed = record_install(exported, '/tmp/Mods/Rowan', b'archive')
        self.assertEqual(content_fingerprint(installed), fingerprint)
        tested = record_playtest(installed, 'load', 'passed', 'No pack errors in SMAPI.')
        self.assertEqual(content_fingerprint(tested), fingerprint)
        self.assertEqual(next(case for case in playtest_cases(tested) if case['id'] == 'load')['status'], 'passed')
        self.assertNotIn('tests', installed['creator'])
        self.assertEqual(tested['creator']['last_export']['sha256'], tested['creator']['last_install']['sha256'])

    def test_recording_an_export_on_an_existing_project_keeps_revision_current(self):
        self.assertNotIn('creator', self.document)
        exported = record_export(self.document, '/tmp/a.zip', b'archive')
        self.assertEqual(content_fingerprint(self.document), content_fingerprint(exported))
        self.assertTrue(export_is_current(exported, b'archive'))

    def test_content_edits_invalidate_export_install_and_old_test_results(self):
        result = record_playtest(self.evidence(self.ready(self.create())), 'load', 'passed', 'Works.')
        result['character']['events'][0]['story']['beats'][0]['text'] += ' Changed.'
        self.assertFalse(export_is_current(result))
        case = next(case for case in playtest_cases(result) if case['id'] == 'load')
        self.assertTrue(case['stale'])
        self.assertEqual(case['status'], 'untested')
        self.assertEqual(case['previous_status'], 'passed')
        self.assertEqual(case['notes'], 'Works.')
        with self.assertRaisesRegex(PlaytestError, 'Export the current'):
            record_install(result, '/tmp/Mods/Rowan')
        with self.assertRaisesRegex(PlaytestError, 'Install'):
            record_playtest(result, 'load', 'passed')

    def test_playtest_cases_follow_ready_content_and_include_actual_triggers(self):
        result = self.create()
        self.assertFalse(any(case['id'].startswith('event:') for case in playtest_cases(result)))
        ready = self.ready(result)
        cases = [case for case in playtest_cases(ready) if case['id'].startswith('event:')]
        self.assertEqual(len(cases), 8)
        self.assertIn('Town', cases[0]['instructions'])
        self.assertIn('09:00', cases[0]['instructions'])
        self.assertNotIn('{{ModId}}', cases[0]['instructions'])
        self.assertIn('married', cases[-1]['instructions'])
        self.assertIn(ready['character']['events'][-2]['name'], cases[-1]['instructions'])
        self.assertEqual(cases[0]['event_id'], ready['character']['events'][0]['id'])

    def test_playtest_instructions_include_house_and_unmarried_conditions_and_daily_reset(self):
        result = self.create()
        event = result['character']['events'][0]
        event['story'].update(stage='ready', relationship='unmarried', min_house_upgrade=2,
                              repeat='daily', season='fall', weather='rainy', time_start=1230, time_end=1740)
        instructions = next(case['instructions'] for case in playtest_cases(result) if case.get('event_id') == event['id'])
        for fragment in ('at least 0 hearts', '12:30', '17:40', 'Season: fall', 'Weather: rainy',
                         'must not be married to this character', 'marriage to someone else',
                         'upgrade level 2 or higher', 'Event Repeater 6.5.8', 'same day without reloading',
                         'Sleep to the next day', 'Reload a saved game', 'effects can be earned on every replay'):
            self.assertIn(fragment, instructions)
        self.assertNotIn('This scene is one-time', instructions)

    def test_one_time_playtest_remains_completed_after_sleep_and_saved_reload(self):
        result = self.ready(self.create())
        cases = [case for case in playtest_cases(result) if case.get('event_id')]
        self.assertIn('This scene is one-time', cases[0]['instructions'])
        self.assertIn('then sleep and re-enter: it should not play again', cases[0]['instructions'])
        self.assertIn('saved that completion, reload that save', cases[0]['instructions'])
        self.assertNotIn('Event Repeater', cases[0]['instructions'])
        self.assertIn('must be dating this character', cases[5]['instructions'])
        self.assertIn('must be married to this character', cases[6]['instructions'])

    def test_custom_map_playtests_show_the_exact_game_id_for_home_scene_and_entrance(self):
        result = self.ready(self.create())
        home = {**new_location(), 'name': 'The quiet studio', 'internal_name': 'Studio'}
        inner = {**new_location(), 'name': 'The back garden', 'internal_name': 'Garden',
                 'entrance': {'map': 'Studio', 'x': 4, 'y': 7, 'arrival_x': 4, 'arrival_y': 8}}
        result['world'] = {**new_world(), 'locations': [home, inner]}
        result['character']['home_map'] = 'Studio'
        result['character']['events'][0]['location'] = 'Studio'
        cases = {case['id']: case for case in playtest_cases(result)}
        home_id = exported_location_id(home, result['character'])
        garden_id = exported_location_id(inner, result['character'])
        expected = f'The quiet studio (game map: {home_id})'
        self.assertIn(expected, cases['meet']['instructions'])
        self.assertIn(expected, cases['event:' + result['character']['events'][0]['id']]['instructions'])
        self.assertIn(expected, cases['location:' + inner['id']]['instructions'])
        self.assertIn(garden_id, cases['location:' + inner['id']]['instructions'])
        result['character']['home_map'] = home_id
        self.assertIn(expected, next(case['instructions'] for case in playtest_cases(result) if case['id'] == 'meet'))

    def test_export_install_and_test_records_roundtrip_with_projects(self):
        result = record_playtest(self.evidence(self.ready(self.create())), 'load', 'passed', 'No errors.')
        with tempfile.TemporaryDirectory() as folder:
            target = save_project(result, Path(folder) / 'character.json')
            loaded = load_project(target)
        self.assertEqual(loaded['creator'], result['creator'])
        self.assertEqual(content_fingerprint(loaded), content_fingerprint(result))
        self.assertTrue(export_is_current(loaded, b'archive'))
        self.assertEqual(next(case for case in playtest_cases(loaded) if case['id'] == 'load')['status'], 'passed')

    def test_legacy_metadata_is_preserved_when_recording_release_evidence(self):
        document = self.create()
        legacy = copy.deepcopy(document['creator'])
        tested = record_playtest(self.evidence(document), 'load', 'passed', 'Looks right.')
        for key, value in legacy.items():
            self.assertEqual(tested['creator'][key], value)
        self.assertEqual(document['creator'], legacy)

    def test_invalid_evidence_fails_without_mutation(self):
        before = copy.deepcopy(self.document)
        operations = [
            lambda: record_export(self.document, '/tmp/a.zip', b''),
            lambda: record_export(self.document, '', b'archive'),
            lambda: record_install(record_export(self.document, '/tmp/a.zip', b'a'), '/tmp/Mods/A', b'b'),
            lambda: record_install(record_export(self.document, '/tmp/a.zip', b'a'), ''),
            lambda: record_playtest(self.document, 'nonexistent', 'passed'),
            lambda: record_playtest(self.document, 'load', 'invented'),
            lambda: record_playtest(self.document, 'load', 'untested', 'a' * 8001),
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(PlaytestError):
                    operation()
        self.assertEqual(self.document, before)

    def test_nonromance_project_has_no_marriage_check(self):
        self.document['character']['romanceable'] = False
        self.assertNotIn('marriage', {case['id'] for case in playtest_cases(self.document)})


if __name__ == '__main__':
    unittest.main()
