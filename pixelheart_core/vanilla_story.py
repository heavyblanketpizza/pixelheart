"""Read locally owned vanilla scenes as attributed, editable adaptation drafts.

Original scripts never become executable project data. Only a bounded subset of
linear commands is projected; source text, conditions and dependencies survive
in provenance. No downloaded or bundled game text, reflection, or game execution.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import re
import uuid

from .game_scene_assets import content_root, stat_key
from .local_templates import _safe_path, _read_bounded, _check_cancelled
from .locations import location_name
from .story import new_event, new_beat, new_relationship, new_planned_effect, EMOTES, structure_issues
from .story_planning import new_chapter, normalize_storyline, _check_character, _valid_text
from .xnb_preview import string_dictionary, XnbError, MAX_PACKED

VANILLA_NPCS = {name.lower(): {'name': name} for name in (
    'Abigail', 'Alex', 'Elliott', 'Emily', 'Haley', 'Harvey',
    'Leah', 'Maru', 'Penny', 'Sam', 'Sebastian', 'Shane')}
_MAX_FILES = 128
_MAX_ENTRIES = 4096
_MAX_TEXT = 16 * 1024 * 1024
_MAX_REFERENCE = 2 * 1024 * 1024
_CATALOG_CACHE = OrderedDict()
_ACTOR = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,191}\Z')
# These named dispatches are implemented by the game rather than explicit fork
# operands. Keep this small registry typed and scoped to its source asset.
_DISPATCH = {
    ('cutscene', 'bandFork'): ('Temp', ('poppy', 'heavy', 'techno', 'honkytonk')),
    ('question', 'chooseCharacter'): ('SebastianRoom', ('warrior', 'healer')),
    ('question', 'haleyDarkRoom'): ('Temp', ('decorate', 'leave')),
}
_RELATIONSHIP_SCENES = {('emily', 'ManorHouse', '2123243')}
_OPTIONAL_SCENES = {('emily', 'Farm', '992559')}
_ADAPTATION = ('Review this vanilla adaptation against its original conditions, camera, commands, choices, '
               'mail and follow-up effects. Implement supported behavior or explicitly omit the remaining '
               'effects before marking this event ready. The preserved source is reference only.')


class VanillaStoryError(ValueError):
    """A local source or selected adaptation cannot be loaded safely."""

    def __init__(self, message, *, issues=None):
        self.issues = deepcopy(issues or [])
        super().__init__(message)


def _stable(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(['pixelheart:vanilla:1', *parts], ensure_ascii=False)))


def clear_vanilla_cache():
    _CATALOG_CACHE.clear()


def _split(value, delimiter='/'):
    """Separate script commands/conditions, respecting quoted dialogue slashes."""
    result, start, quoted, escaped = [], 0, False, False
    for index, char in enumerate(value):
        if char == '"' and not escaped:
            quoted = not quoted
        if char == delimiter and not quoted:
            result.append(value[start:index])
            start = index + 1
        escaped = char == '\\' and not escaped
    if quoted:
        raise VanillaStoryError('Unbalanced quotes: the source remains reference only.')
    result.append(value[start:])
    return result


def _tokens(value):
    result, index = [], 0
    while index < len(value):
        if value[index].isspace():
            index += 1
            continue
        start = index
        if value[index] == '"':
            index += 1
            start = index
            while index < len(value):
                if value[index] == '"' and (index == start or value[index - 1] != '\\'):
                    break
                index += 1
            if index == len(value):
                return []
            result.append(value[start:index])
            index += 1
            if index < len(value) and not value[index].isspace():
                return []
        else:
            while index < len(value) and not value[index].isspace():
                index += 1
            result.append(value[start:index])
    return result


def _number(value):
    return int(value) if re.fullmatch(r'-?\d{1,9}', value) else None


def _header(parts, seed):
    if len(parts) < 3:
        return [], None, None, 0
    camera, cast = _tokens(parts[1]), _tokens(parts[2])
    if len(camera) != 2 or any(_number(v) is None for v in camera) or not cast or len(cast) % 4 or len(cast) > 1024:
        return [], None, None, 0
    actors = []
    for index in range(0, len(cast), 4):
        name, x, y, facing = cast[index:index + 4]
        values = [_number(v) for v in (x, y, facing)]
        if not _ACTOR.fullmatch(name) or None in values or values[2] not in range(4):
            return [], None, None, 0
        actors.append({'id': _stable(seed, 'actor', index), 'name': name,
                       'x': values[0], 'y': values[1], 'facing': values[2]})
    return actors, [int(v) for v in camera], parts[0], 3


def _conditions(key):
    try:
        parts = _split(key)
    except VanillaStoryError:
        return key, []
    return parts[0], [_tokens(part) for part in parts[1:]]


def _references(clauses):
    return [values[1:] for values in clauses if values and values[0] in ('e', 'SawEvent') and len(values) > 1]


def _facts(row, name):
    points, other, spouse, dating = [], False, False, False
    for values in row['conditions']:
        if not values:
            continue
        if values[0] in ('f', 'Friendship'):
            for index in range(1, len(values) - 1, 2):
                npc, amount = values[index:index + 2]
                if npc == name and _number(amount) is not None:
                    points.append(int(amount))
                elif npc != name:
                    other = True
        if values[0] in ('O', 'Spouse') and values[1:] == [name]:
            spouse = True
        if values[0] in ('D', 'Dating') and values[1:] == [name]:
            dating = True
    hearts = max(points) / 250 if points else None
    if hearts is not None and hearts.is_integer():
        hearts = int(hearts)
    phase = 'married' if spouse else 'dating' if dating or hearts is not None and hearts >= 10 else 'friendship' if points else 'any'
    return hearts, phase, bool(points or spouse or dating), other


def _catalog(game_root, cancelled):
    _check_cancelled(cancelled)
    root = content_root(game_root)
    folder = _safe_path(root, 'Data/Events', directory=True)
    if folder is None:
        raise VanillaStoryError('This installation has no Data/Events assets.')
    paths = []
    for index, path in enumerate(folder.iterdir()):
        if index >= 2048:
            raise VanillaStoryError('The event folder exceeds the supported file limit.')
        if path.suffix.lower() == '.xnb' and re.fullmatch(r'[A-Za-z0-9_-]+', path.stem):
            checked = _safe_path(root, path.relative_to(root))
            if checked is not None:
                paths.append(checked)
    if not paths or len(paths) > _MAX_FILES:
        raise VanillaStoryError('Use an installation with 1–128 base event assets.')
    paths.sort()
    keys = tuple(stat_key(root, path) for path in paths)
    cached = _CATALOG_CACHE.get(keys)
    if cached is not None:
        _check_cancelled(cancelled)
        return cached
    rows, total, source_assets = [], 0, []
    for path, key in zip(paths, keys):
        _check_cancelled(cancelled)
        payload = _read_bounded(root, path, MAX_PACKED, cancelled)
        values = string_dictionary(payload, maximum=_MAX_ENTRIES)
        if key != stat_key(root, path):
            raise VanillaStoryError('An event asset changed while loading. Please retry.')
        digest = hashlib.sha256(payload).hexdigest()
        asset = 'Data/Events/' + path.stem
        source_assets.append({'asset': asset, 'sha256': digest})
        for event_key, script in values.items():
            _check_cancelled(cancelled)
            total += len(event_key.encode('utf-8')) + len(script.encode('utf-8'))
            if total > _MAX_TEXT or len(rows) >= _MAX_ENTRIES:
                raise VanillaStoryError('The event catalog exceeds the supported size limit.')
            if not _valid_text(event_key, 65536) or not _valid_text(script, 65536):
                raise VanillaStoryError('An event contains invalid or oversized source text.')
            event_id, conditions = _conditions(event_key)
            seed = asset + '\x00' + event_key
            parse_warning = None
            try:
                parts = _split(script)
            except VanillaStoryError as exc:
                parts, parse_warning = [], str(exc)
            actors, camera, music, offset = _header(parts, seed)
            commands = [_tokens(part) for part in parts[offset:]]
            branches, dispatches = [], []
            for command in commands:
                if command and command[0] == 'fork' and len(command) in (2, 3):
                    branches.append(command[-1])
                elif len(command) == 2 and command[0] == 'switchEvent':
                    branches.append(command[1])
                if len(command) >= 2 and tuple(command[:2]) in _DISPATCH:
                    dispatches.append(_DISPATCH[tuple(command[:2])])
            rows.append({'asset': asset, 'location': path.stem, 'event_id': event_id,
                         'event_key': event_key, 'script': script, 'conditions': conditions,
                         'parts': parts, 'commands': commands, 'offset': offset, 'actors': actors,
                         'camera': camera, 'music': music, 'branches': branches,
                         'dispatches': dispatches,
                         'parse_warning': parse_warning, 'sha256': digest})
    result = (rows, source_assets)
    _CATALOG_CACHE[keys] = result
    while len(_CATALOG_CACHE) > 1:
        _CATALOG_CACHE.popitem(last=False)
    return result


def _safe_dialogue(text):
    if not text.strip() or not _valid_text(text, 8000):
        return False
    if any(value in text for value in ('"', '\\', '{{', '}}', '[', ']')) or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        return False
    if any(tag not in {'h', 's', 'a', 'u', 'l', 'b'} and not tag.isdecimal() for tag in re.findall(r'\$([A-Za-z]+|[0-9]+)', text)):
        return False
    if re.search(r'%(?:fork|revealtaste)', text, re.IGNORECASE):
        return False
    return not any(m.end() < len(text) and text[m.end()] not in '#\r\n' for m in re.finditer(r'\$[0-9]+', text))


def _project(row, npc, entry_key, hearts):
    event = new_event()
    event.update(id=_stable(entry_key, 'preview'),
                 location=row['location'], hearts=hearts if type(hearts) is int and 0 <= hearts <= 14 else 0,
                 description='Adapted from a locally supplied Stardew Valley scene. Read the preserved source before rewriting.')
    story = event['story']
    story.update(stage='outline', actors=[], beats=[], music='none', relationship='any')
    warnings = ['This is an adaptation draft, not an exact recreation. Source conditions and effects require review.',
                'The original camera is retained in the reference; the editor uses the NPC starting tile.']
    if hearts is not None and (type(hearts) is not int or not 0 <= hearts <= 14):
        warnings.append(f'The original threshold is {hearts:g} hearts; it cannot be represented exactly. Draft hearts are 0.')
    if row['parse_warning']:
        warnings.append(row['parse_warning'])
    if not row['offset']:
        warnings.append('This is a branch or manual script without a complete scene header; use its original parent context.')
    if row['music'] and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}', row['music']):
        story['music'] = row['music']
    names = set()
    for actor in row['actors']:
        if len(story['actors']) < 16 and actor['name'] not in names and all(0 <= actor[axis] <= 1000 for axis in ('x', 'y')):
            story['actors'].append(deepcopy(actor))
            names.add(actor['name'])
        else:
            warnings.append('Off-screen, duplicate or excess source actors remain in the reference; their editable staging needs review.')
    # Only exact single-value clauses are projected. Every other gate remains in
    # the original key, with a pending review guard, never guessed or rounded.
    unconverted_conditions = []
    predecessors = _references(row['conditions'])
    condition_counts = {}
    for values in row['conditions']:
        if values:
            field = {'t': 'time', 'Time': 'time', 'w': 'weather', 'Weather': 'weather', 'Season': 'season'}.get(values[0])
            if field:
                condition_counts[field] = condition_counts.get(field, 0) + 1
    for values in row['conditions']:
        if not values:
            unconverted_conditions.append('(unparsed condition)')
            continue
        command, args = values[0], values[1:]
        if command in ('f', 'Friendship') and len(args) == 2 and args[0] == npc['name'] and type(hearts) is int and 0 <= hearts <= 14:
            continue
        if command in ('t', 'Time') and condition_counts['time'] == 1 and len(args) == 2 and all(_number(v) is not None for v in args):
            start, end = map(int, args)
            if 600 <= start <= end <= 2600 and all(v % 100 < 60 and v % 10 == 0 for v in (start, end)):
                story.update(time_start=start, time_end=end)
                continue
        if command in ('w', 'Weather') and condition_counts['weather'] == 1 and args in (['sunny'], ['rainy']):
            story['weather'] = args[0]
            continue
        if command == 'Season' and condition_counts['season'] == 1 and len(args) == 1 and args[0] in ('spring', 'summer', 'fall', 'winter'):
            story['season'] = args[0]
            continue
        if command in ('e', 'SawEvent'):
            continue  # Resolved conservatively only when applying selected drafts.
        unconverted_conditions.append(' '.join(values))
    if unconverted_conditions:
        warnings.append('Conditions retained for manual review: ' + '; '.join(unconverted_conditions))
    if any(values and values[0] in ('O', 'Spouse') for values in row['conditions']):
        warnings.append('The original Spouse condition includes engagement or marriage; the suggested married chapter does not change draft relationship access.')
    if predecessors:
        warnings.append('Event-seen conditions are source dependencies. Only one unambiguous selected predecessor can become an editor link.')
    # Off-screen source actors may still have useful editable dialogue. Their
    # absent staging remains a readiness issue; never invent or clamp a tile.
    source_names = {actor['name'] for actor in row['actors']}
    transcript, unsupported, blocked, positions = [], set(), not bool(row['offset']), {a['name']: [a['x'], a['y']] for a in story['actors']}
    omitted_linear = {'skippable', 'faceDirection', 'showFrame', 'stopAnimation', 'playSound', 'stopMusic',
                'playMusic', 'viewport', 'globalFade', 'fade', 'screenFlash', 'textAboveHead', 'shake',
                'animate', 'positionOffset', 'jump', 'eyes', 'farmerEat', 'farmerAnimation', 'tossItem',
                'specificTemporarySprite', 'ambientLight', 'addLantern', 'makeInvisible', 'makeVisible',
                'addConversationTopic', 'addWorldState', 'removeWorldState', 'mail', 'addMailReceived',
                'addQuest', 'removeQuest', 'speed', 'changeMapTile', 'changeSprite', 'bgColor', 'swimming'}
    for index, (part, values) in enumerate(zip(row['parts'][row['offset']:], row['commands'])):
        if not values:
            transcript.append('[Unparsed source segment; see original script.]')
            blocked = True
            continue
        command, args = values[0], values[1:]
        if command == 'speak' and len(args) == 2:
            transcript.append(args[0] + ': ' + args[1])
        elif command == 'message' and len(args) == 1:
            transcript.append('Narration: ' + args[0])
        elif command in ('question', 'quickQuestion'):
            transcript.append('Choice: ' + ' '.join(args))
        elif command in ('fork', 'switchEvent'):
            transcript.append('Branch reference: ' + ' '.join(args))
        elif command in ('changeLocation', 'changeToTemporaryMap'):
            transcript.append('Scene changes to ' + ' '.join(args))
        elif command == 'cutscene':
            transcript.append('Game-controlled scene: ' + ' '.join(args))
        elif command in ('friendship', 'mail', 'addMailReceived', 'addConversationTopic', 'addWorldState'):
            transcript.append('Effect reference: ' + ' '.join(values))
        elif command == 'end' and len(args) >= 3 and args[0] == 'dialogue':
            transcript.append(args[1] + ' (after scene): ' + ' '.join(args[2:]))
        if blocked:
            continue
        beat = None
        if command == 'speak' and len(args) == 2 and args[0] in source_names and _safe_dialogue(args[1]):
            beat = new_beat('dialogue')
            beat.update(actor=args[0], text=args[1])
        elif command == 'message' and len(args) == 1 and 'farmer' in source_names and _safe_dialogue(args[0]):
            beat = new_beat('dialogue')
            beat.update(actor='farmer', text=args[0], source_narration=True)
        elif command == 'pause' and len(args) == 1 and _number(args[0]) is not None and 1 <= int(args[0]) <= 60000:
            beat = new_beat('pause')
            beat['duration'] = int(args[0])
        elif command == 'emote' and len(args) == 2 and args[0] in source_names and _number(args[1]) in EMOTES:
            beat = new_beat('emote')
            beat.update(actor=args[0], emote=int(args[1]))
        elif command == 'friendship' and len(args) == 2 and args[0] in source_names and args[0] != 'farmer' and _number(args[1]) is not None and -1000 <= int(args[1]) <= 1000:
            beat = new_beat('friendship')
            beat.update(actor=args[0], amount=int(args[1]))
        elif command == 'move' and len(args) == 4 and args[0] in positions and all(_number(v) is not None for v in args[1:]):
            x, y, facing = map(int, args[1:])
            end = [positions[args[0]][0] + x, positions[args[0]][1] + y]
            if -100 <= x <= 100 and -100 <= y <= 100 and (x == 0) != (y == 0) and facing in range(4) and all(0 <= v <= 1000 for v in end):
                beat = new_beat('move')
                beat.update(actor=args[0], x=x, y=y, facing=facing)
                positions[args[0]] = end
        elif command == 'end' and not args:
            blocked = True
            continue
        elif command == 'warp' and len(args) in (3, 4) and args[0] in source_names and all(_number(v) is not None for v in args[1:]):
            # Position-changing commands are not represented as fake relative
            # moves. Retain subsequent linear speech but suppress that actor's
            # movement until the author has rebuilt their staging.
            positions.pop(args[0], None)
            unsupported.add(command)
            continue
        if beat is None and command == 'move' and len(args) == 4 and args[0] in source_names and all(_number(v) is not None for v in args[1:]) and int(args[3]) in range(4):
            # A synchronous source move can be linear but outside this editor's
            # staging model. Omit it, preserve speech, and stop projecting later
            # offsets from the now-unknown position. Async/multi-actor moves do
            # not enter this path and remain a sequencing boundary.
            positions.pop(args[0], None)
            unsupported.add(command)
            continue
        if beat is not None and len(story['beats']) < 200:
            beat['id'] = _stable(entry_key, 'beat', index)
            story['beats'].append(beat)
        else:
            unsupported.add(command)
            # Known linear visuals/state effects are omitted with the guard.
            # This does not simulate hardcoded visuals or assume their effects
            # are exportable. Unknown
            # semantics, transfers, async moves, questions and forks end the
            # editable prefix, so conditional paths never become one sequence.
            if command not in omitted_linear:
                blocked = True
                warnings.append(f'Editable beats stop before command {index + 1} ({command}); later commands remain reference only.')
    if unsupported:
        warnings.append('Commands requiring review: ' + ', '.join(sorted(unsupported)))
    if not row['parts']:
        transcript = [row['script']]
    story['planned_effects'] = [new_planned_effect(_ADAPTATION)]
    story['planned_effects'][0]['id'] = _stable(entry_key, 'adaptation-review')
    return event, '\n'.join(transcript), list(dict.fromkeys(warnings))


def load_vanilla_story(template_id, game_root, *, cancelled=None):
    """Return all discovered relationship scenes and optional cast appearances.

    The base-language Data/Events files are inspected, including dependencies and
    branch scripts. Hardcoded scenes, mail text, recipes and dynamic game effects
    are not reconstructed. Discovery describes the installed files, not a promise
    that every narrative moment in the game is a Data/Events entry.
    """
    if not isinstance(template_id, str) or template_id not in VANILLA_NPCS:
        raise VanillaStoryError('Choose one of the twelve vanilla romanceable NPCs.')
    try:
        rows, assets = _catalog(game_root, cancelled)
    except (OSError, XnbError, RuntimeError) as exc:
        raise VanillaStoryError(str(exc)) from exc
    npc = {'id': template_id, **VANILLA_NPCS[template_id]}
    name = npc['name']
    ids, selected, facts = {}, {}, {}
    for index, row in enumerate(rows):
        ids.setdefault(row['event_id'], []).append(index)
        hearts, phase, direct, shared = _facts(row, name)
        facts[index] = hearts, phase
        if (template_id, row['location'], row['event_id']) in _OPTIONAL_SCENES:
            selected[index] = 'appearance'
        elif direct:
            selected[index] = 'appearance' if shared else 'heart_event'
        elif (template_id, row['location'], row['event_id']) in _RELATIONSHIP_SCENES and any(a['name'] == name for a in row['actors']):
            selected[index] = 'continuation'
        elif any(a['name'] == name for a in row['actors']):
            selected[index] = 'appearance'
    # Root relationship chains alone propagate automatic inclusion. Cameos must
    # not pull every other NPC's arc into the selected character's template.
    core = {index for index, category in selected.items() if category != 'appearance'}
    forward = set(core)

    def branch_matches(row):
        matches = []
        for target in row['branches']:
            candidates = ids.get(target, [])
            # A scene can change locations before forking; prefer the actual
            # named transfer, then the current asset, keeping ambiguity visible.
            destinations = [c[1] for c in row['commands'] if len(c) >= 2 and c[0] in ('changeLocation', 'changeToTemporaryMap')]
            locations = ['Temp' if any(c[0] == 'changeToTemporaryMap' for c in row['commands'] if c) else '', *destinations, row['location']]
            scoped = next(([i for i in candidates if rows[i]['location'] == location] for location in locations
                           if any(rows[i]['location'] == location for i in candidates)), candidates)
            matches.extend(scoped)
        for location, targets in row['dispatches']:
            matches.extend(i for target in targets for i in ids.get(target, []) if rows[i]['location'] == location)
        return matches
    while True:
        _check_cancelled(cancelled)
        additions = set()
        core_ids = {rows[index]['event_id'] for index in forward}
        for index in list(core):
            row = rows[index]
            for match in branch_matches(row):
                selected[match] = 'branch'
                additions.add(match)
            for group in _references(row['conditions']):
                for target in group:
                    for match in ids.get(target, []):
                        if match not in core:
                            selected[match] = 'continuation'
                            additions.add(match)
        for index, row in enumerate(rows):
            if index not in core and any(core_ids.intersection(group) for group in _references(row['conditions'])):
                _, _, _, shared = _facts(row, name)
                # Do not relabel scenes explicitly about other friendship
                # targets; they remain optional appearances if relevant.
                foreign = any(v and v[0] in ('f', 'Friendship') and name not in v[1::2] for v in row['conditions'])
                if not foreign and not shared and (template_id, row['location'], row['event_id']) not in _OPTIONAL_SCENES:
                    selected[index] = 'continuation'
                    additions.add(index)
                    forward.add(index)
        new = additions - core
        if not new:
            break
        core.update(new)
    # Optional appearances may have their own referenced branch data, still
    # optional, without broadening primary relationship discovery.
    for index in list(selected):
        for match in branch_matches(rows[index]):
            selected.setdefault(match, 'branch')

    def related_scripts(index):
        seen, pending = {index}, list(branch_matches(rows[index]))
        related, size = [], 0
        while pending:
            _check_cancelled(cancelled)
            target = pending.pop()
            if target in seen:
                continue
            seen.add(target)
            row = rows[target]
            size += len(row['script'].encode('utf-8')) + len(row['event_key'].encode('utf-8'))
            if size > _MAX_REFERENCE:
                raise VanillaStoryError('One scene has too much related script text to preserve safely. Select a smaller source catalog.')
            related.append({field: row[field] for field in ('asset', 'event_id', 'event_key', 'script', 'location')})
            pending.extend(branch_matches(row))
        return sorted(related, key=lambda row: (row['asset'], row['event_key']))

    events, reference_bytes = [], 0
    for index, category in selected.items():
        _check_cancelled(cancelled)
        row = rows[index]
        hearts, phase = facts[index]
        key = _stable(template_id, row['asset'], row['event_key'])
        event, transcript, warnings = _project(row, npc, key, hearts)
        dependencies = [{'kind': 'seen', 'event_ids': group, 'operator': 'or' if len(group) > 1 else 'single'}
                        for group in _references(row['conditions'])]
        dependencies.extend({'kind': 'branch', 'event_ids': [target]} for target in row['branches'])
        dependencies.extend({'kind': 'runtime_dispatch', 'location': location, 'event_ids': list(targets)}
                            for location, targets in row['dispatches'])
        for dependency in dependencies:
            for target in dependency['event_ids']:
                if target not in ids:
                    warnings.append(f'Source dependency {target} is absent from Data/Events; it may be supplied by mail or game logic.')
                elif len(ids[target]) > 1:
                    warnings.append(f'Source dependency {target} has multiple condition variants; no single automatic link is assumed.')
        source = {'game': 'Stardew Valley', 'asset': row['asset'], 'locale': 'default',
                  'game_version': None, 'sha256': row['sha256'], 'format': 'XNB String/String',
                  'attribution': 'Stardew Valley / ConcernedApe — locally supplied reference'}
        optional = index not in core or category in ('appearance', 'branch')
        label = f'{hearts:g} hearts' if hearts is not None else 'Branch' if category == 'branch' else 'Appearance' if category == 'appearance' else 'Follow-up'
        event['name'] = f'{label} · {location_name(row["location"])}'[:100]
        suggested_hearts = None
        if hearts is None and category not in ('appearance', 'branch'):
            if (template_id, row['location'], row['event_id']) in _RELATIONSHIP_SCENES:
                suggested_hearts = 8
            elif phase == 'married':
                suggested_hearts = 14
            elif phase == 'dating':
                suggested_hearts = 10
        if suggested_hearts is not None:
            warnings.append(f'This key has no friendship threshold. The {suggested_hearts}-heart chapter suggestion is planning only; draft access stays at 0 hearts.')
        phase_basis = 'source relationship condition' if any(v and v[0] in ('O', 'Spouse', 'D', 'Dating') and v[1:] == [name] for v in row['conditions']) else 'suggested chapter phase'
        provenance = {**source, 'npc': deepcopy(npc), 'entry_key': key, 'event_id': row['event_id'],
                      'event_key': row['event_key'], 'script': row['script'], 'location': row['location'],
                      'hearts': hearts, 'phase': phase, 'category': category, 'optional': optional,
                      'actors': deepcopy(row['actors']), 'camera': deepcopy(row['camera']), 'music': row['music'],
                      'transcript': transcript, 'warnings': warnings, 'dependencies': dependencies,
                      'suggested_hearts': suggested_hearts, 'phase_basis': phase_basis,
                      'related_scripts': related_scripts(index)}
        reference_bytes += len(json.dumps(provenance, ensure_ascii=False).encode('utf-8'))
        if reference_bytes > _MAX_TEXT:
            raise VanillaStoryError('The selected NPC has too much repeated reference data to preserve safely.')
        event['story']['vanilla_source'] = deepcopy(provenance)
        events.append({'key': key, 'event_id': row['event_id'], 'event_key': row['event_key'],
                       'script': row['script'], 'location': row['location'], 'hearts': hearts, 'phase': phase,
                       'suggested_hearts': suggested_hearts,
                       'category': category, 'optional': optional, 'event': event, 'transcript': transcript,
                       'warnings': warnings, 'source': source, 'dependencies': dependencies,
                       'source_event': {'location': row['location'], 'story': {'actors': deepcopy(row['actors']),
                                          'season': event['story']['season'], 'music': row['music'] or 'none'}}})
    events.sort(key=lambda item: (item['optional'], item['hearts'] if item['hearts'] is not None else 99,
                                  item['location'], item['event_key']))
    return {'npc': npc, 'events': events,
            'source': {'game': 'Stardew Valley', 'locale': 'default', 'game_version': None,
                       'assets': assets, 'asset_count': len(assets),
                       'attribution': 'Stardew Valley / ConcernedApe — locally supplied reference',
                       'limitations': 'Base Data/Events only. Mail, recipes, hardcoded scenes, temporary maps and dynamic effects may require manual adaptation.'}}


def _source_size(source, entry, npc):
    if not isinstance(source, dict) or source.get('entry_key') != entry['key'] or source.get('npc') != npc:
        raise VanillaStoryError('A selected scene has missing or mismatched original provenance.')
    for field, maximum in (('script', 65536), ('event_key', 65536), ('event_id', 65536), ('location', 80)):
        if not _valid_text(source.get(field), maximum) or source[field] != entry.get(field):
            raise VanillaStoryError('The original scene reference is invalid or no longer matches the selected source.')
    if not _valid_text(source.get('transcript'), 131072):
        raise VanillaStoryError('The source transcript is invalid or oversized.')
    actors = source.get('actors')
    if not isinstance(actors, list) or len(actors) > 256:
        raise VanillaStoryError('The original cast is invalid or oversized.')
    for actor in actors:
        if (not isinstance(actor, dict) or not _valid_text(actor.get('name'), 192)
                or any(type(actor.get(axis)) is not int or abs(actor[axis]) > 999999999 for axis in ('x', 'y'))
                or type(actor.get('facing')) is not int or actor['facing'] not in range(4)):
            raise VanillaStoryError('The original cast contains invalid staging.')
    related = source.get('related_scripts', [])
    if not isinstance(related, list) or len(related) > _MAX_ENTRIES or any(
            not isinstance(row, dict) or any(not _valid_text(row.get(field), maximum)
                for field, maximum in (('asset', 256), ('event_id', 65536), ('event_key', 65536), ('script', 65536), ('location', 80)))
            for row in related):
        raise VanillaStoryError('The related original scripts are invalid or oversized.')
    try:
        size = len(json.dumps(source, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise VanillaStoryError('The source reference must contain valid JSON data.') from exc
    if size > _MAX_REFERENCE:
        raise VanillaStoryError('The source reference exceeds the supported size limit.')
    return size


def apply_vanilla_story(character, bundle, event_keys, *, replace_starter=False):
    """Append selected drafts atomically, preserving authored data and identity."""
    _check_character(character)
    if (not isinstance(bundle, dict) or not isinstance(bundle.get('npc'), dict)
            or not isinstance(bundle['npc'].get('id'), str) or bundle['npc']['id'] not in VANILLA_NPCS):
        raise VanillaStoryError('Choose a loaded vanilla NPC template.')
    npc = bundle['npc']
    if npc.get('name') != VANILLA_NPCS[npc['id']]['name']:
        raise VanillaStoryError('The source NPC identity is invalid.')
    rows = bundle.get('events')
    if not isinstance(rows, list) or len(rows) > _MAX_ENTRIES or any(not isinstance(row, dict) or not row.get('key') or not _valid_text(row.get('key'), 100) for row in rows):
        raise VanillaStoryError('The template has invalid event entries.')
    by_key = {row['key']: row for row in rows}
    if any(any(not _valid_text(row.get(field), maximum) for field, maximum in
                   (('event_id', 65536), ('event_key', 65536), ('script', 65536), ('location', 80))) for row in rows):
        raise VanillaStoryError('The template has invalid original event references.')
    if len(by_key) != len(rows) or not isinstance(event_keys, (list, tuple)) or any(not isinstance(key, str) or key not in by_key for key in event_keys):
        raise VanillaStoryError('Choose valid, unique source events from this template.')
    chosen = list(dict.fromkeys(event_keys))
    if not chosen:
        return deepcopy(character)
    result = deepcopy(character)
    if replace_starter:
        from .projects import clear_story_starter
        result = clear_story_starter(result)
    identity = result.get('id') or result.get('internal_name') or 'character'
    if not _valid_text(identity, 192):
        raise VanillaStoryError('Save a stable character identity before importing.')
    result.setdefault('events', [])
    result.setdefault('relationships', [])
    result['storyline'] = normalize_storyline(result.get('storyline'))
    existing = {}
    for event in result['events']:
        source = event.get('story', {}).get('vanilla_source', {})
        if isinstance(source, dict) and isinstance(source.get('entry_key'), str):
            if not isinstance(event.get('id'), str):
                raise VanillaStoryError('An existing imported scene needs a stable ID before importing again.')
            existing[source['entry_key']] = event['id']
    event_ids = {event.get('id') for event in result['events']}
    chapter_ids = {chapter.get('id') for chapter in result['storyline']['chapters']}
    arc_id = _stable(identity, npc['id'], 'arc')
    existing_arc = next((arc for arc in result['relationships'] if arc.get('id') == arc_id), None)
    if existing_arc is not None and existing_arc.get('story', {}).get('vanilla_npc') != npc['id']:
        raise VanillaStoryError('An authored arc already uses the template identity.')
    additions, reference_bytes = [], 0
    for key in chosen:
        if key in existing:
            continue
        entry = by_key[key]
        event = deepcopy(entry.get('event'))
        if not isinstance(event, dict) or structure_issues(event) or not isinstance(event.get('story'), dict):
            raise VanillaStoryError('A selected editable draft has invalid authoring data.')
        source = event['story'].get('vanilla_source')
        reference_bytes += _source_size(source, entry, npc)
        if reference_bytes > _MAX_TEXT:
            raise VanillaStoryError('The selected scenes exceed the total reference size limit.')
        event_id, chapter_id = _stable(identity, key, 'event'), _stable(identity, key, 'chapter')
        if event_id in event_ids or chapter_id in chapter_ids:
            raise VanillaStoryError('An authored record already uses a selected template identity.')
        event.update(id=event_id)
        story = event['story']
        story.update(stage='outline', relationship_id='', previous_event_id='', arc_ids=[arc_id])
        for actor in story.get('actors', []):
            if actor['name'] == npc['name']:
                actor['name'] = '$npc'
        for beat in story.get('beats', []):
            if beat['actor'] == npc['name']:
                beat['actor'] = '$npc'
        # Review cannot be bypassed by editing the preview's guard or stage.
        guard = new_planned_effect(_ADAPTATION)
        guard['id'] = _stable(event_id, 'adaptation-review')
        story['planned_effects'] = [effect for effect in story.get('planned_effects', [])
                                    if effect.get('description') != _ADAPTATION] + [guard]
        chapter = new_chapter(event['name'])
        suggested = source.get('suggested_hearts')
        chapter.update(id=chapter_id, phase=entry.get('phase', 'any'), hearts=suggested if type(suggested) is int and 0 <= suggested <= 14 else event['hearts'],
                       purpose='Study and adapt the preserved vanilla scene.', arc_ids=[arc_id], event_ids=[event_id])
        additions.append((event, chapter, entry))
        existing[key] = event_id
    if not additions:
        return result
    if len(result['events']) + len(additions) > 100 or len(result['storyline']['chapters']) + len(additions) > 100 or len(result['relationships']) + (existing_arc is None) > 100:
        raise VanillaStoryError('The selected template would exceed the 100-event, chapter or arc limit. Select fewer scenes.')
    if existing_arc is None:
        arc = new_relationship()
        arc.update(id=arc_id, name=f"Inspired by {npc['name']}", description='A local vanilla reference adapted into an original relationship arc.')
        arc['story'].update(stage='outline', target='farmer', vanilla_npc=npc['id'])
        result['relationships'].append(arc)
    # Full keys distinguish condition variants; an ID with multiple catalog
    # candidates is deliberately never guessed, even if only one was selected.
    source_ids = {}
    for entry in rows:
        source_ids.setdefault(entry.get('event_id'), []).append(entry['key'])
    for event, chapter, entry in additions:
        groups = _references(_conditions(entry['event_key'])[1])
        if len(groups) == 1 and len(groups[0]) == 1:
            matches = source_ids.get(groups[0][0], [])
            if len(matches) == 1 and matches[0] in chosen and matches[0] in existing and existing[matches[0]] != event['id']:
                event['story']['previous_event_id'] = existing[matches[0]]
        result['events'].append(event)
        result['storyline']['chapters'].append(chapter)
    by_id = {event.get('id'): event for event in result['events'] if isinstance(event.get('id'), str)}
    for event, _, _ in additions:
        seen, current = {event['id']}, event['story']['previous_event_id']
        while current and current in by_id:
            if current in seen:
                event['story']['previous_event_id'] = ''
                event['story']['vanilla_source']['warnings'].append('A cyclic source prerequisite was retained as a reference instead of an editor link.')
                break
            seen.add(current)
            current = by_id[current].get('story', {}).get('previous_event_id', '')
    _check_character(result)
    return result


def _vanilla_initialization(character, bundle, event_keys):
    """Build a replacement and its impact together, without mutating inputs."""
    from .life import COLLECTIONS, life_structure_issues

    _check_character(character)
    life = character.get('life', {})
    issues = life_structure_issues(life)
    if issues:
        raise VanillaStoryError('Correct the daily-life data before initializing a story.', issues=issues)
    if not isinstance(event_keys, (list, tuple)) or not event_keys:
        raise VanillaStoryError('Select at least one vanilla scene to initialize the story.')
    replacement = deepcopy(character)
    replacement['events'] = []
    replacement['relationships'] = []
    replacement['storyline'] = normalize_storyline(replacement.get('storyline'))
    replacement['storyline']['chapters'] = []
    replacement['storyline'].pop('starter_origin', None)
    # Reuse all append validation, source retention, stable identities and draft
    # guards. Building before reporting impact makes the preview reviewable and
    # ensures bad selections or limits can never cause a partial replacement.
    replacement = apply_vanilla_story(replacement, bundle, event_keys)
    old_events = {event.get('id'): event for event in character.get('events', [])
                  if isinstance(event.get('id'), str)}
    new_events = {event['id']: event for event in replacement['events']}
    links, blocked_issues = [], []
    labels = {'dialogues': 'Dialogue', 'routines': 'Routine', 'spouse_dialogue': 'Married dialogue'}
    for kind in COLLECTIONS:
        for index, rule in enumerate(life.get(kind, [])):
            event_id = rule.get('conditions', {}).get('after_event_id', '')
            if not event_id:
                continue
            retained = event_id in new_events
            event = old_events.get(event_id, new_events.get(event_id, {}))
            field = f'life.{kind}.{index}.conditions.after_event_id'
            rule_name, event_name = rule.get('name') or 'Untitled rule', event.get('name') or 'Missing event'
            links.append({'field': field, 'kind': kind, 'rule_name': rule_name,
                          'event_id': event_id, 'event_name': event_name,
                          'enabled': rule.get('enabled', False), 'status': 'retained' if retained else 'blocked'})
            if not retained:
                blocked_issues.append({'level': 'error', 'field': field,
                    'message': f'{labels[kind]} “{rule_name}” follows “{event_name}”, which is absent from the replacement. '
                               'Relink or clear that after-event condition before initializing the story.'})
    warnings = ['The selected scenes replace the current story as drafts. Review their preserved vanilla conditions and effects before making them ready.']
    retained_count = sum(link['status'] == 'retained' for link in links)
    if retained_count:
        warnings.append(f'{retained_count} daily-life link(s) keep their exact event IDs. These events reset to drafts; linked enabled rules need those scenes ready before export.')
    if blocked_issues:
        warnings.append(f'{len(blocked_issues)} daily-life link(s) would lose their event. Initialization is blocked until those links are resolved; no links are cleared automatically.')
    impact = {'npc': deepcopy(bundle['npc']), 'selected_count': len(replacement['events']),
              'replace_counts': {'events': len(character.get('events', [])),
                                 'chapters': len(character.get('storyline', {}).get('chapters', [])),
                                 'relationships': len(character.get('relationships', []))},
              'create_counts': {'events': len(replacement['events']),
                                'chapters': len(replacement['storyline']['chapters']),
                                'relationships': len(replacement['relationships'])},
              'life_links': links, 'blocked': bool(blocked_issues), 'warnings': warnings,
              'issues': blocked_issues}
    return replacement, impact


def preview_vanilla_initialization(character, bundle, event_keys):
    """Report concrete replacement counts and affected Life links, read-only.

    Events, chapters and arcs will be replaced. The character brief, all other
    character fields and unknown storyline extensions survive. Missing target
    IDs block initialization, including links in disabled daily-life rules.
    """
    return _vanilla_initialization(character, bundle, event_keys)[1]


def initialize_vanilla_story(character, bundle, event_keys):
    """Return an explicitly initialized story while preserving nonstory data.

    Recompute impact at application time; a stale UI preview cannot bypass a new
    dependency. No daily-life rule is silently cleared, disabled or retargeted.
    This function never reads or writes a project, artwork, map or export file.
    """
    replacement, impact = _vanilla_initialization(character, bundle, event_keys)
    if impact['blocked']:
        raise VanillaStoryError(' '.join(issue['message'] for issue in impact['issues']), issues=impact['issues'])
    return replacement
