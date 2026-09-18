"""The beginner journey creates editable content and honest revision evidence."""
import copy
import tempfile
from pathlib import Path
import unittest

from pixelheart_core.creator import (
    ARCHETYPES, CreatorError, apply_proposal, build_proposal, content_fingerprint,
    creator_progress, export_is_current, new_brief, playtest_cases,
    record_export, record_install, record_playtest,
)
from pixelheart_core.projects import load_project, new_project, save_project
from pixelheart_core.story import compile_story, event_issues, exported_npc_id
from pixelheart_core.life import compile_life, life_issues
from pixelheart_core.world import cast_actor_id, exported_location_id, new_location, new_world, world_character, world_issues


class CreatorTests(unittest.TestCase):
    def setUp(self):
        self.document = new_project()
        self.document['character']['id'] = 'creator-test-project'
        self.brief = {**new_brief(), 'name': 'Rowan', 'concept': 'A traveller learns to stay.'}

    def create(self, **changes):
        brief = {**self.brief, **changes}
        return apply_proposal(self.document, build_proposal(self.document, brief))

    def ready(self, document):
        result = copy.deepcopy(document)
        for event in result['character']['events']:
            event['story']['stage'] = 'ready'
        return result

    def evidence(self, document):
        return record_install(record_export(document, '/tmp/mod.zip', b'archive'), '/tmp/Mods/Rowan', b'archive')

    def test_preview_never_mutates_and_has_real_authored_output(self):
        before = copy.deepcopy(self.document)
        proposal = build_proposal(self.document, self.brief)
        self.assertEqual(self.document, before)
        self.assertTrue(proposal['additions'])
        result = apply_proposal(self.document, proposal)
        self.assertEqual(self.document, before)
        character = result['character']
        self.assertEqual(character['name'], 'Rowan')
        self.assertEqual(len(character['dialogues']), 8)
        self.assertEqual({item['trigger'] for item in character['dialogues']}, {'Introduction', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'})
        self.assertEqual([event['hearts'] for event in character['events']], [0, 2, 4, 6, 8, 10, 12, 14])
        self.assertEqual([event['story']['stage'] for event in character['events']], ['scene'] * 8)
        for event in character['events']:
            self.assertEqual([actor['name'] for actor in event['story']['actors']], ['$npc', 'farmer'])
            self.assertTrue(event['story']['premise'])
            self.assertTrue(event['story']['conflict'])
            self.assertTrue(event['story']['outcome'])
            self.assertGreaterEqual(len(event['story']['beats']), 5)
            for beat in event['story']['beats']:
                if beat['kind'] == 'dialogue':
                    self.assertTrue(beat['text'].strip())
                    self.assertNotIn('{name}', beat['text'])
        self.assertEqual(compile_story(character), [])
        self.assertFalse([issue for issue in event_issues(character['events'][0], character) if issue['level'] == 'error'])

    def test_all_three_complete_arcs_compile_with_unique_authored_lines(self):
        introductions = set()
        for family in ARCHETYPES:
            character = self.ready(self.create(archetype=family))['character']
            introductions.add(character['dialogues'][0]['text'])
            errors = [issue for event in character['events'] for issue in event_issues(event, character) if issue['level'] == 'error']
            self.assertEqual(errors, [], family)
            patches = compile_story(character)
            entries = {key: text for patch in patches for key, text in patch['Entries'].items()}
            self.assertEqual(sum('/Friendship ' in key for key in entries), 8)
            self.assertEqual(sum('_choice_' in key and '/Friendship ' not in key for key in entries), 8)
            self.assertTrue(all('question fork0' in text for key, text in entries.items() if '/Friendship ' in key))
            self.assertEqual(len({event['story']['beats'][-1]['text'] for event in character['events']}), 8)
            self.assertFalse([issue for issue in life_issues(character) if issue['level'] == 'error'])
            self.assertTrue(compile_life(character))
        self.assertEqual(len(introductions), 3)

    def test_friendship_stops_at_eight_and_has_no_romance_or_spouse_content(self):
        result = self.create(relationship='friendship')
        character = result['character']
        self.assertFalse(character['romanceable'])
        self.assertEqual([event['hearts'] for event in character['events']], [0, 2, 4, 6, 8])
        self.assertEqual(character['life']['spouse_dialogue'], [])
        self.assertNotIn('marriage', {case['id'] for case in playtest_cases(result)})

    def test_brief_saved_while_typing_does_not_skip_first_time_creation(self):
        self.document['creator'] = {'version': 1, 'brief': {**self.brief, 'relationship': 'friendship', 'companion_name': 'Moss'}}
        result = self.create(relationship='friendship', companion_name='Moss')
        character = result['character']
        self.assertEqual(character['name'], 'Rowan')
        self.assertFalse(character['romanceable'])
        self.assertEqual(len(character['events']), 5)
        self.assertEqual(len(character['events'][0]['story']['actors']), 3)
        self.assertNotEqual(character['dialogues'][0]['text'], "Hello! It's lovely to meet you.$h")

    def test_repeated_proposal_preserves_all_authoring_and_stable_references(self):
        result = self.create()
        first_event = result['character']['events'][0]
        first_event['name'] = 'My first chapter'
        first_event['story']['beats'][0]['text'] = 'My own line, kept exactly.'
        first_event['story']['stage'] = 'ready'
        result['character']['life']['dialogues'][0]['text'] = 'My own season line.'
        second = build_proposal(result, self.brief)
        self.assertEqual(second['additions'], [])
        self.assertEqual(second['document'], result)
        self.assertEqual(apply_proposal(result, second), result)
        self.assertTrue(second['preserved'])
        events = result['character']['events']
        self.assertEqual([event['story']['previous_event_id'] for event in events], [''] + [event['id'] for event in events[:-1]])

    def test_missing_chapter_can_be_restored_without_overwriting_other_edits(self):
        result = self.create()
        removed = result['character']['events'].pop(3)
        result['character']['events'][0]['name'] = 'My edited meeting'
        restored = apply_proposal(result, build_proposal(result, self.brief))
        self.assertEqual(len(restored['character']['events']), 8)
        by_id = {event['id']: event for event in restored['character']['events']}
        self.assertEqual(by_id[removed['id']], removed)
        self.assertEqual(restored['character']['events'][0]['name'], 'My edited meeting')

    def test_existing_project_identity_dialogue_schedule_gifts_never_replaced(self):
        self.document['character'].update(name='Ada', internal_name='Ada', bio='Keep my life story.', romanceable=False)
        self.document['character']['dialogues'][0]['text'] = 'My introduction.'
        self.document['character']['schedule'][0]['x'] = 18
        self.document['character']['gifts']['love'] = ['Coffee']
        result = self.create()
        self.assertEqual(result['character']['name'], 'Ada')
        self.assertEqual(result['character']['bio'], 'Keep my life story.')
        self.assertFalse(result['character']['romanceable'])
        self.assertEqual(result['character']['dialogues'][0]['text'], 'My introduction.')
        self.assertEqual(result['character']['schedule'], self.document['character']['schedule'])
        self.assertEqual(result['character']['gifts']['love'], ['Coffee'])
        self.assertEqual(len(result['character']['events']), 5)

    def test_existing_name_preserves_its_internal_name_even_if_it_looks_like_a_default(self):
        self.document['character']['name'] = 'Ada'
        result = self.create()
        self.assertEqual(result['character']['name'], 'Ada')
        self.assertEqual(result['character']['internal_name'], 'NewCharacter')

    def test_existing_gift_ids_are_not_added_again_as_names_in_another_taste(self):
        self.document['character']['gifts']['hate'] = ['(O)395']
        result = self.create()
        self.assertEqual(result['character']['gifts']['hate'], ['(O)395'])
        self.assertNotIn('Coffee', result['character']['gifts']['love'])

    def test_over_capacity_preview_is_rejected_without_losing_existing_content(self):
        self.document['character']['dialogues'] = [{'id': str(index), 'trigger': 'custom_' + str(index), 'text': 'My writing.'} for index in range(250)]
        before = copy.deepcopy(self.document)
        with self.assertRaisesRegex(CreatorError, '250-dialogues'):
            build_proposal(self.document, self.brief)
        self.assertEqual(self.document, before)

    def test_changed_project_rejects_a_stale_preview(self):
        proposal = build_proposal(self.document, self.brief)
        self.document['character']['bio'] = 'Written since preview.'
        with self.assertRaisesRegex(CreatorError, 'changed after'):
            apply_proposal(self.document, proposal)
        self.assertEqual(self.document['character']['bio'], 'Written since preview.')

    def test_template_names_render_as_inert_dialogue(self):
        result = self.ready(self.create(name='A/B "$action" [123]'))
        self.assertTrue(compile_story(result['character']))
        self.assertEqual(result['creator']['brief']['name'], 'A/B "$action" [123]')

    def test_optional_companion_has_real_dialogue_cast_and_own_asset_requirements(self):
        result = self.create(companion_name='Moss')
        companion = result['world']['characters'][0]
        npc = companion['character']
        self.assertEqual(companion['id'], npc['id'])
        self.assertFalse(npc['romanceable'])
        self.assertEqual(npc['age'], 'adult')
        self.assertEqual(len(npc['dialogues']), 8)
        self.assertIsNone(companion['artwork']['portrait'])
        first = result['character']['events'][0]
        self.assertIn(cast_actor_id(companion), [actor['name'] for actor in first['story']['actors']])
        self.assertTrue(compile_story(self.ready(result)['character']))
        repeated = build_proposal(result, {**self.brief, 'companion_name': 'Moss'})
        self.assertEqual(result, repeated['document'])
        self.assertEqual(repeated['additions'], [])

    def test_companion_scene_reference_survives_renaming_and_blocks_deleted_cast(self):
        result = self.create(companion_name='Moss')
        companion = result['world']['characters'][0]
        stable_actor = cast_actor_id(companion)
        companion['character']['internal_name'] = 'MossRenamed'
        prepared = world_character(result['character'], result['world'])
        resolved_actor = exported_npc_id(companion['character'])
        self.assertIn(resolved_actor, [actor['name'] for actor in prepared['events'][0]['story']['actors']])
        self.assertIn(stable_actor, [actor['name'] for actor in result['character']['events'][0]['story']['actors']])
        result['world']['characters'].clear()
        issues = world_issues(result['world'], result['character'])
        self.assertTrue(any(issue['level'] == 'error' and 'supporting character' in issue['message'].lower() for issue in issues), issues)

    def test_proposal_preserves_existing_world_assets_dependencies_and_custom_metadata(self):
        from pixelheart_core.world import new_companion, new_location, new_world
        companion = new_companion('An existing friend')
        companion['artwork'] = {'portrait': 'artwork/friend.png', 'sprite': 'artwork/friend-sprite.png'}
        companion['character']['dialogues'][0]['text'] = 'My carefully written introduction.'
        place = new_location()
        place.update(name='My studio', internal_name='MyStudio', map='world/my-studio/map.tmx', custom={'author': 'Keep this'})
        self.document['world'] = {**new_world(), 'characters': [companion], 'locations': [place],
            'dependencies': [{'id': 'Example.Framework', 'minimum_version': '1.0.0', 'required': True}],
            'custom': {'keep': [1, 2, 3]}}
        before = copy.deepcopy(self.document['world'])
        result = self.create(companion_name='Moss')
        self.assertEqual(result['world']['characters'][0], before['characters'][0])
        self.assertEqual(result['world']['locations'], before['locations'])
        self.assertEqual(result['world']['dependencies'], before['dependencies'])
        self.assertEqual(result['world']['custom'], before['custom'])
        self.assertEqual(self.document['world'], before)
        self.assertEqual(len(result['world']['characters']), 2)

    def test_boundary_tiles_keep_the_generated_cast_distinct(self):
        self.document['character'].update(home_x=1000, home_y=1000)
        result = self.create(companion_name='Moss')
        actors = result['character']['events'][0]['story']['actors']
        self.assertEqual(len({(actor['x'], actor['y']) for actor in actors}), 3)
        self.assertTrue(all(0 <= actor[axis] <= 1000 for actor in actors for axis in ('x', 'y')))

    def test_revision_evidence_is_distinct_and_does_not_change_content_hash(self):
        result = self.ready(self.create())
        fingerprint = content_fingerprint(result)
        exported = record_export(result, '/tmp/mod.zip', b'archive')
        self.assertNotIn('last_export', result['creator'])
        self.assertEqual(content_fingerprint(exported), fingerprint)
        self.assertTrue(export_is_current(exported, b'archive'))
        self.assertFalse(export_is_current(exported, b'wrong archive'))
        with self.assertRaisesRegex(CreatorError, 'Install'):
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
        with self.assertRaisesRegex(CreatorError, 'Export the current'):
            record_install(result, '/tmp/Mods/Rowan')
        with self.assertRaisesRegex(CreatorError, 'Install'):
            record_playtest(result, 'load', 'passed')

    def test_no_fake_completion_from_checkboxes_or_metadata(self):
        result = self.create()
        result['creator']['milestones'] = {'all': True}
        rows = {row['id']: row for row in creator_progress(result)}
        self.assertEqual(rows['brief']['status'], 'complete')
        self.assertNotEqual(rows['first_chapter']['status'], 'complete')
        self.assertNotEqual(rows['artwork']['status'], 'complete')
        self.assertNotEqual(rows['chapters']['status'], 'complete')
        self.assertNotEqual(rows['export']['status'], 'complete')
        ready = self.ready(result)
        self.assertEqual(next(row for row in creator_progress(ready) if row['id'] == 'chapters')['status'], 'complete')
        ready['character']['events'].pop()
        self.assertNotEqual(next(row for row in creator_progress(ready) if row['id'] == 'chapters')['status'], 'complete')

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
        result = record_playtest(self.evidence(self.ready(self.create(companion_name='Moss'))), 'load', 'passed', 'No errors.')
        with tempfile.TemporaryDirectory() as folder:
            target = save_project(result, Path(folder) / 'character.json')
            loaded = load_project(target)
        self.assertEqual(loaded['creator'], result['creator'])
        self.assertEqual(content_fingerprint(loaded), content_fingerprint(result))
        self.assertTrue(export_is_current(loaded, b'archive'))
        self.assertEqual(next(case for case in playtest_cases(loaded) if case['id'] == 'load')['status'], 'passed')

    def test_invalid_brief_and_evidence_fail_without_mutation(self):
        before = copy.deepcopy(self.document)
        for changed in ({'name': ''}, {'archetype': 'missing'}, {'relationship': 'unknown'}, {'companion_name': 'Rowan'}):
            with self.assertRaises(CreatorError):
                build_proposal(self.document, {**self.brief, **changed})
        with self.assertRaises(CreatorError):
            record_export(self.document, '/tmp/a.zip', b'')
        with self.assertRaises(CreatorError):
            record_install(record_export(self.document, '/tmp/a.zip', b'a'), '/tmp/Mods/A', b'b')
        with self.assertRaises(CreatorError):
            record_playtest(self.document, 'nonexistent', 'passed')
        with self.assertRaises(CreatorError):
            record_playtest(self.document, 'load', 'invented')
        self.assertEqual(self.document, before)


if __name__ == '__main__':
    unittest.main()
