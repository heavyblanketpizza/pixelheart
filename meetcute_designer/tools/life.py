"""Their life beyond the first meeting: married-life lines, conditional lines and conditional routines, as daily-life rules.

All three tools write ``character["life"]`` records through the core's own validator. A rule that is valid
goes into the mod at once; one that waits on a heart event the creator has not marked ready is kept as
a draft, the same state the app's own editors start from.
"""
from __future__ import annotations

import uuid

from meetcute_core.game_clock import clock_label, game_minutes, game_time
from meetcute_core.life import (MOMENTS, SEASONS, WEEKDAYS, life_issues, life_structure_issues, new_conditions,
                                  new_life_record, normalize_life)

from ..providers import ToolSpec
from ..summary import condition_captions
from . import ToolError, ToolOutcome
from .dialogue import check_text, provenance_source
from .routine import STOPS_SCHEMA, build_stops, stops_detail


MAX_LINES = 20
MAX_RULES = 100                       # the core's cap per collection
WEATHERS = ("sunny", "rainy", "snowy")
RELATIONSHIPS = ("unmarried", "dating", "married")
READY_FIRST = "Mark the required story event ready before including this rule."   # the core's wording
CAPTIONS = {"dialogues": "Story reactions", "spouse_dialogue": "Marriage dialogue"}
DAY_END = 1560                        # 2:00 AM in game minutes; nothing can follow a stop there
# The app's "+ Return to farmhouse": the game's own bed destination, so no farmhouse tile is guessed.
FARMHOUSE = {"location": "bed", "x": 0, "y": 0, "facing": "down", "activity": "Return to the spouse's home"}


def _nullable_enum(choices, description):
    return {"anyOf": [{"type": "string", "enum": list(choices)}, {"type": "null"}], "description": description}


def _nullable_int(description):
    return {"type": ["integer", "null"], "description": description}


def _object(properties, description=None):
    schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    if description:
        schema["description"] = description
    return schema


_SEASON = _nullable_enum(SEASONS, "Only in this season.")
_WEEKDAY = _nullable_enum(WEEKDAYS, "Only on this weekday.")
_HEARTS = _nullable_int("Only at this many hearts or more, 0 to 14.")
_AFTER = _nullable_int("Only after the farmer has seen the heart event at this level (see Heart events).")
_HOUSE = _nullable_int("Only once the farmhouse has this upgrade or more: 1 kitchen, 2 family home, 3 cellar.")
HOME_WHEN = _object({"season": _SEASON, "weekday": _WEEKDAY, "min_hearts": _HEARTS, "after_event_hearts": _AFTER, "min_house_upgrade": _HOUSE},
                    "Narrow the line to a season, weekday, friendship level, finished heart event or farmhouse size; null for any.")


def _when(relationship):
    return _object({
        "season": _SEASON,
        "weather": _nullable_enum(WEATHERS, "Only in this weather."),
        "weekday": _WEEKDAY,
        "min_hearts": _HEARTS,
        "relationship": _nullable_enum(RELATIONSHIPS, "Only while not married to, dating, or married to the farmer. " + relationship),
        "after_event_hearts": _AFTER,
        "min_house_upgrade": _HOUSE,
    }, "At least one condition; null means any.")


WHEN = _when("married moves the line to their conversations at home.")
ROUTINE_WHEN = _when("A married routine ends with the game's return to the farmhouse.")

MARRIED_SPEC = ToolSpec(
    "write_married_life",
    "Write what they say at home after the wedding: a morning, rainy morning, evening or rainy evening line, each "
    "filling every random slot for that moment. Write all four at once. Each needs romance to be open. "
    "Use 'when' only for a more specific version, such as a winter morning or one after a heart event.",
    _object({"lines": {"type": "array", "description": f"1 to {MAX_LINES} lines.",
                       "items": _object({"moment": {"type": "string", "enum": list(MOMENTS)},
                                         "text": {"type": "string"},
                                         "when": {"anyOf": [HOME_WHEN, {"type": "null"}]}})}}),
)
CONDITIONAL_SPEC = ToolSpec(
    "write_conditional_lines",
    "Write lines that replace the weekday greetings while a condition holds: a season, weather, weekday, minimum "
    "hearts, dating or married, after a heart event, or a farmhouse size. Later rules win over earlier ones. "
    "Give each a short name the creator will recognise. Lines that always apply belong in write_dialogue.",
    _object({"rules": {"type": "array", "description": f"1 to {MAX_LINES} rules.",
                       "items": _object({"name": {"type": "string", "description": "Up to 100 characters."},
                                         "text": {"type": "string"},
                                         "when": WHEN})}}),
)
ROUTINE_SPEC = ToolSpec(
    "plan_conditional_routine",
    "Plan a routine that replaces their everyday one while a condition holds: rainy days, a season, a weekday, a "
    "friendship level, dating or married, after a heart event, or a farmhouse size. Give the whole day's stops in "
    "time order, with the same times and place ids as plan_routine. A married routine ends with the game's return to "
    "the farmhouse automatically. A routine with the same conditions is rewritten; later routines win over earlier ones. "
    "The creator places each stop on the map afterwards.",
    _object({"when": ROUTINE_WHEN, "stops": STOPS_SCHEMA}),
)


def _life(character):
    raw = character.get("life") if isinstance(character.get("life"), dict) else {}
    issues = life_structure_issues(raw)
    if issues:
        raise ToolError("The daily-life rules in this project need fixing in the app first: " + issues[0]["message"])
    return normalize_life(raw)


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _events(character):
    return _rows(character.get("events"))


def _conditions(when, events, *, home):
    """The model's plain conditions as the core's condition record, or a ToolError it can act on."""
    allowed = HOME_WHEN["properties"] if home else WHEN["properties"]
    when = {} if when is None else when
    if not isinstance(when, dict):
        raise ToolError("'when' must be an object or null.")
    unknown = sorted(set(when) - set(allowed))
    if unknown:
        raise ToolError("Unknown conditions: " + ", ".join(unknown) + ".")
    result = new_conditions()
    if home:
        result["relationship"] = "married"
    for key, choices in (("season", SEASONS), ("weather", WEATHERS), ("weekday", WEEKDAYS), ("relationship", RELATIONSHIPS)):
        value = when.get(key)
        if value is None:
            continue
        if value not in choices:
            raise ToolError(f"Choose {key} from " + ", ".join(choices) + ".")
        result[key] = value
    for key, limit in (("min_hearts", 14), ("min_house_upgrade", 3)):
        value = when.get(key)
        if value is None:
            continue
        if type(value) is not int or not 0 <= value <= limit:
            raise ToolError(f"Give {key} as a whole number from 0 to {limit}.")
        result[key] = value
    hearts = when.get("after_event_hearts")
    if hearts is not None:
        if type(hearts) is not int:
            raise ToolError("Give after_event_hearts as a whole number.")
        event = next((row for row in events if row.get("hearts") == hearts and isinstance(row.get("id"), str)), None)
        if event is None:
            raise ToolError(f"There is no heart event at {hearts} hearts. Write it first with write_heart_event.")
        result["after_event_id"] = event["id"]
    return result


def _key(conditions):
    return tuple(sorted((str(key), str(value)) for key, value in conditions.items()))


def _name(caption, conditions, events, *, home=False):
    """"Caption · conditions in words"; at home, being married is understood and left out."""
    parts = [part for part in condition_captions(conditions, events) if not (home and part == "married")]
    return (caption + (" · " + ", ".join(parts) if parts else ""))[:100]


def _return_home(stops):
    """The app's "+ Return to farmhouse" stop: an hour after the last stop, 10:00 PM at the earliest, or None once the day is over."""
    last = game_minutes(stops[-1]["time"])
    if last is None or last >= DAY_END:
        return None
    return {"id": str(uuid.uuid4()), "time": game_time(max(1320, min(DAY_END, last + 60))), **FARMHOUSE}


def _write(character, life, kind, planned, keyer):
    """Apply planned records: rewrite the one with the same key, else append. Returns (rewritten, pending indexes)."""
    records, rewritten, pending = life[kind], 0, []
    for key, fields in planned:
        index = next((i for i, row in enumerate(records) if keyer(row) == key), None)
        if index is None:
            if len(records) >= MAX_RULES:
                raise ToolError(f"This character already has {MAX_RULES} rules of this kind; remove some in the app first.")
            records.append(new_life_record(kind, character))
            index = len(records) - 1
        else:
            rewritten += 1
        records[index].update(fields, enabled=True)
        pending.append(index)
    character["life"] = life
    return rewritten, pending


def _settle(character, kind, pending):
    """Validate the written rules through the core; a rule only waiting on its event becomes a draft."""
    records = character["life"][kind]
    by_index = {}
    for issue in life_issues(character):
        if issue["level"] != "error" or not issue["field"].startswith(f"life.{kind}."):
            continue
        index = issue["field"].split(".")[2]
        if index.isdigit() and int(index) in pending:
            by_index.setdefault(int(index), []).append(issue["message"])
    drafts = []
    for index, messages in by_index.items():
        if set(messages) == {READY_FIRST}:
            records[index]["enabled"] = False
            drafts.append(index)
        else:
            raise ToolError("; ".join(dict.fromkeys(messages)))
    return drafts


def _draft_notes(character, kind, drafts, *, subject=None):
    """What the receipt detail and the message gain when written rules wait on heart events: ("", "") when none do.

    ``subject`` names a single written rule ("It is a draft"); without it the drafts are counted.
    """
    if not drafts:
        return "", ""
    count = len(drafts)
    events = {row["id"]: row for row in _events(character) if isinstance(row.get("id"), str)}
    levels = sorted({events[character["life"][kind][index]["conditions"]["after_event_id"]].get("hearts", 0) for index in drafts})
    which = (f"the {levels[0]}-heart event" if len(levels) == 1
             else "the " + ", ".join(f"{level}-heart" for level in levels) + " events")
    if subject is None:
        detail = f" · {count} draft{'' if count == 1 else 's'} until {'its event is' if count == 1 else 'their events are'} ready"
        subject = f"{count} {'is a draft' if count == 1 else 'are drafts'}"
    else:
        detail = " · draft until its event is ready"
    message = f" {subject} that will play once the creator marks {which} ready and ticks Include this rule in the next mod export in the app."
    return detail, message


def _outcome(document, kind, count, rewritten, drafts):
    detail = f"{count} line{'' if count == 1 else 's'} written"
    if rewritten:
        detail += f" · {rewritten} rewritten"
    more_detail, more_message = _draft_notes(document["character"], kind, drafts)
    receipt = {"section": "dialogue", "title": "Updated " + CAPTIONS[kind], "detail": detail + more_detail}
    return ToolOutcome(document, receipt, f"Done. {detail}." + more_message)


def write_married_life(document, input, context):
    lines = input.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise ToolError(f"Give between 1 and {MAX_LINES} lines.")
    character = document["character"]
    life, events, source = _life(character), _events(character), provenance_source(context.model_label)
    planned, seen = [], {}
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            raise ToolError(f"Line {index} must be an object with moment, text and when.")
        moment = line.get("moment")
        if moment not in MOMENTS:
            raise ToolError(f"Line {index}: choose moment from " + ", ".join(MOMENTS) + ".")
        check_text(line.get("text"), index)
        conditions = _conditions(line.get("when"), events, home=True)
        key = (moment, _key(conditions))
        if key in seen:
            raise ToolError(f"Lines {seen[key]} and {index} are for the same moment with the same conditions.")
        seen[key] = index
        planned.append((key, {"moment": moment, "text": line["text"], "name": _name(MOMENTS[moment], conditions, events, home=True),
                              "conditions": conditions, "source": source}))
    rewritten, pending = _write(character, life, "spouse_dialogue", planned, lambda row: (row.get("moment"), _key(row["conditions"])))
    drafts = _settle(character, "spouse_dialogue", pending)
    return _outcome(document, "spouse_dialogue", len(planned), rewritten, drafts)


def write_conditional_lines(document, input, context):
    rules = input.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_LINES:
        raise ToolError(f"Give between 1 and {MAX_LINES} rules.")
    character = document["character"]
    life, events, source = _life(character), _events(character), provenance_source(context.model_label)
    planned, seen = [], {}
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ToolError(f"Rule {index} must be an object with name, text and when.")
        name = rule.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ToolError(f"Rule {index}: give a short name, up to 100 characters.")
        check_text(rule.get("text"), index)
        conditions = _conditions(rule.get("when"), events, home=False)
        if conditions == new_conditions():
            raise ToolError(f"Rule {index}: give at least one condition; lines that always apply belong in write_dialogue.")
        key = _key(conditions)
        if key in seen:
            raise ToolError(f"Rules {seen[key]} and {index} have the same conditions.")
        seen[key] = index
        planned.append((key, {"name": name.strip(), "text": rule["text"], "conditions": conditions, "source": source}))
    rewritten, pending = _write(character, life, "dialogues", planned, lambda row: _key(row["conditions"]))
    drafts = _settle(character, "dialogues", pending)
    return _outcome(document, "dialogues", len(planned), rewritten, drafts)


def plan_conditional_routine(document, input, context):
    character = document["character"]
    life, events = _life(character), _events(character)
    conditions = _conditions(input.get("when"), events, home=False)
    if conditions == new_conditions():
        raise ToolError("Give at least one condition; the everyday routine belongs in plan_routine.")
    key = _key(conditions)
    current = next((row for row in life["routines"] if _key(row["conditions"]) == key), None)
    # Tiles the creator has placed beat the defaults: this routine's own first, then the everyday one, then the rest.
    placed = [*(_rows(current["stops"]) if current is not None else []), *_rows(character.get("schedule")),
              *(stop for row in life["routines"] if row is not current for stop in _rows(row["stops"]))]
    stops = build_stops(document, input.get("stops"), placed)
    home = _return_home(stops) if conditions["relationship"] == "married" else None
    if home is not None:
        stops.append(home)
    name = _name("Routine", conditions, events)
    rewritten, pending = _write(character, life, "routines", [(key, {"name": name, "stops": stops, "conditions": conditions})],
                                lambda row: _key(row["conditions"]))
    drafts = _settle(character, "routines", pending)
    detail = stops_detail(len(stops))
    message = (f"Done. {detail}. '{name}' replaces their everyday routine on the days it matches; stops stand on a default "
               "tile until the creator places them in Daily routine → Conditional routines.")
    if rewritten:
        message += " It took the place of the routine that had the same conditions."
    if home is not None:
        message += f" It ends with the game's return to the farmhouse at {clock_label(game_minutes(home['time']))}."
    elif conditions["relationship"] == "married":
        message += (" It does not finish at home: the last stop is already at 2:00 AM, so move it earlier "
                    "if they should sleep at the farmhouse.")
    more_detail, more_message = _draft_notes(character, "routines", drafts, subject="It is a draft")
    receipt = {"section": "schedule", "title": "Updated Conditional routines", "detail": detail + more_detail}
    return ToolOutcome(document, receipt, message + more_message)


TOOLS = ((MARRIED_SPEC, write_married_life), (CONDITIONAL_SPEC, write_conditional_lines), (ROUTINE_SPEC, plan_conditional_routine))
