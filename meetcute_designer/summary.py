"""A compact plain-text picture of a character for the designer's context block."""
from __future__ import annotations

from meetcute_core.life import COLLECTIONS, MOMENTS, new_conditions, normalize_life

from .catalog import item_names


MAX_DIALOGUE_LINES = 80
MIN_DIALOGUE_LINES = 8
MAX_STOPS = 40
MAX_EVENTS = 30
MAX_RULES = 30
RULE_CAPTIONS = {"dialogues": "conditional line", "routines": "routine", "spouse_dialogue": "married life"}


def _cut(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def condition_captions(conditions, events=()) -> list[str]:
    """A daily-life rule's conditions in plain words, in the core's order; empty when it always applies."""
    conditions = {**new_conditions(), **(conditions if isinstance(conditions, dict) else {})}
    parts = []
    for key in ("season", "weather", "weekday"):
        if conditions[key] != "any":
            parts.append(str(conditions[key]))
    if conditions["min_hearts"]:
        parts.append(f"{conditions['min_hearts']}+ hearts")
    if conditions["relationship"] != "any":
        parts.append(str(conditions["relationship"]))
    if conditions["after_event_id"]:
        event = next((row for row in events if isinstance(row, dict) and row.get("id") == conditions["after_event_id"]), None)
        parts.append(f"after {event.get('name') or 'an untitled event'} ({event.get('hearts', '?')} hearts)" if event else "after a missing event")
    if conditions["min_house_upgrade"]:
        parts.append(f"house upgrade {conditions['min_house_upgrade']}+")
    return parts


def _rule_line(kind, row, events):
    parts = []
    if kind == "spouse_dialogue":
        parts.append(MOMENTS.get(row.get("moment"), "Morning at home").lower())
    parts.extend(part for part in condition_captions(row.get("conditions"), events) if not (kind == "spouse_dialogue" and part == "married"))
    tail = _plural(len(_rows(row.get("stops"))), "stop") if kind == "routines" else _cut(row.get("text", ""), 80)
    return (f"  {RULE_CAPTIONS[kind]} · {row.get('name') or 'Untitled'} · {'in mod' if row.get('enabled') else 'draft'}"
            f" · {', '.join(parts) or 'always'} · {tail}")


def summarize(document: dict, *, limit: int = 10000) -> str:
    document = document if isinstance(document, dict) else {}
    character = document.get("character") if isinstance(document.get("character"), dict) else {}
    get = character.get
    lines = [
        f"Name: {get('name', '')} (internal {get('internal_name', '')}) · {get('pronouns', '')} · {get('gender', '')}",
        f"Tagline: {get('tagline', '')}",
        f"Occupation: {get('occupation', '')}",
        f"Birthday: {str(get('season', 'spring')).title()} {get('day', 1)}",
        f"Personality: manners {get('manners', 'neutral')} · social anxiety {get('social_anxiety', 'neutral')}"
        f" · optimism {get('optimism', 'neutral')} · romance {'open' if get('romanceable', True) else 'not open'}",
        f"Home: {get('home_map', '')} ({get('home_x', '')}, {get('home_y', '')})",
        f"Bio: {_cut(get('bio', ''), 400)}",
        "Storyline brief:",
    ]
    storyline = get("storyline") if isinstance(get("storyline"), dict) else {}
    brief = storyline.get("brief") if isinstance(storyline.get("brief"), dict) else {}
    brief_lines = [f"  {field}: {_cut(brief[field], 400)}" for field in ("desire", "question", "boundaries", "motif")
                   if str(brief.get(field, "")).strip()]
    lines.extend(brief_lines or ["  (empty)"])

    dialogues = _rows(get("dialogues"))
    head = lines
    dialogue_lines = [f"  {row.get('trigger', '')}: {_cut(row.get('text', ''), 80)}" for row in dialogues]
    lines = []

    names = item_names(document)
    gifts = get("gifts") if isinstance(get("gifts"), dict) else {}
    tastes = []
    for taste in ("love", "like", "dislike", "hate"):
        ids = [item for item in gifts.get(taste, []) if isinstance(item, str)] if isinstance(gifts.get(taste), list) else []
        tastes.append(f"{taste}: {', '.join(names.get(item, item) for item in ids) if ids else 'none'}")
    lines.append("Gifts: " + " · ".join(tastes))

    stops = _rows(get("schedule"))
    lines.append(f"Daily routine ({_plural(len(stops), 'stop')}):")
    lines.extend(f"  {row.get('time', '')} {row.get('location', '')} {_cut(row.get('activity', ''), 80)}".rstrip()
                 for row in stops[:MAX_STOPS])
    if len(stops) > MAX_STOPS:
        lines.append(f"  …and {len(stops) - MAX_STOPS} more stops")

    events = _rows(get("events"))
    lines.append(f"Heart events ({len(events)}):")
    if len(events) > MAX_EVENTS:
        lines.append(f"  (first {MAX_EVENTS} shown)")
    for event in events[:MAX_EVENTS]:
        story = event.get("story") if isinstance(event.get("story"), dict) else {}
        lines.append(f"  {event.get('hearts', '')} hearts · {event.get('name', '')} · {story.get('stage', 'idea')}"
                     f" · {event.get('location', '')} · {_cut(story.get('premise', ''), 120)}")

    life = normalize_life(get("life") if isinstance(get("life"), dict) else {})
    rules = [(kind, row) for kind in COLLECTIONS for row in _rows(life.get(kind))]
    lines.append(f"Daily life ({_plural(len(rules), 'rule')}):")
    lines.extend(_rule_line(kind, row, events) for kind, row in rules[:MAX_RULES])
    if len(rules) > MAX_RULES:
        lines.append(f"  …and {len(rules) - MAX_RULES} more rules")

    world = document.get("world") if isinstance(document.get("world"), dict) else {}
    places = [row for row in _rows(world.get("locations")) if row.get("internal_name") and not row.get("spouse_room")]
    lines.append(f"Places you designed: {len(places)}")

    artwork = document.get("artwork") if isinstance(document.get("artwork"), dict) else {}
    lines.append(f"Artwork: portrait {'yes' if artwork.get('portrait') else 'no'} · sprite {'yes' if artwork.get('sprite') else 'no'}")

    tail = lines

    def assemble(shown):
        conversation = [f"Conversations ({_plural(len(dialogues), 'line')}):", *dialogue_lines[:shown]]
        if len(dialogue_lines) > shown:
            conversation.append(f"  …and {len(dialogue_lines) - shown} more lines")
        return "\n".join([*head, *conversation, *tail])

    shown = min(len(dialogue_lines), MAX_DIALOGUE_LINES)
    text = assemble(shown)
    # Everyday lines are the bulkiest section; trim them before anything after them is lost.
    while len(text) > limit and shown > MIN_DIALOGUE_LINES:
        shown = max(MIN_DIALOGUE_LINES, shown // 2)
        text = assemble(shown)
    if len(text) > limit:
        text = text[:max(0, limit - 7)] + "…(more)"
    return text
