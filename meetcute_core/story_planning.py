"""Local story organization. Chapter order and arc links never become triggers.

All public helpers copy their inputs. Existing event identities and executable
conditions are preserved; starters only append explicitly selected new drafts.
"""
from __future__ import annotations

from copy import deepcopy
import json
import uuid


CHAPTER_PHASES = ("any", "friendship", "dating", "married")
CHAPTER_PATTERNS = ("blank", "first_meeting", "conflict", "reconciliation", "shared_activity",
                    "private_side", "boundary", "help_with_cost", "public_step",
                    "remembered_preference", "mutual_invitation", "life_together")
MAX_CHAPTERS = 100
_FRIENDSHIP = ("shared_interest", "understanding", "trust", "friendship_payoff")
STORY_STARTERS = {
    "friendship": {"name": "Friendship", "description": "A complete friendship with flexible chapters.",
                   "milestones": _FRIENDSHIP},
    "romance": {"name": "Friendship and romance", "description": "Friendship, dating, and life together.",
                "milestones": (*_FRIENDSHIP, "romantic_decision", "shared_life")},
    "dating": {"name": "Dating continuation", "description": "Add a mutual romantic decision.",
               "milestones": ("romantic_decision",)},
    "married": {"name": "Married life", "description": "Make room for love and independent lives.",
                "milestones": ("shared_life",)},
}
_MILESTONES = {
    "shared_interest": ("A shared interest", 2, "friendship", "shared_activity", "What can we do together?"),
    "understanding": ("A more specific understanding", 4, "friendship", "private_side", "What complicates the first impression?"),
    "trust": ("Growing trust", 6, "friendship", "shared_activity", "What becomes possible because of this bond?"),
    "friendship_payoff": ("A friendship that matters", 8, "friendship", "public_step", "What has changed even if we never date?"),
    "romantic_decision": ("Choosing each other", 10, "dating", "mutual_invitation", "Why do these people choose each other?"),
    "shared_life": ("A life together", 14, "married", "life_together", "How do love and independent identity coexist?"),
}


class StoryPlanningError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(" ".join(item["message"] for item in issues if item["level"] == "error"))


def new_chapter(name="New chapter"):
    return {"id": str(uuid.uuid4()), "name": name, "purpose": "", "before": "", "after": "",
            "player_role": "", "phase": "any", "hearts": 0, "arc_ids": [], "event_ids": [], "pattern": "blank"}


def normalize_storyline(value=None):
    """Add missing authoring defaults while retaining all extension metadata.

    Invalid shapes are left intact so the structural validator can report them;
    normalization must never quietly discard malformed imported work.
    """
    result = deepcopy({} if value is None else value)
    if not isinstance(result, dict):
        return result
    brief = result.get("brief", {})
    if isinstance(brief, dict):
        result["brief"] = {"desire": "", "question": "", "boundaries": "", "motif": "", **brief}
    result.setdefault("chapters", [])
    if isinstance(result["chapters"], list):
        result["chapters"] = [{**new_chapter(), **chapter} if isinstance(chapter, dict) else chapter
                              for chapter in result["chapters"]]
    return result


def _valid_text(value, maximum):
    return (isinstance(value, str) and len(value) <= maximum and "\x00" not in value
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def storyline_structure_issues(value):
    """Typed draft checks; completeness and existing references are not required."""
    issues = []

    def add(path, message):
        issues.append({"level": "error", "field": "storyline" + ("." + path if path else ""), "message": message})

    if not isinstance(value, dict):
        add("", "Storyline must be an object.")
        return issues
    brief = value.get("brief", {})
    if not isinstance(brief, dict):
        add("brief", "The character brief must be an object.")
    else:
        for field in ("desire", "question", "boundaries", "motif"):
            if field in brief and not _valid_text(brief[field], 8000):
                add("brief." + field, "Enter valid text of at most 8000 characters.")
    chapters = value.get("chapters", [])
    if not isinstance(chapters, list) or len(chapters) > MAX_CHAPTERS:
        add("chapters", f"Use a list with up to {MAX_CHAPTERS} chapters.")
        return issues
    seen = set()
    for index, chapter in enumerate(chapters):
        path = f"chapters.{index}"
        if not isinstance(chapter, dict):
            add(path, "Each chapter must be an object.")
            continue
        for field, maximum in (("id", 100), ("name", 100), ("purpose", 8000), ("before", 8000),
                               ("after", 8000), ("player_role", 8000), ("starter_key", 100)):
            if field in chapter and not _valid_text(chapter[field], maximum):
                add(path + "." + field, f"Enter valid text of at most {maximum} characters.")
        if "id" in chapter:
            identity = chapter["id"]
            if not isinstance(identity, str) or not identity or identity in seen:
                add(path + ".id", "Each chapter needs a unique nonempty text ID.")
            else:
                seen.add(identity)
        for field, choices in (("phase", CHAPTER_PHASES), ("pattern", CHAPTER_PATTERNS)):
            if field in chapter and (not isinstance(chapter[field], str) or chapter[field] not in choices):
                add(path + "." + field, "Choose one of: " + ", ".join(choices) + ".")
        if "hearts" in chapter and (type(chapter["hearts"]) is not int or not 0 <= chapter["hearts"] <= 14):
            add(path + ".hearts", "Choose a whole number of hearts from 0 to 14.")
        for field in ("arc_ids", "event_ids"):
            references = chapter.get(field, [])
            if not isinstance(references, list) or len(references) > 100:
                add(path + "." + field, "Use a list with up to 100 references.")
                continue
            local_seen = set()
            for ref_index, reference in enumerate(references):
                ref_path = f"{path}.{field}.{ref_index}"
                if not _valid_text(reference, 100) or not reference:
                    add(ref_path, "Choose a nonempty text ID of at most 100 characters.")
                elif reference in local_seen:
                    add(ref_path, "Include each reference only once in a chapter.")
                else:
                    local_seen.add(reference)
    return issues


def _rows(character, field):
    value = character.get(field, []) if isinstance(character, dict) else []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _chapters(character):
    value = character.get("storyline", {}) if isinstance(character, dict) else {}
    return _rows(value, "chapters")


def _story(record):
    value = record.get("story", {})
    return value if isinstance(value, dict) else {}


def _contains(record, field, identity):
    value = record.get(field, [])
    return isinstance(value, list) and identity in value


def _ids(record, field):
    value = record.get(field, [])
    return [identity for identity in value if isinstance(identity, str)] if isinstance(value, list) else []


def storyline_issues(character):
    """Report structural errors and harmless broken planning associations."""
    if not isinstance(character, dict):
        return [{"level": "error", "field": "storyline", "message": "Character data must be an object."}]
    issues = storyline_structure_issues(character.get("storyline", {}))
    if issues:
        return issues
    ids = {field: {row["id"] for row in _rows(character, collection) if isinstance(row.get("id"), str)}
           for field, collection in (("arc_ids", "relationships"), ("event_ids", "events"))}
    for index, chapter in enumerate(_chapters(character)):
        for field, known in ids.items():
            for ref_index, reference in enumerate(chapter.get(field, [])):
                if reference not in known:
                    issues.append({"level": "warning", "field": f"storyline.chapters.{index}.{field}.{ref_index}",
                                   "message": "This planning reference was removed or cannot be found. Relink it or remove the reference."})
        if character.get("romanceable") is False and (chapter.get("phase") in ("dating", "married") or chapter.get("hearts", 0) > 10):
            issues.append({"level": "warning", "field": f"storyline.chapters.{index}.phase",
                           "message": "This chapter suggests romance for a non-romanceable character. Planning notes do not change game access."})
    return issues


def event_references(character, event_id):
    """Human-readable incoming uses, including disabled daily-life drafts."""
    if not isinstance(event_id, str) or not event_id:
        return []
    references = []
    for event in _rows(character, "events"):
        if _story(event).get("previous_event_id") == event_id:
            references.append(f"Event ‘{event.get('name') or 'Untitled event'}’ requires this event.")
    for chapter in _chapters(character):
        if _contains(chapter, "event_ids", event_id):
            references.append(f"Chapter ‘{chapter.get('name') or 'Untitled chapter'}’ includes this event.")
    life = character.get("life", {}) if isinstance(character, dict) else {}
    for kind, label in (("dialogues", "Dialogue"), ("routines", "Routine"), ("spouse_dialogue", "Married dialogue")):
        for rule in _rows(life, kind):
            conditions = rule.get("conditions", {})
            if isinstance(conditions, dict) and conditions.get("after_event_id") == event_id:
                references.append(f"{label} ‘{rule.get('name') or 'Untitled rule'}’ follows this event.")
    return references


def event_removal_issues(character, event_id):
    """Gameplay dependencies that must be resolved before deleting an event.

    Chapter membership is organizational and is removed with the event. These
    issues retain editor field paths so callers can open the actual dependency.
    """
    if not isinstance(character, dict) or not isinstance(event_id, str) or not event_id:
        return []
    issues = []
    events = character.get("events", [])
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict) or event.get("id") == event_id:
            continue
        if _story(event).get("previous_event_id") == event_id:
            issues.append({"level": "error", "field": f"events.{index}.story.previous_event_id",
                           "message": f"Event ‘{event.get('name') or 'Untitled event'}’ requires this event. Choose another prerequisite or ‘No earlier event required’."})
    life = character.get("life", {})
    for kind, label in (("dialogues", "Dialogue"), ("routines", "Routine"), ("spouse_dialogue", "Marriage dialogue")):
        rules = life.get(kind, []) if isinstance(life, dict) else []
        for index, rule in enumerate(rules if isinstance(rules, list) else []):
            if not isinstance(rule, dict):
                continue
            conditions = rule.get("conditions", {})
            if isinstance(conditions, dict) and conditions.get("after_event_id") == event_id:
                issues.append({"level": "error", "field": f"life.{kind}.{index}.conditions.after_event_id",
                               "message": f"{label} ‘{rule.get('name') or 'Untitled rule'}’ follows this event. Choose another event or clear its after-event condition."})
    return issues


def remove_event(character, event_id):
    """Copy a character without this event and its chapter memberships.

    Authored chapters, other events, arc links and extension data are retained.
    Incoming gameplay conditions are never silently cleared or retargeted.
    """
    if not isinstance(character, dict) or not isinstance(character.get("events"), list):
        _fail("events", "Character events must be a list.")
    matches = [index for index, event in enumerate(character["events"])
               if isinstance(event, dict) and event.get("id") == event_id]
    if not isinstance(event_id, str) or not event_id or len(matches) != 1:
        _fail("events", "Choose one existing event to remove.")
    issues = event_removal_issues(character, event_id)
    if issues:
        raise StoryPlanningError(issues)
    result = deepcopy(character)
    del result["events"][matches[0]]
    for chapter in _chapters(result):
        if isinstance(chapter.get("event_ids"), list):
            chapter["event_ids"] = [identity for identity in chapter["event_ids"] if identity != event_id]
    return result


def relationship_references(character, relationship_id):
    if not isinstance(relationship_id, str) or not relationship_id:
        return []
    references = []
    for event in _rows(character, "events"):
        story = _story(event)
        if story.get("relationship_id") == relationship_id or _contains(story, "arc_ids", relationship_id):
            references.append(f"Event ‘{event.get('name') or 'Untitled event'}’ links to this arc.")
    for chapter in _chapters(character):
        if _contains(chapter, "arc_ids", relationship_id):
            references.append(f"Chapter ‘{chapter.get('name') or 'Untitled chapter'}’ links to this arc.")
    return references


def relationship_event_ids(character, relationship_id):
    """Union of legacy links, direct planning links, and chapter associations."""
    if not isinstance(relationship_id, str) or not relationship_id:
        return []
    chapter_events = {identity for chapter in _chapters(character) if _contains(chapter, "arc_ids", relationship_id)
                      for identity in _ids(chapter, "event_ids")}
    return list(dict.fromkeys(event["id"] for event in _rows(character, "events")
                             if isinstance(event.get("id"), str) and
                             (_story(event).get("relationship_id") == relationship_id
                              or _contains(_story(event), "arc_ids", relationship_id) or event["id"] in chapter_events)))


def related_events(character, relationship_id):
    identities = set(relationship_event_ids(character, relationship_id))
    return [deepcopy(event) for event in _rows(character, "events")
            if isinstance(event.get("id"), str) and event["id"] in identities]


def _fail(field, message):
    raise StoryPlanningError([{"level": "error", "field": field, "message": message}])


def _check_character(character):
    if not isinstance(character, dict):
        _fail("storyline", "Character data must be an object.")
    for axis in ("home_x", "home_y"):
        if axis in character and (type(character[axis]) is not int or not 0 <= character[axis] <= 1000):
            _fail(axis, "Choose a whole-number starting tile from 0 to 1000.")
    if "home_map" in character and not _valid_text(character["home_map"], 80):
        _fail("home_map", "Choose a map ID of at most 80 characters.")
    issues = storyline_structure_issues(character.get("storyline", {}))
    if issues:
        raise StoryPlanningError(issues)
    from .story import structure_issues
    for key in ("events", "relationships"):
        rows = character.get(key, [])
        if not isinstance(rows, list) or len(rows) > 100:
            _fail(key, "Use a list with up to 100 story records.")
        seen = set()
        for index, row in enumerate(rows):
            found = structure_issues(row, key)
            if found:
                raise StoryPlanningError([{**issue, "field": f"{key}.{index}." + issue["field"]} for issue in found])
            identity = row.get("id")
            if identity is not None:
                if identity in seen:
                    _fail(f"{key}.{index}.id", "Each story record needs a unique ID.")
                seen.add(identity)


def preview_story_starter(character, starter="romance", relationship_id=""):
    """Return only missing chapters and scene drafts, without changing the project."""
    _check_character(character)
    if not isinstance(starter, str) or starter not in STORY_STARTERS:
        _fail("storyline", "Choose a supported story starter.")
    if not _valid_text(relationship_id, 100):
        _fail("relationships", "Choose a valid relationship ID.")
    relations = {row.get("id"): row for row in _rows(character, "relationships") if isinstance(row.get("id"), str)}
    if relationship_id and relationship_id not in relations:
        _fail("relationships", "Choose an existing relationship or personal arc.")
    from .story import new_event
    identity = character.get("id") or character.get("internal_name") or "character"
    if not _valid_text(identity, 192):
        _fail("id", "Choose a stable character identity before applying a starter.")
    existing_chapters = {row.get("id") for row in _chapters(character) if isinstance(row.get("id"), str)}
    existing_events = {row.get("id") for row in _rows(character, "events") if isinstance(row.get("id"), str)}
    result = {"starter": starter, "relationship_id": relationship_id, "chapters": [], "events": [], "existing_count": 0}
    for key in STORY_STARTERS[starter]["milestones"]:
        def stable_id(kind):
            return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["pixelheart:storyline:1", identity, relationship_id, key, kind])))
        chapter_id, event_id = stable_id("chapter"), stable_id("event")
        if chapter_id in existing_chapters:
            result["existing_count"] += 1
            continue
        name, hearts, phase, pattern, purpose = _MILESTONES[key]
        chapter = new_chapter(name)
        chapter.update(id=chapter_id, hearts=hearts, phase=phase, pattern=pattern, purpose=purpose,
                       event_ids=[event_id], arc_ids=[relationship_id] if relationship_id else [], starter_key=key)
        result["chapters"].append(chapter)
        if event_id not in existing_events:
            event = new_event(character, pattern)
            event.update(id=event_id, name=name, hearts=hearts)
            event["story"].update(arc_ids=list(chapter["arc_ids"]),
                                   relationship=phase if phase in ("dating", "married") else "any")
            for collection in ("actors", "beats", "planned_effects"):
                for index, entry in enumerate(event["story"][collection]):
                    entry["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_id}:{collection}:{index}"))
            result["events"].append(event)
    return result


def apply_story_starter(character, preview, chapter_ids=None):
    """Append selected missing drafts; edited chapter access affects new events only."""
    _check_character(character)
    if not isinstance(preview, dict):
        _fail("storyline", "A starter preview must be an object.")
    chapters, events = preview.get("chapters"), preview.get("events")
    issues = storyline_structure_issues({"chapters": chapters})
    if issues:
        raise StoryPlanningError(issues)
    if not isinstance(events, list) or len(events) > 100:
        _fail("events", "A preview must contain up to 100 event drafts.")
    from .story import normalize_event, structure_issues
    by_event = {}
    for index, event in enumerate(events):
        issues = structure_issues(event)
        if issues:
            raise StoryPlanningError([{**issue, "field": f"events.{index}." + issue["field"]} for issue in issues])
        identity = event.get("id")
        if not identity or identity in by_event:
            _fail(f"events.{index}.id", "Every preview event needs a unique stable ID.")
        by_event[identity] = event
    chapter_by_id = {}
    for index, chapter in enumerate(chapters):
        identity = chapter.get("id")
        if not identity:
            _fail(f"storyline.chapters.{index}.id", "Every preview chapter needs a stable ID.")
        chapter_by_id[identity] = chapter
    if chapter_ids is None:
        selected = set(chapter_by_id)
    else:
        if (not isinstance(chapter_ids, (list, tuple)) or len(chapter_ids) > MAX_CHAPTERS
                or any(not _valid_text(identity, 100) for identity in chapter_ids)):
            _fail("storyline.chapters", "Choose chapter IDs from this preview.")
        selected = set(chapter_ids)
        if selected - chapter_by_id.keys():
            _fail("storyline.chapters", "A selected chapter is not in this preview.")
    result = deepcopy(character)
    result["storyline"] = normalize_storyline(result.get("storyline"))
    result.setdefault("events", [])
    existing_chapters = {row.get("id") for row in _chapters(result) if isinstance(row.get("id"), str)}
    existing_events = {row.get("id") for row in _rows(result, "events") if isinstance(row.get("id"), str)}
    for raw in chapters:
        if raw["id"] not in selected or raw["id"] in existing_chapters:
            continue
        chapter = normalize_storyline({"chapters": [raw]})["chapters"][0]
        for event_id in chapter["event_ids"]:
            if event_id in existing_events:
                continue
            if event_id not in by_event:
                _fail("storyline.chapters", "A selected chapter refers to an event absent from the preview and project.")
            event = normalize_event(by_event[event_id])
            event["hearts"] = chapter["hearts"]
            if len(chapter["event_ids"]) == 1:
                event["name"] = chapter["name"]
            # All starter output is a draft, even if a caller changed its stage.
            event["story"].update(stage="outline", arc_ids=list(chapter["arc_ids"]),
                                   relationship=chapter["phase"] if chapter["phase"] in ("dating", "married") else "any")
            result["events"].append(event)
            existing_events.add(event_id)
        result["storyline"]["chapters"].append(chapter)
        existing_chapters.add(chapter["id"])
    if len(result["storyline"]["chapters"]) > MAX_CHAPTERS or len(result["events"]) > 100:
        _fail("storyline.chapters", "This selection would exceed the limit of 100 chapters or 100 events. Select fewer chapters.")
    return result
