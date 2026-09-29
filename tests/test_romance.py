"""Romance drafts are blank; grouping never migrates or rewrites saved work."""
from copy import deepcopy
import json
import unittest

from pixelheart_core.romance import (
    ROMANCE_HEARTS, RomanceError, group_romance_events, new_romance_event, new_romance_events,
)
from pixelheart_core.story import compile_story, structure_issues


class RomanceTests(unittest.TestCase):
    def test_six_blank_drafts_have_standard_cast_and_explicit_access(self):
        character = {'id': 'my-character', 'home_map': 'Beach', 'home_x': 12, 'home_y': 15,
                     'romanceable': False, 'events': [{'id': 'old', 'hearts': 5}],
                     'storyline': {'brief': {'desire': 'My own writing'}, 'chapters': []}}
        before = deepcopy(character)
        events = new_romance_events(character)
        self.assertEqual(character, before)
        self.assertEqual(tuple(event['hearts'] for event in events), ROMANCE_HEARTS)
        self.assertEqual(len({event['id'] for event in events}), 6)
        for event in events:
            self.assertEqual(structure_issues(event), [])
            self.assertEqual(event['location'], 'Beach')
            self.assertEqual(event['name'], f"{event['hearts']}-heart event")
            self.assertEqual(event['description'], '')
            story = event['story']
            self.assertEqual(story['stage'], 'outline')
            self.assertEqual(story['relationship'], {10: 'dating', 14: 'married'}.get(event['hearts'], 'any'))
            self.assertEqual([a['name'] for a in story['actors']], ['$npc', 'farmer'])
            self.assertEqual((story['actors'][0]['x'], story['actors'][0]['y']), (12, 15))
            self.assertEqual(len(story['beats']), 1)
            self.assertEqual(story['beats'][0]['kind'], 'dialogue')
            self.assertEqual(story['beats'][0]['text'], '')
            self.assertEqual(story['arc_ids'], [])
            self.assertEqual(story['relationship_id'], '')
            self.assertEqual(story['previous_event_id'], '')
            self.assertEqual(story['planned_effects'], [])
            for key in ('premise', 'conflict', 'outcome', 'before', 'after', 'motif', 'player_role', 'aftermath_notes'):
                self.assertEqual(story[key], '')
        self.assertEqual(compile_story({**character, 'events': events}), [])
        self.assertEqual(json.loads(json.dumps(events)), events)

    def test_each_new_part_has_independent_identity_and_mutable_fields(self):
        first, second = new_romance_event({}, 6), new_romance_event({}, 6)
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['story']['actors'][0]['id'], second['story']['actors'][0]['id'])
        self.assertNotEqual(first['story']['beats'][0]['id'], second['story']['beats'][0]['id'])
        first['story']['beats'][0]['text'] = 'Only this part changes.'
        self.assertEqual(second['story']['beats'][0]['text'], '')

    def test_grouping_preserves_multipart_order_extensions_and_extras_exactly(self):
        events = [
            {'id': 'eight-first', 'hearts': 8, 'source': {'raw': 'Keep all source data'}},
            {'id': 'five', 'hearts': 5, 'description': 'A legacy five-heart scene'},
            {'id': 'two', 'hearts': 2, 'story': {'beats': [], 'unknown': [1, 2]}},
            {'id': 'eight-second', 'hearts': 8, 'story': {'previous_event_id': 'eight-first'}},
            {'id': 'fractional', 'hearts': 6.8}, {'id': 'textual', 'hearts': '2'},
            {'id': 'missing'}, {'id': 'float', 'hearts': 2.0}, {'id': 'bool', 'hearts': True},
        ]
        before = deepcopy(events)
        groups = group_romance_events(events)
        self.assertEqual(list(groups['milestones']), list(ROMANCE_HEARTS))
        self.assertEqual(groups['milestones'][8], [events[0], events[3]])
        self.assertEqual(groups['milestones'][2], [events[2]])
        self.assertEqual(groups['milestones'][6], [])
        self.assertEqual(groups['extras'], [events[i] for i in (1, 4, 5, 6, 7, 8)])
        self.assertEqual(events, before)
        self.assertEqual(sum(map(len, groups['milestones'].values())) + len(groups['extras']), len(events))
        groups['milestones'][8][0]['source']['raw'] = 'View-only edit'
        groups['extras'][0]['description'] = 'View-only edit'
        self.assertEqual(events, before)

    def test_rejects_invalid_new_milestones_and_unsafe_starting_shapes(self):
        for hearts in (None, True, 2.0, '2', 0, 3, 12, 15, [], {}):
            with self.subTest(hearts=hearts), self.assertRaises(RomanceError):
                new_romance_event({}, hearts)
        for character in ('bad', [], {'home_x': True}, {'home_y': -1}, {'home_map': None}, {'home_map': 'x' * 81}):
            with self.subTest(character=character), self.assertRaises(RomanceError):
                new_romance_events(character)
        for records in (None, {}, [None], [{'id': str(i)} for i in range(101)]):
            with self.assertRaises(RomanceError):
                group_romance_events(records)

    def test_defaults_and_boundary_positions_remain_valid_drafts(self):
        self.assertEqual(len(new_romance_events()), 6)
        event = new_romance_event({'home_x': 1000, 'home_y': 1000, 'home_map': ''}, 14)
        self.assertEqual(event['location'], 'Town')
        self.assertEqual(structure_issues(event), [])
        self.assertEqual(event['story']['actors'][1]['y'], 1000)
        self.assertEqual(group_romance_events([])['extras'], [])


if __name__ == '__main__':
    unittest.main()
