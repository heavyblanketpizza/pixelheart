"""Revision-specific export, installation, and in-game playtest evidence.

The legacy ``creator`` document key remains the storage location for release
records so existing projects retain their export history and observations.
All public operations return copies and preserve other project metadata.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json

from .story import event_game_id, exported_npc_id


PLAYTEST_STATUSES = ("untested", "passed", "failed")
_TRACKING = {"tests", "exports", "installation", "installations", "last_export", "last_install", "updated_at"}


class PlaytestError(ValueError):
    """The requested release operation would record invalid evidence."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def content_fingerprint(document):
    """Hash authoring content, excluding evidence records and volatile timestamps."""
    def clean(value, path=()):
        if isinstance(value, dict):
            result = {key: clean(item, path + (key,)) for key, item in value.items()
                      if key not in {"created_at", "updated_at"}
                      and not (path == ("creator",) and key in _TRACKING)}
            if not path and result.get("creator") == {}:
                result.pop("creator")
            return result
        if isinstance(value, list):
            return [clean(item, path) for item in value]
        return value
    try:
        payload = json.dumps(clean(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlaytestError("Project content must be valid JSON.") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _current_record(document, key):
    record = document.get("creator", {}).get(key) or {}
    return record if record.get("fingerprint") == content_fingerprint(document) else {}


def playtest_cases(document):
    from .world import exported_location_id, exported_mod_id
    character = document.get("character", {})
    world = document.get("world", {})
    npc = exported_npc_id(character)
    mod_id = exported_mod_id(character)
    location = character.get("home_map", "Town")

    def map_name(reference):
        for place in world.get("locations", []):
            if place.get("spouse_room"):
                continue
            game_id = exported_location_id(place, character)
            if reference in (place.get("internal_name"), game_id):
                return f"{place.get('name') or place['internal_name']} (game map: {game_id})"
        return reference

    x, y = character.get("home_x", 32), character.get("home_y", 62)
    cases = [
        {"id": "load", "title": "The game loads the pack", "instructions": "Launch the installed revision through SMAPI with Content Patcher. Load a test save, check the SMAPI log for this pack, and confirm there are no red errors."},
        {"id": "meet", "title": "Meet your character", "instructions": f"On a fresh test save, find {character.get('name', 'your character')} in {map_name(location)}, starting near tile {x}, {y}. Talk once and confirm the introduction, portrait and display name. Internal NPC ID: {npc}."},
        {"id": "routine", "title": "Follow a whole day", "instructions": "Sleep once, then follow the character through every authored stop from 06:00 until the final stop. Check walkable tiles, routes between maps, facing and sprite frames. Repeat in each authored season and weather variant."},
        {"id": "dialogue", "title": "Check everyday conversation", "instructions": "Talk on each weekday. Confirm distinct authored lines and expressions. Check any season, heart-level and post-event conversation rules at their intended conditions."},
        {"id": "gifts", "title": "Give the chosen gifts", "instructions": "Give one item from each authored taste category on appropriate days. Confirm that the game reports the intended reactions and doesn't substitute an unknown item."},
    ]
    for event in character.get("events", []):
        if event.get("story", {}).get("stage") != "ready":
            continue
        story = event["story"]
        previous = story.get("previous_event_id")
        prior = next((item.get("name", "the previous chapter") for item in character.get("events", []) if item.get("id") == previous), "the prerequisite chapter")
        start, end = story.get("time_start", 600), story.get("time_end", 2400)
        instructions = f"Reach at least {event.get('hearts', 0)} hearts and enter {map_name(event.get('location', location))} between {start // 100:02d}:{start % 100:02d} and {end // 100:02d}:{end % 100:02d}."
        if previous:
            instructions += f" Watch {prior} first."
        if story.get("season", "any") != "any":
            instructions += f" Season: {story['season']}."
        if story.get("weather", "any") != "any":
            instructions += f" Weather: {story['weather']}."
        relationship = story.get("relationship", "any")
        if relationship == "unmarried":
            instructions += " The player must not be married to this character; marriage to someone else does not exclude this scene."
        elif relationship == "dating":
            instructions += " The player must be dating this character."
        elif relationship == "married":
            instructions += " The player must be married to this character."
        house_upgrade = story.get("min_house_upgrade", 0)
        if house_upgrade:
            instructions += f" The player's farmhouse must be at upgrade level {house_upgrade} or higher."
        instructions += " Watch each answer and skip once using separate test saves. Check every line, portrait, position and friendship effect."
        if story.get("repeat", "once") == "daily":
            instructions += " Install Event Repeater 6.5.8 or later. After completion, leave and re-enter on the same day without reloading: the scene should stay completed. Sleep to the next day and re-enter with its conditions satisfied: it should play again. Reload a saved game and check again, since reloading also resets this repeatable scene. Friendship effects can be earned on every replay."
        else:
            instructions += " This scene is one-time. After completion, leave and re-enter, then sleep and re-enter: it should not play again. After the game has saved that completion, reload that save and confirm it stays completed."
        instructions += f" Event ID: {event_game_id(event, character).replace('{{ModId}}', mod_id)}."
        cases.append({"id": "event:" + event["id"], "event_id": event["id"], "section": "story", "title": "Play chapter: " + event.get("name", "Untitled"), "instructions": instructions})
    if character.get("romanceable"):
        cases.append({"id": "marriage", "title": "Test the relationship after marriage", "instructions": "On a separate test save, test bouquet dating, proposal, wedding, spouse conversations, kiss and wedding sprite frames, spouse room, and a full married day. Check each ready married chapter after its prerequisites."})
    if world.get("characters") or world.get("companions"):
        cases.append({"id": "companions", "title": "Meet the supporting cast", "instructions": "Find each supporting character, confirm their own portrait, sprite, dialogue and route, and watch each scene that includes them. Check that their internal IDs resolve correctly."})
    for place in world.get("locations", []):
        if place.get("spouse_room"):
            instructions = "On a married test save, enter the upgraded farmhouse and inspect the supplied spouse room. Check its walls, floors, furniture, entry path and the spouse's movement at different times."
        else:
            entrance = place.get("entrance", {})
            instructions = f"Visit {map_name(place.get('internal_name', ''))}. Enter from {map_name(entrance.get('map', 'the entrance map'))} at tile {entrance.get('x', 0)}, {entrance.get('y', 0)}. Check the entry tile, walkable space, tilesheet artwork and the return exit. Leave and re-enter, then follow any NPC routes that use this map."
        cases.append({"id": "location:" + str(place.get("id", "")), "section": "home",
                      "title": "Visit " + place.get("name", "the custom location"), "instructions": instructions})
    fingerprint = content_fingerprint(document)
    records = document.get("creator", {}).get("tests", {})
    for case in cases:
        case.setdefault("section", {"load": "export", "meet": "identity", "routine": "schedule", "dialogue": "dialogue", "gifts": "gifts", "marriage": "life", "companions": "home"}.get(case["id"], "export"))
        record = records.get(case["id"], {})
        stale = bool(record and record.get("fingerprint") != fingerprint)
        case.update(status="untested" if stale else record.get("status", "untested"), stale=stale,
                    notes=record.get("notes", ""), previous_status=record.get("status", "untested"))
    return cases


def export_is_current(document, archive_bytes=None):
    """Whether recorded export evidence matches current content and optional bytes."""
    record = _current_record(document, "last_export")
    return bool(record) and (archive_bytes is None or isinstance(archive_bytes, bytes)
        and hashlib.sha256(archive_bytes).hexdigest() == record.get("sha256"))


def record_playtest(document, test_id, status, notes=""):
    if status not in PLAYTEST_STATUSES:
        raise PlaytestError("Choose untested, passed or failed.")
    if not isinstance(notes, str) or len(notes) > 8000 or "\x00" in notes:
        raise PlaytestError("Test notes must be text of at most 8000 characters.")
    if test_id not in {case["id"] for case in playtest_cases(document)}:
        raise PlaytestError("This playtest no longer belongs to the current project.")
    if status != "untested" and not _current_record(document, "last_install"):
        raise PlaytestError("Install the current exported revision before recording its in-game test results.")
    result = copy.deepcopy(document)
    result.setdefault("creator", {}).setdefault("tests", {})[test_id] = {
        "status": status, "notes": notes, "fingerprint": content_fingerprint(document), "recorded_at": _now()}
    return result


def record_export(document, path, archive_bytes):
    if not isinstance(archive_bytes, bytes) or not archive_bytes:
        raise PlaytestError("Record an export only after a nonempty archive was successfully written.")
    if not str(path).strip():
        raise PlaytestError("The exported archive needs a path.")
    result = copy.deepcopy(document)
    record = {"path": str(path), "fingerprint": content_fingerprint(document),
              "sha256": hashlib.sha256(archive_bytes).hexdigest(), "bytes": len(archive_bytes), "recorded_at": _now()}
    creator = result.setdefault("creator", {})
    creator["last_export"] = record
    creator.setdefault("exports", []).append(copy.deepcopy(record))
    return result


def record_install(document, path, archive_bytes=None):
    exported = _current_record(document, "last_export")
    if not exported:
        raise PlaytestError("Export the current project revision before recording its installation.")
    if not str(path).strip():
        raise PlaytestError("The installed content pack needs a destination path.")
    if archive_bytes is not None and (not isinstance(archive_bytes, bytes) or hashlib.sha256(archive_bytes).hexdigest() != exported["sha256"]):
        raise PlaytestError("The installed archive does not match the recorded export.")
    result = copy.deepcopy(document)
    record = {"path": str(path), "fingerprint": content_fingerprint(document),
              "sha256": exported["sha256"], "recorded_at": _now()}
    creator = result.setdefault("creator", {})
    creator["last_install"] = record
    creator.setdefault("installations", []).append(copy.deepcopy(record))
    return result
