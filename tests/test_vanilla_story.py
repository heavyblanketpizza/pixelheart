"""Synthetic event assets only; no vanilla game text or artwork is bundled."""
from copy import deepcopy
from pathlib import Path
import struct
import tempfile
import unittest

from tests.test_game_scene_assets import seven, string, i32


def dictionary_xnb(mapping, *, reader_index=2):
    readers = ['Microsoft.Xna.Framework.Content.DictionaryReader`2[[System.String, mscorlib],[System.String, mscorlib]], MonoGame.Framework',
               'Microsoft.Xna.Framework.Content.StringReader, MonoGame.Framework']
    data = seven(len(readers)) + b''.join(string(r) + i32(0) for r in readers) + b'\x00\x01'
    data += i32(len(mapping))
    for key, value in mapping.items():
        data += seven(reader_index) + string(key) + seven(reader_index) + string(value)
    return b'XNBw\x05\x00' + struct.pack('<I', len(data) + 10) + data


def game_fixture(root, assets):
    root = Path(root)
    (root / 'Maps').mkdir(parents=True, exist_ok=True)
    (root / 'Maps/Town.xnb').write_bytes(b'placeholder; map is not decoded by this loader')
    (root / 'Data/Events').mkdir(parents=True, exist_ok=True)
    for location, rows in assets.items():
        (root / 'Data/Events' / (location + '.xnb')).write_bytes(dictionary_xnb(rows))
    return root

from pixelheart_core.vanilla_story import (
    VANILLA_NPCS, VanillaStoryError, load_vanilla_story, apply_vanilla_story, clear_vanilla_cache,
    preview_vanilla_initialization, initialize_vanilla_story,
)
from pixelheart_core.xnb_preview import string_dictionary, decode_xnb, XnbError
from pixelheart_core.projects import new_project, untouched_story_starter
from pixelheart_core.story import event_issues, compile_story


def scene(name='Abigail', commands='speak Abigail "Good morning."/end', *, x=5):
    return f'none/5 6/{name} {x} 6 2 farmer 5 8 0/{commands}'


class VanillaStoryTests(unittest.TestCase):
    def setUp(self):
        clear_vanilla_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def load(self, assets, npc='abigail'):
        return load_vanilla_story(npc, game_fixture(self.root, assets))

    def test_string_dictionary_preserves_unicode_and_reader_api(self):
        value = {'one/key': 'A / B “雪”', 'two': ''}
        payload = dictionary_xnb(value)
        self.assertEqual(string_dictionary(payload), value)
        self.assertTrue(decode_xnb(payload)[0].startswith('Microsoft.Xna.Framework.Content.DictionaryReader'))
        with self.assertRaises(XnbError):
            string_dictionary(dictionary_xnb(value, reader_index=1))
        with self.assertRaises(XnbError):
            string_dictionary(payload, maximum=1)
        with self.assertRaises(XnbError):
            string_dictionary(payload[:-1])

    def test_all_twelve_exact_gates_and_only_genuine_cast_appearances(self):
        self.assertEqual(len(VANILLA_NPCS), 12)
        for slug, npc in VANILLA_NPCS.items():
            name = npc['name']
            bundle = self.load({'Town': {f'one/f {name} 500': scene(name, f'speak {name} "Hi."/end'),
                                          'other': scene('Robin', f'speak Robin "{name} is absent."/end')}}, slug)
            self.assertEqual(len(bundle['events']), 1)
            self.assertEqual(bundle['events'][0]['hearts'], 2)
            self.assertFalse(bundle['events'][0]['optional'])

    def test_quotes_slashes_and_conditional_fork_are_preserved_without_flattening(self):
        original = scene(commands='speak Abigail "One / two."/animate Abigail false true 100 0 1/speak Abigail "After pose."/fork flag path/speak Abigail "Conditional tail."/end')
        branch = 'speak Abigail "Branch only."/fork next'
        bundle = self.load({'Town': {'a/f Abigail 500': original, 'path': branch, 'next': 'message "Done."/end'}})
        root = next(e for e in bundle['events'] if e['event_id'] == 'a')
        self.assertEqual(root['script'], original)
        self.assertEqual([b['text'] for b in root['event']['story']['beats']], ['One / two.', 'After pose.'])
        self.assertIn('Conditional tail.', root['transcript'])
        self.assertEqual({e['event_id'] for e in bundle['events']}, {'a', 'path', 'next'})
        self.assertTrue(all(e['optional'] for e in bundle['events'] if e['category'] == 'branch'))
        refs = root['event']['story']['vanilla_source']['related_scripts']
        self.assertEqual({r['event_id'] for r in refs}, {'path', 'next'})
        self.assertEqual(next(r['script'] for r in refs if r['event_id'] == 'path'), branch)

    def test_shared_scene_does_not_pull_foreign_arc_into_primary(self):
        bundle = self.load({'Town': {
            'one/f Abigail 500': scene(),
            'two/e one/f Abigail 1000': scene(),
            'alex/f Alex 2500': scene('Alex', 'end'),
            'group/f Abigail 2500/f Alex 2500/e two/e alex': 'none/5 5/Abigail 5 5 2 Alex 6 5 2 farmer 5 8 0/end',
        }})
        primary = {r['event_id'] for r in bundle['events'] if not r['optional']}
        self.assertEqual(primary, {'one', 'two'})
        self.assertNotIn('alex', {r['event_id'] for r in bundle['events']})
        self.assertTrue(next(r for r in bundle['events'] if r['event_id'] == 'group')['optional'])

    def test_spouse_without_cast_and_runtime_emily_roots(self):
        bundle = self.load({'Town': {'a/O Alex/n gaveMoney': 'none/5 6/farmer 5 8 0/message "Ready."/end'}}, 'alex')
        row = bundle['events'][0]
        self.assertFalse(row['optional'])
        self.assertIsNone(row['hearts'])
        self.assertEqual(row['event']['hearts'], 0)
        self.assertEqual(row['suggested_hearts'], 14)
        self.assertEqual(row['event']['story']['relationship'], 'any')
        self.assertIn('O Alex', ' '.join(row['warnings']))
        bundle = self.load({'ManorHouse': {'2123243/e runtimeOnly': scene('Emily', 'end')},
                            'Woods': {'date/D Emily/e otherRuntime': scene('Emily', 'end')},
                            'Farm': {'992559/O Emily': scene('Emily', 'end')}}, 'emily')
        self.assertFalse(next(r for r in bundle['events'] if r['event_id'] == '2123243')['optional'])
        self.assertFalse(next(r for r in bundle['events'] if r['event_id'] == 'date')['optional'])
        self.assertTrue(next(r for r in bundle['events'] if r['event_id'] == '992559')['optional'])
        self.assertIn('absent', ' '.join(next(r for r in bundle['events'] if r['event_id'] == 'date')['warnings']))

    def test_fractional_gates_offscreen_actors_and_advanced_dialogue(self):
        bundle = self.load({'Town': {'one/f Abigail 1700/L/s summer/Time 600 1200/Time 900 1700':
                                      scene(x=-1000, commands='speak Abigail "Still editable."/speak Abigail "$q 1 question"/speak Abigail "Conditional."/end')}})
        row = bundle['events'][0]
        self.assertEqual(row['hearts'], 6.8)
        event = row['event']
        self.assertEqual(event['hearts'], 0)
        self.assertEqual(event['story']['min_house_upgrade'], 0)
        self.assertEqual(event['story']['season'], 'any')
        self.assertEqual(event['story']['time_start'], 600)
        self.assertEqual(event['story']['time_end'], 2400)
        self.assertEqual(event['story']['vanilla_source']['actors'][0]['x'], -1000)
        self.assertNotIn('Abigail', [a['name'] for a in event['story']['actors']])
        self.assertEqual([b['text'] for b in event['story']['beats']], ['Still editable.'])

    def test_cross_map_typed_dispatch_preserves_only_correct_branch_context(self):
        bundle = self.load({'Town': {'one/f Abigail 500': scene(commands='changeToTemporaryMap Darkroom/fork leave/end')},
                            'Temp': {'leave': 'message "Temporary branch"/end'},
                            'SebastianRoom': {'leave': 'message "Unrelated branch"/end'}})
        rows = bundle['events']
        self.assertEqual({r['location'] for r in rows}, {'Town', 'Temp'})
        root = next(r for r in rows if r['event_id'] == 'one')
        self.assertEqual(root['event']['story']['vanilla_source']['related_scripts'][0]['script'], 'message "Temporary branch"/end')
        bundle = self.load({'BusStop': {'one/f Sam 2000': scene('Sam', 'cutscene bandFork/end')},
                            'Temp': {n: 'message "Music"/end' for n in ('poppy', 'heavy', 'techno', 'honkytonk')}}, 'sam')
        root = next(r for r in bundle['events'] if r['event_id'] == 'one')
        self.assertEqual(len(root['event']['story']['vanilla_source']['related_scripts']), 4)

    def test_apply_is_pure_deduplicated_and_only_remaps_structured_cast(self):
        bundle = self.load({'Town': {'a/f Abigail 500': scene(commands='speak Abigail "Abigail stays literal."/end'),
                                     'b/f Abigail 1000/e a': scene()}})
        character = new_project()['character']
        original, bundle_before = deepcopy(character), deepcopy(bundle)
        keys = [r['key'] for r in bundle['events']]
        result = apply_vanilla_story(character, bundle, keys)
        self.assertEqual(character, original)
        self.assertEqual(bundle, bundle_before)
        self.assertEqual(len(result['events']), 2)
        self.assertEqual(len(result['relationships']), 1)
        first, second = result['events']
        self.assertEqual(second['story']['previous_event_id'], first['id'])
        self.assertEqual(first['story']['actors'][0]['name'], '$npc')
        self.assertEqual(first['story']['beats'][0]['actor'], '$npc')
        self.assertEqual(first['story']['beats'][0]['text'], 'Abigail stays literal.')
        self.assertEqual(first['story']['vanilla_source']['actors'][0]['name'], 'Abigail')
        self.assertEqual(apply_vanilla_story(result, bundle, keys), result)
        self.assertEqual(compile_story(result), [])
        first['story']['stage'] = 'ready'
        self.assertTrue(any('planned_effects' in r['field'] for r in event_issues(first, result)))

    def test_ambiguous_or_and_multiple_predecessors_never_become_false_chain(self):
        bundle = self.load({'Town': {'a/f Abigail 500': scene(), 'a/f Abigail 750': scene(),
                                     'b/f Abigail 1000/e a': scene(), 'c/f Abigail 1500/e a b': scene(),
                                     'd/f Abigail 2000/e b/e c': scene()}})
        result = apply_vanilla_story(new_project()['character'], bundle, [r['key'] for r in bundle['events']])
        self.assertEqual(len(result['events']), 5)
        self.assertTrue(all(not e['story']['previous_event_id'] for e in result['events']))

    def test_atomic_bounds_and_explicit_untouched_starter_replacement(self):
        bundle = self.load({'Town': {'a/f Abigail 500': scene()}})
        key = bundle['events'][0]['key']
        character = new_project(story_starter='romance')['character']
        self.assertTrue(untouched_story_starter(character))
        result = apply_vanilla_story(character, bundle, [key], replace_starter=True)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(len(character['events']), 6)
        character['events'][0]['name'] = 'Authored scene'
        with self.assertRaises(ValueError):
            apply_vanilla_story(character, bundle, [key], replace_starter=True)
        full = new_project()['character']
        full['events'] = [dict(bundle['events'][0]['event'], id=str(i)) for i in range(100)]
        for e in full['events']:
            e['story'] = deepcopy(e['story'])
            e['story'].pop('vanilla_source')
        before = deepcopy(full)
        with self.assertRaises(VanillaStoryError):
            apply_vanilla_story(full, bundle, [key])
        self.assertEqual(full, before)

    def test_cancellation_symlinks_and_asset_cache_invalidation(self):
        bundle = self.load({'Town': {'a/f Abigail 500': scene()}})
        with self.assertRaises(ValueError):
            load_vanilla_story('abigail', self.root, cancelled=lambda: True)
        (self.root / 'Data/Events/Town.xnb').write_bytes(dictionary_xnb({'b/f Abigail 500': scene()}))
        self.assertEqual(load_vanilla_story('abigail', self.root)['events'][0]['event_id'], 'b')
        (self.root / 'Data/Events/Linked.xnb').symlink_to(self.root / 'Data/Events/Town.xnb')
        with self.assertRaises(ValueError):
            load_vanilla_story('abigail', self.root)

    def test_malformed_choices_and_preview_cannot_remove_review_guard(self):
        bundle = self.load({'Town': {'a/f Abigail 500': scene()}})
        character = new_project()['character']
        for keys in (['missing'], [None], 'not-a-list'):
            with self.assertRaises(VanillaStoryError):
                apply_vanilla_story(character, bundle, keys)
        event = bundle['events'][0]['event']
        event['story'].update(stage='ready', planned_effects=[])
        result = apply_vanilla_story(character, bundle, [bundle['events'][0]['key']])
        self.assertEqual(result['events'][0]['story']['stage'], 'outline')
        self.assertEqual(result['events'][0]['story']['planned_effects'][0]['resolution'], 'pending')

    def test_linear_effects_survive_as_omissions_but_async_and_unknown_flow_stop(self):
        for barrier in ('move Abigail 1 0 2 true', 'move Abigail 1 0 2 farmer 0 1 0', 'futureBranch anything'):
            with self.subTest(barrier=barrier):
                bundle = self.load({'Town': {'a/f Abigail 500': scene(commands=
                    'specificTemporarySprite heart 1 2/addWorldState flag/makeInvisible Abigail/'
                    'speak Abigail "Before boundary."/' + barrier + '/speak Abigail "After boundary."/end')}})
                event = bundle['events'][0]['event']
                self.assertEqual([b['text'] for b in event['story']['beats']], ['Before boundary.'])
                self.assertIn('addWorldState', ' '.join(event['story']['vanilla_source']['warnings']))
                self.assertNotIn('[skippable]', bundle['events'][0]['transcript'])

    def test_missing_source_threshold_is_chapter_only_and_cycles_are_not_projected(self):
        bundle = self.load({'Town': {'a/O Abigail/e b': scene(), 'b/O Abigail/e a': scene()}})
        result = apply_vanilla_story(new_project()['character'], bundle, [r['key'] for r in bundle['events']])
        self.assertTrue(all(event['hearts'] == 0 for event in result['events']))
        self.assertTrue(all(chapter['hearts'] == 14 for chapter in result['storyline']['chapters']))
        self.assertLess(sum(bool(event['story']['previous_event_id']) for event in result['events']), 2)

    def test_invalid_reference_shapes_fail_without_mutation(self):
        original = self.load({'Town': {'a/f Abigail 500': scene()}})
        character = new_project()['character']
        for mutation in (
            lambda b: b['npc'].update(id=[]),
            lambda b: b['events'][0].update(event_id=[]),
            lambda b: b['events'][0]['event']['story']['vanilla_source'].update(actors='bad'),
            lambda b: b['events'][0]['event']['story']['vanilla_source'].update(related_scripts=[None]),
            lambda b: b['events'][0]['event']['story']['vanilla_source'].update(extension=float('nan')),
        ):
            bundle = deepcopy(original)
            mutation(bundle)
            before = deepcopy(character)
            with self.assertRaises(ValueError):
                apply_vanilla_story(character, bundle, [original['events'][0]['key']])
            self.assertEqual(character, before)

    def test_initialization_reports_replacement_and_preserves_nonstory_data(self):
        from pixelheart_core.story import new_relationship
        bundle = self.load({'Town': {'a/f Abigail 500': scene(), 'b/f Abigail 1000/e a': scene()}})
        keys = [entry['key'] for entry in bundle['events']]
        character = new_project(story_starter='romance')['character']
        character['events'][0]['name'] = 'An authored event'
        old_arc = new_relationship()
        old_arc['name'] = 'An authored arc'
        character['relationships'] = [old_arc]
        character['storyline']['brief'].update(desire='My own character desire', motif='A private motif')
        character['storyline']['extension'] = {'keep': [1, 2]}
        character['private_extension'] = {'artwork': 'private/sprite.png', 'notes': 'Keep this untouched'}
        character['life'] = {'extension': {'keep': True}}
        before, bundle_before = deepcopy(character), deepcopy(bundle)
        preview = preview_vanilla_initialization(character, bundle, keys)
        self.assertEqual(preview['replace_counts'], {'events': 6, 'chapters': 6, 'relationships': 1})
        self.assertEqual(preview['create_counts'], {'events': 2, 'chapters': 2, 'relationships': 1})
        self.assertEqual(preview['selected_count'], 2)
        self.assertFalse(preview['blocked'])
        self.assertEqual(preview['life_links'], [])
        result = initialize_vanilla_story(character, bundle, keys)
        self.assertEqual(character, before)
        self.assertEqual(bundle, bundle_before)
        self.assertEqual({k: v for k, v in result.items() if k not in ('events', 'relationships', 'storyline')},
                         {k: v for k, v in character.items() if k not in ('events', 'relationships', 'storyline')})
        self.assertEqual(result['storyline']['brief'], character['storyline']['brief'])
        self.assertEqual(result['storyline']['extension'], character['storyline']['extension'])
        self.assertNotIn('starter_origin', result['storyline'])
        self.assertEqual(len(result['events']), 2)
        self.assertNotIn(old_arc['id'], [arc['id'] for arc in result['relationships']])
        self.assertTrue(all(event['story']['stage'] == 'outline' for event in result['events']))
        self.assertEqual(initialize_vanilla_story(result, bundle, keys), result)

    def test_initialization_blocks_all_missing_life_links_including_disabled(self):
        from pixelheart_core.life import new_life_record
        bundle = self.load({'Town': {'a/f Abigail 500': scene()}})
        keys = [bundle['events'][0]['key']]
        character = new_project(story_starter='friendship')['character']
        previous = character['events'][0]
        character['life'] = {}
        for kind in ('dialogues', 'routines', 'spouse_dialogue'):
            row = new_life_record(kind)
            row['conditions']['after_event_id'] = previous['id']
            character['life'][kind] = [row]
        before = deepcopy(character)
        impact = preview_vanilla_initialization(character, bundle, keys)
        self.assertTrue(impact['blocked'])
        self.assertEqual(len(impact['life_links']), 3)
        self.assertTrue(all(link['status'] == 'blocked' and not link['enabled'] for link in impact['life_links']))
        self.assertEqual(impact['life_links'][0]['event_name'], previous['name'])
        with self.assertRaises(VanillaStoryError) as context:
            initialize_vanilla_story(character, bundle, keys)
        self.assertEqual(context.exception.issues, impact['issues'])
        self.assertEqual({issue['field'] for issue in context.exception.issues}, {
            'life.dialogues.0.conditions.after_event_id', 'life.routines.0.conditions.after_event_id',
            'life.spouse_dialogue.0.conditions.after_event_id'})
        self.assertEqual(character, before)

    def test_initialization_preserves_exact_surviving_life_ids_and_warns_readiness(self):
        from pixelheart_core.life import new_life_record, life_issues
        bundle = self.load({'Town': {'a/f Abigail 500': scene(), 'b/f Abigail 1000/e a': scene()}})
        keys = [entry['key'] for entry in bundle['events']]
        character = initialize_vanilla_story(new_project()['character'], bundle, keys)
        event = character['events'][0]
        event['name'] = 'My adapted earlier event'
        event['story']['stage'] = 'ready'
        rule = new_life_record()
        rule.update(enabled=True, name='A linked daily response', text='The event changed this day.')
        rule['conditions']['after_event_id'] = event['id']
        character['life'] = {'dialogues': [rule]}
        impact = preview_vanilla_initialization(character, bundle, keys)
        self.assertFalse(impact['blocked'])
        self.assertEqual(impact['life_links'][0]['status'], 'retained')
        self.assertEqual(impact['life_links'][0]['event_id'], event['id'])
        self.assertEqual(impact['life_links'][0]['event_name'], 'My adapted earlier event')
        self.assertIn('ready before export', ' '.join(impact['warnings']))
        result = initialize_vanilla_story(character, bundle, keys)
        self.assertEqual(result['life'], character['life'])
        self.assertEqual(result['events'][0]['id'], event['id'])
        self.assertEqual(result['events'][0]['story']['stage'], 'outline')
        self.assertTrue(any(issue['field'] == 'life.dialogues.0.conditions.after_event_id' for issue in life_issues(result)))

    def test_initialization_rechecks_dependencies_and_rejects_invalid_selections_atomically(self):
        from pixelheart_core.life import new_life_record
        bundle = self.load({'Town': {'a/f Abigail 500': scene()}})
        keys = [bundle['events'][0]['key']]
        character = new_project(story_starter='friendship')['character']
        self.assertFalse(preview_vanilla_initialization(character, bundle, keys)['blocked'])
        row = new_life_record()
        row['conditions']['after_event_id'] = character['events'][0]['id']
        character['life'] = {'dialogues': [row]}
        before = deepcopy(character)
        for selection in (keys, [], ['missing'], None):
            with self.assertRaises(ValueError):
                initialize_vanilla_story(character, bundle, selection)
            self.assertEqual(character, before)
        character['life'] = {'dialogues': 'malformed'}
        with self.assertRaises(VanillaStoryError) as context:
            preview_vanilla_initialization(character, bundle, keys)
        self.assertEqual(context.exception.issues[0]['field'], 'life.dialogues')


if __name__ == '__main__':
    unittest.main()
