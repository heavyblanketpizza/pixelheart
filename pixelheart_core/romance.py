"""Blank romance scenes and a view over existing events; no project mutations."""
from __future__ import annotations

from copy import deepcopy

from .story import new_beat, new_event
from .story_planning import _valid_text


ROMANCE_HEARTS = (2, 4, 6, 8, 10, 14)


class RomanceError(ValueError):
    pass


def new_romance_event(character, hearts):
    """Create one independent blank draft with the standard scene controls.

    Friendship scenes remain accessible after dating. Ten- and fourteen-heart
    drafts explicitly require dating and marriage respectively. The caller owns
    insertion into its event list; existing stories and character settings stay
    unchanged, including whether that character can currently be romanced.
    """
    if type(hearts) is not int or hearts not in ROMANCE_HEARTS:
        raise RomanceError('Choose a standard heart event: 2, 4, 6, 8, 10 or 14 hearts.')
    if character is None:
        character = {}
    if not isinstance(character, dict):
        raise RomanceError('Character data must be an object.')
    for field in ('home_x', 'home_y'):
        if field in character and (type(character[field]) is not int or not 0 <= character[field] <= 1000):
            raise RomanceError('Starting tiles must be whole numbers from 0 to 1000.')
    if 'home_map' in character and not _valid_text(character['home_map'], 80):
        raise RomanceError('The starting map must be valid text of at most 80 characters.')
    event = new_event(character)
    event.update(name=f'{hearts}-heart event', hearts=hearts)
    event['story'].update(stage='outline', beats=[new_beat('dialogue')],
                          relationship='married' if hearts == 14 else 'dating' if hearts == 10 else 'any')
    return event


def new_romance_events(character=None):
    """Return six fresh, blank scenes without adding chapters or arcs."""
    return [new_romance_event(character, hearts) for hearts in ROMANCE_HEARTS]


def group_romance_events(events):
    """Return copied view buckets, never a replacement serialization order.

    Multiple parts at one threshold retain their original relative order. Every
    other threshold, including missing or legacy textual values, stays in extras
    without normalization or rounding. Callers must keep the original flat list
    for saving so grouping cannot reorder authored events across milestones.
    """
    if not isinstance(events, list) or len(events) > 100 or any(not isinstance(event, dict) for event in events):
        raise RomanceError('Events must be a list of at most 100 event objects.')
    groups = {'milestones': {hearts: [] for hearts in ROMANCE_HEARTS}, 'extras': []}
    for event in events:
        hearts = event.get('hearts')
        target = groups['milestones'][hearts] if type(hearts) is int and hearts in ROMANCE_HEARTS else groups['extras']
        target.append(deepcopy(event))
    return groups
