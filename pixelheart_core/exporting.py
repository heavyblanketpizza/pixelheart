"""Standalone validation and Content Patcher export for authored NPCs.

The archive is a starter content pack. Structural checks cannot verify map
pathfinding, visual frame content, or behavior inside Stardew Valley.
"""

from __future__ import annotations

import hashlib
import copy
import io
import json
import re
import warnings
import zipfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .birthdays import birthdays_on
from .story import compile_story, event_game_id, exported_npc_id, normalize_event, story_issues, story_repeat_events
from .life import compile_life, life_issues
from .world import WorldError, world_character, world_issues, compile_world, exported_location_id
from .validation import GENDERS
from .dialogue_templates import MAX_DIALOGUES
from .provenance import source_metadata


CONTENT_PATCHER_FORMAT = "2.9.0"
MAX_ARTWORK_BYTES = 5 * 1024 * 1024
SEASONS = {"spring", "summer", "fall", "winter"}
APPEARANCE_VARIANTS = ("spring", "summer", "fall", "winter", "beach")
ENUMS = {
    "age": {"adult"},
    "manners": {"polite", "neutral", "rude"},
    "social_anxiety": {"shy", "neutral", "outgoing"},
    "optimism": {"positive", "neutral", "negative"},
}
GIFT_IDS = {
    "sunflower": "421", "poppy": "376", "daffodil": "18", "leek": "20",
    "clay": "330", "trash": "168", "goat cheese": "426", "peach": "636",
    "wood": "388", "coffee": "395", "joja cola": "167", "amethyst": "66",
    "starfruit": "268", "quartz": "80",
}
TASTES = ("love", "like", "dislike", "hate")
FACING = {"up": 0, "right": 1, "down": 2, "left": 3}
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
MAP_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
DIALOGUE_KEY = re.compile(r"[A-Za-z0-9_.*()+:-]{1,160}\Z")


class ExportValidationError(ValueError):
    """Raised when known export blockers are present."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__(" ".join(issue["message"] for issue in issues if issue["level"] == "error"))


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _integer(value):
    if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
        raise ValueError("Expected an integer")
    return int(value)


def _time(value):
    text = str(value)
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hours, minutes = text.split(":")
        result = int(hours) * 100 + int(minutes)
    else:
        result = _integer(value)
    if not 600 <= result <= 2600 or result % 100 >= 60 or result % 10:
        raise ValueError("Use ten-minute times from 06:00 through 26:00")
    return result


def _facing(value):
    if isinstance(value, str) and value.lower() in FACING:
        return FACING[value.lower()]
    result = _integer(value)
    if result not in range(4):
        raise ValueError("Invalid direction")
    return result


def _gift_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Enter an item name or ID")
    value = str(value).strip()
    if value.casefold() in GIFT_IDS:
        return GIFT_IDS[value.casefold()]
    if re.fullmatch(r"-?\d+", value):
        return str(int(value))
    if re.fullmatch(r"\(O\)[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", value):
        return value[3:]
    # Prefixes distinguish intentional advanced IDs from misspelled item names.
    if re.fullmatch(r"(?:id|tag):[A-Za-z_][A-Za-z0-9_.-]{0,99}", value):
        return value.split(":", 1)[1]
    raise ValueError(f'Unknown gift "{value}". Use an object ID, id:CustomItem, or tag:context_tag.')


def _artwork_size(path, kind, romanceable, add):
    if not path:
        add("error", kind, f"Provide a complete local {kind} PNG sheet. Preview artwork is not a game sheet.")
        return None
    try:
        artwork = Path(path)
        if artwork.stat().st_size > MAX_ARTWORK_BYTES:
            raise ValueError("File must be 5 MB or smaller.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(artwork) as image:
                if image.format != "PNG":
                    raise ValueError("Only PNG artwork is supported.")
                size = image.size
                if size[0] * size[1] > 4_194_304:
                    raise ValueError("Image exceeds the supported pixel limit.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Use a static PNG sheet, not an animated PNG.")
                image.verify()
            # verify checks the container; load also checks the pixel data.
            with Image.open(artwork) as image:
                image.load()
    except (OSError, ValueError, TypeError, UnidentifiedImageError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        add("error", kind, f"The {kind} sheet cannot be read: {exc}")
        return None
    width, height = size
    if kind == "portrait":
        valid = width == 128 and height >= 192 and height % 64 == 0
        expected = "128 px wide and at least 192 px high, in rows of 64 px (six 64×64 expressions)"
    else:
        minimum = 416 if romanceable else 128
        valid = width == 64 and height >= minimum and height % 32 == 0
        expected = f"64 px wide and at least {minimum} px high, in rows of 32 px"
        if romanceable:
            expected += "; romance needs the kiss and wedding frames through frame 50"
    if not valid:
        add("error", kind, f"The {kind} sheet is {width}×{height}. It must be {expected}.")
        return None
    add("success", kind, f"{kind.title()} sheet dimensions are valid ({width}×{height}). Check the frame artwork in-game.")
    return size


def _appearance_sheets(appearances, add):
    """Normalize optional sheet sets without accepting arbitrary asset paths."""
    if appearances is None:
        return {}
    if not isinstance(appearances, Mapping):
        add("error", "appearances", "Appearances must map season or beach names to portrait and sprite sheets.")
        return {}
    result = {}
    for variant, sheets in appearances.items():
        field = f"appearances.{variant}"
        if sheets is None:
            continue
        if not isinstance(sheets, Mapping):
            add("error", field, "Each appearance must contain portrait and/or sprite sheet paths.")
            continue
        if not sheets or all(path is None for path in sheets.values()):
            continue
        if variant not in APPEARANCE_VARIANTS:
            add("error", field, "Choose spring, summer, fall, winter, or beach for an appearance.")
            continue
        if any(kind not in ("portrait", "sprite") for kind in sheets):
            add("error", field, "Appearance sheets must use portrait and sprite keys.")
            continue
        result[variant] = {kind: path for kind, path in sheets.items() if path is not None}
    return {variant: result[variant] for variant in APPEARANCE_VARIANTS if variant in result}


def _validate_portrait_indices(dialogues, size, add, appearance=None):
    if not size:
        return
    frame_count = size[0] // 64 * (size[1] // 64)
    for index, entry in enumerate(dialogues):
        if isinstance(entry, dict):
            # Numeric commands at the end of a line are portrait selections.
            for number in re.findall(r"\$(\d+)(?=#|$)", _text(entry.get("text"))):
                significant = number.lstrip("0") or "0"
                # Bound the conversion first: input can exceed Python's
                # integer-string limit even within a valid dialogue length.
                if len(significant) > len(str(frame_count - 1)) or int(significant) >= frame_count:
                    label = number if len(number) <= 16 else number[:12] + "…"
                    field = f"appearances.{appearance}.portrait" if appearance else f"dialogues.{index}"
                    detail = f"{appearance.title()} appearance, dialogue {index + 1}: " if appearance else ""
                    add("error", field, f"{detail}Portrait ${label} is outside your {frame_count}-frame portrait sheet.")


def _validate_story_portrait_indices(data, size, add, appearance=None):
    """Check only speech that will use the authored NPC's portrait in-game."""
    if not size or not isinstance(data.get("events"), list):
        return
    frame_count = size[0] // 64 * (size[1] // 64)
    own_actors = {"$npc", _text(data.get("internal_name")), exported_npc_id(data)}
    for index, event in enumerate(data["events"]):
        if not isinstance(event, dict) or not isinstance(event.get("story"), dict):
            continue
        story = normalize_event(event)["story"]
        if story.get("stage") != "ready" or not isinstance(story.get("beats"), list):
            continue
        for beat_index, beat in enumerate(story["beats"]):
            if not isinstance(beat, dict) or beat.get("kind") not in ("dialogue", "choice") or _text(beat.get("actor")) not in own_actors:
                continue
            # The compiler converts authored newlines to game dialogue breaks.
            choices = beat.get("choices", []) if isinstance(beat.get("choices", []), list) else []
            texts = [_text(beat.get("text"))] + [_text(option.get("text")) for option in choices if isinstance(option, dict)]
            text = "#".join(texts).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "#$b#")
            for number in re.findall(r"\$(\d+)(?=#|$)", text):
                significant = number.lstrip("0") or "0"
                if len(significant) > len(str(frame_count - 1)) or int(significant) >= frame_count:
                    label = number if len(number) <= 16 else number[:12] + "…"
                    field = f"events.{index}.story.beats.{beat_index}"
                    detail = f"{appearance.title()} appearance: " if appearance else ""
                    add("error", field, f"{detail}Scene dialogue portrait ${label} is outside your {frame_count}-frame portrait sheet.")


def _validate_life_portrait_indices(data, size, add, appearance=None):
    life = data.get("life", {})
    if not isinstance(life, dict):
        return
    for kind in ("dialogues", "spouse_dialogue"):
        rows = life.get(kind, [])
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("enabled") is not True:
                continue
            def report(level, _field, message):
                add(level, f"life.{kind}.{index}.text", message)
            _validate_portrait_indices([row], size, report, appearance)


def validate_character(data, portrait_path=None, sprite_path=None, *, appearances=None, world=None, project_root=None):
    """Return issues for default and optional appearance sheets without mutation."""
    issues = []

    def add(level, field, message):
        issues.append({"level": level, "field": field, "message": message})

    if not isinstance(data, dict):
        add("error", "project", "Character data must be an object.")
        return issues
    # The complete character, including authoring notes, is preserved in
    # project.json. Check that backup before accepting a project for export.
    try:
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        add("error", "project", "Character data must be serializable as JSON with valid Unicode and finite numbers; remove circular references or excessively nested notes.")
        return issues
    if world is not None:
        issues.extend(world_issues(world, data, project_root))
        try:
            data = world_character(data, world)
        except (WorldError, TypeError, ValueError) as exc:
            add("error", "world", str(exc))
    if not _text(data.get("name")):
        add("error", "name", "Give your character a display name.")
    elif "{{" in data["name"]:
        add("error", "name", "Use a plain display name without Content Patcher {{tokens}}.")
    if not IDENTIFIER.fullmatch(_text(data.get("internal_name"))):
        add("error", "internal_name", "Internal name must start with an ASCII letter and contain up to 64 letters, numbers, or underscores.")
    if _text(data.get("season")).lower() not in SEASONS:
        add("error", "season", "Choose spring, summer, fall, or winter for the birthday.")
    try:
        birthday_day = _integer(data.get("day"))
        if not 1 <= birthday_day <= 28:
            raise ValueError
    except ValueError:
        add("error", "day", "Birthday must be a day from 1 to 28.")
    else:
        birthday_season = _text(data.get("season")).lower()
        if birthday_season in SEASONS:
            shared_with = birthdays_on(birthday_season, birthday_day)
            if shared_with:
                add("error", "day", f"{birthday_season.title()} {birthday_day} is reserved for {', '.join(shared_with)}'s birthday. Shared birthdays are not allowed. Choose an open day in Identity before exporting.")
    for field, choices in ENUMS.items():
        if _text(data.get(field, "adult" if field == "age" else "neutral")).lower() not in choices:
            if field == "age":
                add("error", field, "Choose adult. Pixelheart creates adult love-interest NPCs only.")
            else:
                add("error", field, f"Choose a supported {field.replace('_', ' ')}: {', '.join(sorted(choices))}.")
    if "gender" in data and data["gender"] not in GENDERS:
        add("error", "gender", "Choose Male, Female, or Undefined for the game gender.")
    romanceable = data.get("romanceable", False)
    if not isinstance(romanceable, bool):
        add("error", "romanceable", "Romanceable must be true or false.")
    if not MAP_NAME.fullmatch(_text(data.get("home_map"))):
        add("error", "home_map", "Enter a home map's internal name, such as Town or BusStop.")
    for field in ("home_x", "home_y"):
        try:
            if not 0 <= _integer(data.get(field)) <= 4095:
                raise ValueError
        except ValueError:
            add("error", field, "Home tile coordinates must be whole numbers from 0 to 4095.")

    dialogues = data.get("dialogues", [])
    if not isinstance(dialogues, list) or not dialogues:
        add("error", "dialogues", "Add at least one dialogue line before exporting.")
        dialogues = []
    if len(dialogues) > MAX_DIALOGUES:
        add("error", "dialogues", f"A character can have up to {MAX_DIALOGUES} dialogue entries.")
    seen = set()
    for index, entry in enumerate(dialogues):
        field = f"dialogues.{index}"
        if not isinstance(entry, dict):
            add("error", field, "Each dialogue must have a trigger and text.")
            continue
        try:
            source_metadata(entry)
        except ValueError as exc:
            add("error", field + ".source", str(exc))
        key, line = _text(entry.get("trigger")), _text(entry.get("text"))
        if not DIALOGUE_KEY.fullmatch(key):
            add("error", field, "Use a game dialogue key such as Introduction, Mon, or spring_Mon2.")
        if key in seen:
            add("error", field, f'Dialogue key "{key}" is repeated. Each trigger must be unique.')
        seen.add(key)
        if not line:
            add("error", field, "Dialogue text cannot be empty.")
        if "{{" in line:
            add("error", field, "Content Patcher {{tokens}} are not supported in authored dialogue; use game dialogue commands instead.")
    if dialogues and "Introduction" not in seen:
        add("warning", "dialogues", "Add an Introduction dialogue for the first meeting.")
    if dialogues and any(day not in seen for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        add("warning", "dialogues", "Some weekdays lack fallback dialogue; the game may use a generic greeting.")

    schedule = data.get("schedule", [])
    if not isinstance(schedule, list) or not schedule:
        add("error", "schedule", "Add at least one stop for the default daily schedule.")
        schedule = []
    previous = -1
    for index, stop in enumerate(schedule):
        field = f"schedule.{index}"
        if not isinstance(stop, dict):
            add("error", field, "Each schedule stop must specify a time, map, and tile coordinates.")
            continue
        try:
            time = _time(stop.get("time"))
            if time <= previous:
                add("error", field, "Schedule times must increase, with no duplicate times.")
            previous = time
        except ValueError:
            add("error", field, "Use a valid ten-minute time from 06:00 to 26:00, such as 09:30.")
        if not MAP_NAME.fullmatch(_text(stop.get("location"))):
            add("error", field, "Schedule locations must be internal map names, such as Town.")
        for axis in ("x", "y"):
            try:
                if not 0 <= _integer(stop.get(axis)) <= 4095:
                    raise ValueError
            except ValueError:
                add("error", field, f"Schedule {axis.upper()} must be a whole tile coordinate from 0 to 4095.")
        try:
            _facing(stop.get("facing", "down"))
        except ValueError:
            add("error", field, "Facing must be up, right, down, left, or 0–3.")
    if any(isinstance(stop, dict) and stop.get("activity") for stop in schedule):
        add("warning", "schedule", "Activity descriptions are saved as author notes. Exported stops set position and facing, without custom animations.")
    add("warning", "schedule", "Map names, walkable tiles, and routes require in-game testing. One daily route is used for all seasons and weather.")

    gifts = data.get("gifts", {})
    if not isinstance(gifts, dict):
        add("error", "gifts", "Gift tastes must be grouped into love, like, dislike, and hate.")
        gifts = {}
    gift_tastes = {}
    for taste in TASTES:
        values = gifts.get(taste, [])
        if not isinstance(values, list):
            add("error", f"gifts.{taste}", "Gift entries must be a list of names or IDs.")
            continue
        for value in values:
            try:
                gift_id = _gift_id(value)
            except ValueError as exc:
                add("error", f"gifts.{taste}", str(exc))
                continue
            if gift_id in gift_tastes and gift_tastes[gift_id] != taste:
                add("error", f"gifts.{taste}", f"Gift {value} appears in more than one taste. Choose one preference.")
            gift_tastes[gift_id] = taste
    if not gift_tastes:
        add("warning", "gifts", "No personal gift tastes are set; the game will use universal tastes.")

    portrait_size = _artwork_size(portrait_path, "portrait", romanceable, add)
    _artwork_size(sprite_path, "sprite", romanceable, add)
    _validate_portrait_indices(dialogues, portrait_size, add)
    _validate_story_portrait_indices(data, portrait_size, add)
    _validate_life_portrait_indices(data, portrait_size, add)
    appearance_sheets = _appearance_sheets(appearances, add)
    for variant, sheets in appearance_sheets.items():
        def add_appearance(level, kind, message):
            add(level, f"appearances.{variant}.{kind}", f"{variant.title()} appearance: {message}")

        for kind, path in sheets.items():
            size = _artwork_size(path, kind, romanceable, add_appearance)
            if kind == "portrait":
                _validate_portrait_indices(dialogues, size, add, variant)
                _validate_story_portrait_indices(data, size, add, variant)
                _validate_life_portrait_indices(data, size, add, variant)
    if "beach" in appearance_sheets:
        add("warning", "appearances.beach", "Beach artwork is exported as island attire. Island visits remain disabled; resort participation needs separate setup and in-game testing.")
    if romanceable:
        life = data.get("life", {})
        has_spouse_lines = isinstance(life, dict) and any(
            isinstance(row, dict) and row.get("enabled")
            for kind in ("spouse_dialogue", "dialogues")
            for row in (life.get(kind, []) if isinstance(life.get(kind, []), list) else [])
            if kind == "spouse_dialogue" or isinstance(row, dict) and isinstance(row.get("conditions"), dict)
            and row["conditions"].get("relationship") == "married")
        has_spouse_room = isinstance(world, dict) and isinstance(world.get("locations", []), list) and any(
            isinstance(place, dict) and place.get("spouse_room") for place in world.get("locations", []))
        dialogue_note = "authored spouse dialogue with the game's fallback dialogue" if has_spouse_lines else "the game's generic spouse dialogue"
        room_note = "the supplied spouse-room section" if has_spouse_room else "the game's default spouse room"
        add("warning", "romanceable", f"Romance uses {dialogue_note} and {room_note}. Test courtship, marriage, home routines, and kiss frame 28 in-game. Festival participation remains disabled.")
    issues.extend(story_issues(data))
    issues.extend(life_issues(data))
    if world is None:
        from .homes import home_issues
        existing = {(issue["level"], issue["field"]) for issue in issues}
        issues.extend(issue for issue in home_issues(data)
                      if (issue["level"], issue["field"]) not in existing)
    if not any(issue["level"] == "error" for issue in issues):
        add("success", "export", "Structural checks passed. This starter pack still needs to be tested in Stardew Valley.")
    return issues


def _gender(pronouns):
    value = _text(pronouns).lower().replace(" ", "")
    return {"he/him": "Male", "she/her": "Female"}.get(value, "Undefined")


def _story_test_guide(data, patches, mod_id, npc_id):
    """Describe the exact compiled events, resolving CP tokens for game testing."""
    entries = {
        key.split("/", 1)[0]: (patch["Target"], key, script)
        for patch in patches for key, script in patch["Entries"].items()
    }
    events = [normalize_event(event) for event in data.get("events", [])
              if isinstance(event, dict) and event_game_id(event, data) in entries]
    by_id = {event["id"]: event for event in events}
    relationships = {entry["id"]: entry for entry in data.get("relationships", [])
                     if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    # Present prerequisite scenes before their successors, independent of editor order.
    ordered = []
    pending = list(events)
    while pending:
        completed = {event["id"] for event in ordered}
        available = [event for event in pending
                     if event.get("story", {}).get("previous_event_id") not in by_id
                     or event["story"]["previous_event_id"] in completed]
        if not available:  # Validation prevents cycles; keep the guide total as a safeguard.
            available = pending[:]
        ordered.extend(available)
        pending = [event for event in pending if event not in available]

    def resolve(value):
        return str(value).replace("{{ModId}}", mod_id)

    lines = [
        f"{data['name']} — Story playtest guide", "",
        f"NPC internal name: {npc_id}",
        f"Playable scenes in this pack: {len(ordered)}", "",
        "These are structured scenes with optional two-answer endings, friendship effects, and linked",
        "event prerequisites. Relationship descriptions are not custom NPC family data.",
        "All drafts and outlines remain in project.json. This guide records exported",
        "content, not proof of an in-game test. Pixelheart has not tested this pack in-game.", "",
        "Before testing", "",
        "1. Install SMAPI, Content Patcher, and this pack on a backed-up test save.",
        "2. Install any other mods that supply referenced maps or NPC actors.",
        "3. Check SMAPI's log for missing assets, unknown actors, or invalid patches.",
        "4. Test scenes in the prerequisite order below. Enter their map under every",
        "   listed condition. Friendship preconditions use points (250 per heart).",
        "5. Check starting tiles, camera, movement, portraits, dialogue, and friendship",
        "   effects. Verify the scene ends and returns control to the player.",
        "6. A Once scene should stay complete after reentry and on the next day.",
        "   A Daily scene should repeat after sleeping with Event Repeater installed,",
        "   when its conditions still match. Reloading a save also resets repeatable",
        "   scenes, so test same-day reloads and their repeatable friendship effects.",
        "   Check that linked scenes stay locked before their prerequisite and unlock afterward.",
        "7. On separate test saves, skip before and after friendship beats; confirm",
        "   every intended friendship effect is applied once and the arc can continue.",
        "8. For each choice, test both answers on separate saves and verify the listed",
        "   response and effect. Skipping before choosing should award no answer effect.",
        "   Skip during each answer's closing response and check its effect applies once.",
        "9. Record findings alongside the scene outline, revise, export, and retest.", "",
        "Exported scenes (prerequisites first)", "",
    ]
    for number, event in enumerate(ordered, 1):
        story = event.get("story", {})
        event_id = event_game_id(event, data)
        target, key, _ = entries[event_id]
        previous = by_id.get(story.get("previous_event_id"))
        relation = relationships.get(story.get("relationship_id"))
        lines.extend([
            f"{number}. {_text(event.get('name')) or 'Untitled scene'}",
            f"   Event ID: {resolve(event_id)}",
            f"   Location: {target.removeprefix('Data/Events/')}",
            f"   Repeat: {'Daily (Event Repeater; resets after sleep or save reload)' if story.get('repeat') == 'daily' else 'Once'}",
        ])
        conditions = key.split("/")[1:]
        lines.append("   Conditions: " + ("; ".join(resolve(value) for value in conditions if value) or "No additional conditions"))
        if previous:
            lines.append(f"   Previous scene: {_text(previous.get('name')) or 'Untitled scene'} ({resolve(event_game_id(previous, data))})")
        if relation:
            lines.append(f"   Relationship arc: {_text(relation.get('name')) or 'Unnamed relationship'}")
        effects = [beat for beat in story.get("beats", []) if beat.get("kind") == "friendship"]
        for beat in effects:
            actor = beat.get("actor", "$npc")
            if actor in ("$npc", data.get("internal_name"), exported_npc_id(data)):
                actor = npc_id
            lines.append(f"   Friendship effect: {actor}, {int(beat['amount']):+d} player friendship points")
        for beat in story.get("beats", []):
            if beat.get("kind") != "choice":
                continue
            actor = beat.get("actor", "$npc")
            if actor in ("$npc", data.get("internal_name"), exported_npc_id(data)):
                actor = npc_id
            lines.append(f"   Choice question: {beat['text']}")
            for option in beat["choices"]:
                lines.append(f"   Answer: {option['label']}")
                lines.append(f"     Response from {actor}: {option['text']}")
                lines.append(f"     Effect: {int(option.get('friendship', 0)):+d} player friendship points with {actor}")
        lines.append(f"   Full event key: {resolve(key)}")
        if _text(story.get("test_notes")):
            lines.append(f"   Author's test notes: {story['test_notes'].strip()}")
        lines.append("")
    lines.extend([
        "Keep project, NPC, and event IDs stable after using this pack in a save.",
        "Changing an event's title or order does not create a new in-game event.",
        "Reference: https://stardewvalleywiki.com/Modding:Event_data", "",
    ])
    return "\n".join(lines)


def _portable_artwork(record, filename):
    """The ZIP contains the selected sheet, with its portable source notices."""
    try:
        metadata = source_metadata(record) if isinstance(record, dict) else {}
    except ValueError as exc:
        raise ExportValidationError([{"level": "error", "field": "artwork.source", "message": str(exc)}]) from exc
    return {"original": filename, "selected": "original", **metadata} if metadata else filename


def _asset_credits(document):
    """Describe source records without copying local filenames or claiming rights."""
    notices = []

    def add(label, record):
        if not isinstance(record, dict):
            return
        metadata = source_metadata(record)
        if "source" in metadata:
            notices.append((label, metadata["source"]))
        for source in metadata.get("source_history", []):
            notices.append((label + " — previous-sheet source (history only)", source))

    def dialogue_notices(character, label):
        groups = {}
        for row in character.get("dialogues", []):
            if not isinstance(row, dict):
                continue
            metadata = source_metadata(row)
            if "source" in metadata:
                source = metadata["source"]
                key = json.dumps(source, sort_keys=True)
                groups.setdefault(key, [source, 0])[1] += 1
        for source, count in groups.values():
            notices.append((f"{label} ({count} imported entries)", source))

    dialogue_notices(document.get("character", {}), "Dialogue")
    world = document.get("world") or {}
    for index, companion in enumerate(world.get("characters", []), 1):
        dialogue_notices(companion.get("character", {}), f"Supporting character {index} dialogue")
    artwork = document.get("artwork", {})
    for kind in ("portrait", "sprite"):
        add(f"Default {kind}", artwork.get(kind))
    for variant, appearance in artwork.get("variants", {}).items():
        for kind in ("portrait", "sprite"):
            add(f"{variant.title()} {kind}", appearance.get(kind))
    if not notices:
        return None
    lines = ["Imported reference sources", "",
             "These notices record origins, not permission to redistribute or a claim",
             "that the imported material is unchanged. Game content remains owned by",
             "ConcernedApe; modifications may belong to their respective creators.", ""]
    for label, source in notices:
        lines.append(label)
        for field, title in (("asset", "Game asset"), ("source_name", "Source"),
                             ("attribution", "Attribution"), ("creator", "Creator"),
                             ("sha256", "Imported SHA-256")):
            if source.get(field):
                lines.append(f"  {title}: {source[field]}")
        if source.get("source_url") or source.get("url"):
            lines.append("  Reference: " + (source.get("source_url") or source["url"]))
        if source.get("modified_game_possible"):
            lines.append("  This game export may include changes from installed mods.")
        lines.append("")
    return "\n".join(lines)


def build_mod_archive(
    data,
    portrait_path: str | PathLike[str] | None = None,
    sprite_path: str | PathLike[str] | None = None,
    *,
    appearances=None,
    world=None,
    project_root=None,
    project_document=None,
) -> bytes:
    """Return a complete ZIP or raise ExportValidationError for known blockers."""
    if world is None and isinstance(project_document, dict):
        world = project_document.get("world")
    issues = validate_character(data, portrait_path, sprite_path, appearances=appearances, world=world, project_root=project_root)
    if portrait_path is None or sprite_path is None or any(issue["level"] == "error" for issue in issues):
        raise ExportValidationError(issues)
    original_data = data
    if project_document is not None:
        try:
            if not isinstance(project_document, dict):
                raise ValueError("The project backup must be an object.")
            json.dumps(project_document, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise ExportValidationError([{"level": "error", "field": "project", "message": "The complete project backup must contain valid JSON values."}]) from None
    world_content = compile_world(world, data, project_root) if world is not None else None
    if world is not None:
        data = world_character(data, world)
    internal = data["internal_name"].strip()
    suffix = hashlib.sha256(str(data.get("id") or internal).encode()).hexdigest()[:10]
    mod_id = f"Pixelheart.{internal}_{suffix}"
    npc_id = f"{mod_id}_{internal}"
    manifest = {
        "Name": f"{data['name'].strip()} — a Pixelheart NPC",
        "Author": "Pixelheart creator",
        "Version": "1.0.0",
        "Description": _text(data.get("tagline")) or f"Meet {data['name'].strip()}, a custom villager.",
        "UniqueID": mod_id,
        "MinimumApiVersion": "4.0.0",
        "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher", "MinimumVersion": CONTENT_PATCHER_FORMAT},
        "UpdateKeys": [],
    }
    npc = {
        "DisplayName": data["name"].strip(),
        "BirthSeason": data["season"].strip().lower(), "BirthDay": _integer(data["day"]),
        "Gender": data["gender"] if "gender" in data else _gender(data.get("pronouns")),
        "Age": data.get("age", "adult").strip().title(),
        "Manner": data.get("manners", "neutral").strip().title(),
        "SocialAnxiety": data.get("social_anxiety", "neutral").strip().title(),
        "Optimism": data.get("optimism", "neutral").strip().title(),
        "HomeRegion": "Town", "CanSocialize": "TRUE", "CanReceiveGifts": True,
        "CanBeRomanced": data.get("romanceable", False), "TextureName": npc_id,
        "SpawnIfMissing": True, "CanVisitIsland": "FALSE", "IntroductionsQuest": False,
        "WinterStarParticipant": "FALSE", "FlowerDanceCanDance": False,
        "Home": [{"Id": "Default", "Location": data["home_map"].strip(),
                  "Tile": {"X": _integer(data["home_x"]), "Y": _integer(data["home_y"])},
                  "Direction": data.get("home_facing", "down")}],
    }
    if world_content:
        npc.update(world_content["npc_fields"])
    changes = [
        {"Action": "Load", "Target": f"Portraits/{npc_id}", "FromFile": "assets/portraits.png"},
        {"Action": "Load", "Target": f"Characters/{npc_id}", "FromFile": "assets/sprites.png"},
        {"Action": "Load", "Target": f"Characters/Dialogue/{npc_id}", "FromFile": "assets/dialogue.json"},
        {"Action": "Load", "Target": f"Characters/schedules/{npc_id}", "FromFile": "assets/schedule.json"},
        {"Action": "EditData", "Target": "Data/Characters", "Entries": {npc_id: npc}},
    ]
    appearance_files = {}
    appearance_backup = {}
    for variant, sheets in _appearance_sheets(appearances, lambda *_: None).items():
        appearance = {"Id": variant}
        if variant == "beach":
            appearance["IsIslandAttire"] = True
        else:
            appearance["Season"] = variant
        appearance_backup[variant] = {}
        for kind in ("portrait", "sprite"):
            if kind not in sheets:
                continue
            target = f"{'Portraits' if kind == 'portrait' else 'Characters'}/{npc_id}_{variant}"
            filename = f"assets/appearances/{variant}/{kind}s.png"
            appearance[kind.title()] = target
            appearance_files[filename] = sheets[kind]
            appearance_backup[variant][kind] = filename
            changes.append({"Action": "Load", "Target": target, "FromFile": filename})
        npc.setdefault("Appearance", []).append(appearance)
    reactions = ("This is wonderful. Thank you!$h", "Thank you, I like this.$h",
                 "Thank you, but this isn't really for me.", "Oh... I'd rather not have this.", "Thank you for thinking of me.")
    tastes = []
    for taste, reaction in zip((*TASTES, "neutral"), reactions):
        values = data.get("gifts", {}).get(taste, []) if taste != "neutral" else []
        tastes.extend((reaction, " ".join(dict.fromkeys(_gift_id(value) for value in values))))
    changes.append({"Action": "EditData", "Target": "Data/NPCGiftTastes", "Entries": {npc_id: "/".join(tastes) + "/"}})
    if data.get("romanceable"):
        changes.append({"Action": "EditData", "Target": "Data/EngagementDialogue", "Entries": {
            npc_id + "0": "I'm looking forward to our life together, @.$l",
            npc_id + "1": "A new beginning, together. That means so much to me.$h",
        }})
    story_patches = compile_story(data, npc_id=npc_id)
    changes.extend(story_patches)
    life_content = compile_life(data, npc_id=npc_id)
    changes.extend(life_content["patches"])
    if world_content:
        changes.extend(world_content["patches"])
        if world_content["dependencies"]:
            manifest["Dependencies"] = world_content["dependencies"]
    route = "/".join(
        f"{_time(stop['time'])} {stop['location'].strip()} {_integer(stop['x'])} {_integer(stop['y'])} {_facing(stop.get('facing', 'down'))}"
        for stop in data["schedule"]
    )
    files = {
        "manifest.json": manifest,
        "content.json": {"Format": CONTENT_PATCHER_FORMAT, "Changes": changes},
        "assets/dialogue.json": {entry["trigger"].strip(): entry["text"].strip() for entry in data["dialogues"]},
        "assets/schedule.json": {"spring": route},
        "project.json": {"format": "pixelheart-project", "version": 1, "character": original_data},
        "validation.json": issues,
    }
    files.update(life_content["files"])
    if world_content:
        files.update(world_content["files"])
        files["project.json"]["world"] = world_content["world"]
    repeat_events = story_repeat_events(data, npc_id=npc_id, mod_id=mod_id)
    if world_content:
        repeat_events.extend(world_content["repeat_events"])
    if repeat_events:
        files["content.json"]["RepeatEvents"] = list(dict.fromkeys(repeat_events))
        dependencies = manifest.setdefault("Dependencies", [])
        existing = next((entry for entry in dependencies if entry["UniqueID"].casefold() == "misscoriel.eventrepeater"), None)
        if existing:
            existing["IsRequired"] = True
            digits = tuple(int(part) for part in re.findall(r"\d+", existing.get("MinimumVersion", ""))[:3])
            if digits < (6, 5, 8):
                existing["MinimumVersion"] = "6.5.8"
        else:
            dependencies.append({"UniqueID": "misscoriel.eventrepeater", "MinimumVersion": "6.5.8", "IsRequired": True})
    if appearance_backup:
        files["project.json"]["artwork"] = {
            "portrait": "assets/portraits.png", "sprite": "assets/sprites.png",
            "variants": appearance_backup,
        }
    if project_document is not None:
        # Preserve the creator brief, catalog, playtest records, and extension
        # metadata while making every included asset path portable in the ZIP.
        backup = copy.deepcopy(project_document)
        backup.update(files["project.json"])
        original_artwork = project_document.get("artwork", {})
        original_artwork = original_artwork if isinstance(original_artwork, dict) else {}
        backup["artwork"] = {
            kind: _portable_artwork(original_artwork.get(kind), filename)
            for kind, filename in (("portrait", "assets/portraits.png"), ("sprite", "assets/sprites.png"))
        }
        if appearance_backup:
            source_variants = original_artwork.get("variants", {})
            source_variants = source_variants if isinstance(source_variants, dict) else {}
            backup["artwork"]["variants"] = {}
            for variant, sheets in appearance_backup.items():
                source_set = source_variants.get(variant, {})
                source_set = source_set if isinstance(source_set, dict) else {}
                backup["artwork"]["variants"][variant] = {
                    kind: _portable_artwork(source_set.get(kind), filename) for kind, filename in sheets.items()
                }
        files["project.json"] = backup
    try:
        credits = _asset_credits(files["project.json"])
    except ValueError as exc:
        raise ExportValidationError([{"level": "error", "field": "source", "message": str(exc)}]) from exc
    world_guide = None
    if world_content and any(world_content["world"].get(key) for key in ("characters", "locations", "dependencies")):
        compiled_world = world_content["world"]
        lines = [f"{data['name']} — World playtest guide", "",
                 "This guide describes compiled content; it does not certify an in-game test.",
                 "Install every required dependency from manifest.json before testing.", "",
                 f"Primary character: {data['name']} ({npc_id})",
                 f"  Home: {data['home_map']} at {data['home_x']}, {data['home_y']}; facing {data.get('home_facing', 'down')}.",
                 "  Sleep once, check their morning position, then follow the complete route home at night.", ""]
        for entry in compiled_world["characters"]:
            companion = world_character(entry["character"], compiled_world, original_data)
            lines.extend([f"Supporting character: {companion['name']} ({exported_npc_id(companion)})",
                          f"  Home: {companion['home_map']} at {companion['home_x']}, {companion['home_y']}; facing {companion.get('home_facing', 'down')}.",
                          "  Meet them, test dialogue and gifts, then sleep and follow their daily route.", ""])
        for place in compiled_world["locations"]:
            identity = exported_location_id(place, original_data)
            lines.append(f"Place: {place['name']} ({identity})")
            if place["spouse_room"]:
                lines.extend([f"  Farmhouse spouse-room section: {place['room_x']}, {place['room_y']} (6×9)",
                              "  Marry the primary character on a test save; inspect the room and its boundaries."])
            else:
                entrance = place["entrance"]
                source = next((exported_location_id(item, original_data) for item in compiled_world["locations"]
                               if item["internal_name"] == entrance["map"]), entrance["map"])
                lines.extend([f"  Enter from {source} tile {entrance['x']}, {entrance['y']}.",
                              f"  Arrive inside at {place['entry_x']}, {place['entry_y']}.",
                              f"  Exit from {place['exit_x']}, {place['exit_y']} to {source} tile {entrance['arrival_x']}, {entrance['arrival_y']}.",
                              "  Test both warps, collision, tile actions, event staging, and NPC pathfinding."])
            lines.append("")
        lines.extend(["For enabled daily-life content, test each matching season, weather, weekday,",
                      "relationship, house-upgrade, and completed-event condition. Check routines",
                      "after sleeping and compare overlapping rules in their displayed order.",
                      "A home assignment sets the default spawn. Explicit routes keep their own destinations.",
                      "If you moved old-home stops, verify each changed route and its final stop in-game.",
                      "Married return-to-farmhouse stops remain separate from the unmarried home.",
                      "Inspect SMAPI logs. Keep project, NPC, and map identities stable after release.", ""])
        world_guide = "\n".join(lines)
    readme = f"""{data['name']} — Pixelheart starter NPC

Target: Stardew Valley 1.6, SMAPI 4+, Content Patcher {CONTENT_PATCHER_FORMAT}+.
NPC internal name: {npc_id}
Home: {data['home_map']} at {data['home_x']}, {data['home_y']}; facing {data.get('home_facing', 'down')}.

Install SMAPI, Content Patcher, and the required dependencies in manifest.json,
then extract this folder into your Mods folder.
Run the game through SMAPI. Test on a backed-up save, meet the NPC at the home
map and tile, and sleep once before checking the next day's full schedule.
Review SMAPI's log for warnings and test dialogue, gifts, birthday, pathfinding,
and (if enabled) romance in-game. This pack has not been tested in-game by Pixelheart.

Home assignment sets the default spawn. Explicit schedule destinations stay as
authored. Check their morning position and follow the full route to its final
stop at night, including any old-home stops moved to the new home. Test enabled
conditional routines too. Married return-to-farmhouse stops are separate.

The supplied PNGs are included unchanged. Sheet dimensions do not verify frame
content. Portraits use six standard emotions first; sprites need the standard
walking frames and, for romance, kissing/wedding frames. No game art is bundled.

The default daily schedule covers every season and weather. Enabled daily-life
rules add conditional conversations, routes, and spouse dialogue. Ready story scenes are
exported as playable event scripts; if present, STORY_TESTING.txt lists their
triggers, event IDs, answer outcomes, repeat rules, and relationship arcs for
in-game verification. Daily scenes require Event Repeater 6.5.8 or later. Ideas,
outlines, relationship descriptions, biography, activity descriptions, and
appearance palette remain authoring notes in project.json. Relationship arcs
use linked events and player friendship effects, not custom NPC family data.
The game gender is the selected Male, Female, or Undefined value. Legacy data
without a gender field uses he/him for Male, she/her for Female, and Undefined
otherwise. Original pronouns are preserved in project.json. Gift reaction text is a
starter template. Unlisted gifts follow the game's universal tastes.

Romance uses the game's courtship and marriage systems, default kiss frame 28,
and generic spouse dialogue wherever authored lines do not apply. A supplied
spouse-room section replaces the default room. Supporting cast and supplied maps
are included when authored; WORLD_TESTING.txt records their identities and entrances.
Sleep animations and festival or island participation require further authoring;
festival and island participation remain disabled. project.json is an authoring
backup. Desktop exports can reopen it after extracting the complete pack folder;
they retain the creator plan, test records, and selected artwork and map references.

Before publishing, edit manifest.json Author and Description and add UpdateKeys
if needed. Keep UniqueID and the NPC internal name stable for save compatibility.

Pixelheart output permission notice
Copyright (c) 2026 the Pixelheart copyright holder.

You may use, copy, modify, and distribute Pixelheart-owned template material
intentionally included in this export as part of free Stardew Valley NPC packs
and modified versions of those packs, without separate permission. Recipients
receive the same permission subject to these terms. Retain this output-permission
notice with the pack. Credit to Pixelheart is appreciated but optional;
third-party credit requirements still apply.

Packs containing Pixelheart-owned material may not be sold, paywalled, require
payment or a subscription for access, or earn Donation Points. This permission
does not authorize distributing the Pixelheart editor, its source code, its
executable, or a separate template library, or converting Pixelheart-owned
material for use in other games.

NPC packs may appear in Stardew Valley gameplay videos, recordings, and
livestreams, including monetized content. Advertising revenue, channel
subscriptions, viewer tips, and sponsorship of that gameplay content are
permitted without separate Pixelheart permission. This exception does not permit
charging for covered downloads or earning Donation Points from mod files.

Pixelheart claims no ownership of uploaded artwork, authored dialogue,
character data, or other original user content merely because it was used.
These restrictions cover Pixelheart and its material, not independent rights
in original user content used outside Pixelheart. You must have the necessary
rights in artwork, music, mods, and other third-party material. Their own
licenses and credit requirements continue to apply.

Pixelheart is a free, unofficial community tool for Stardew Valley, a game
developed by ConcernedApe (Eric Barone). Pixelheart is not affiliated with, sponsored by,
or endorsed by ConcernedApe or ConcernedApe LLC. Stardew Valley, its original
content, and its trademarks remain the property of their respective rights
holders. Pixelheart grants no rights in those third-party works or marks.
These are Pixelheart's own terms, not a general policy on all Stardew Valley mods.

Full Pixelheart Source-Available License 1.1:
See LICENSE accompanying the Pixelheart source distribution.

References:
https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide.md
https://stardewvalleywiki.com/Modding:NPC_data
https://stardewvalleywiki.com/Modding:Schedule_data
https://stardewvalleywiki.com/Modding:Gift_taste_data
https://stardewvalleywiki.com/Modding:Dialogue
https://stardewvalleywiki.com/Modding:Event_data
"""
    buffer = io.BytesIO()
    folder = f"[CP] {internal}"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(f"{folder}/{name}", value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        archive.writestr(f"{folder}/README.txt", readme)
        if credits:
            archive.writestr(f"{folder}/CREDITS.txt", credits)
        story_guides = [_story_test_guide(data, story_patches, mod_id, npc_id)] if story_patches else []
        if world_content:
            story_guides.extend(world_content["story_guides"])
        if story_guides:
            archive.writestr(f"{folder}/STORY_TESTING.txt", "\n\n".join(story_guides))
        if world_guide:
            archive.writestr(f"{folder}/WORLD_TESTING.txt", world_guide)
        archive.write(portrait_path, f"{folder}/assets/portraits.png")
        archive.write(sprite_path, f"{folder}/assets/sprites.png")
        for filename, path in appearance_files.items():
            archive.write(path, f"{folder}/{filename}")
    return buffer.getvalue()
