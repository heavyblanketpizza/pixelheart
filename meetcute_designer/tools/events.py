"""Every heart brings you closer: one heart event at a time, never marked ready by the model."""
from __future__ import annotations

from meetcute_core.story import event_issues, new_beat, new_event, normalize_event, structure_issues

from ..providers import ToolSpec
from . import ToolError, ToolOutcome
from .routine import known_places


MAX_LINES = 40
# The portrait commands the game understands; see meetcute_core.dialogue_pages.EMOTIONS.
FEELING_CODES = {"happy": "$h", "sad": "$s", "love": "$l", "angry": "$a", "unique": "$u", "neutral": ""}
SPEC = ToolSpec(
    "write_heart_event",
    "Write or rewrite the heart event at one friendship level (2, 4, 6, 8, 10 or 14 hearts; 14 only when they can "
    "be romanced). Give it a name, a place, the premise, the conflict and the outcome, plus the lines spoken in the "
    "scene. The creator stages positions and marks the event ready in the app.",
    {
        "type": "object",
        "properties": {
            "hearts": {"type": "integer", "description": "Friendship level, 0 to 14."},
            "name": {"type": "string", "description": "A short title, up to 100 characters."},
            "location": {"type": "string", "description": "A place id from list_places."},
            "premise": {"type": "string", "description": "What the scene is about."},
            "conflict": {"type": "string", "description": "What complicates it."},
            "outcome": {"type": "string", "description": "What changes by the end; may be empty."},
            "script": {
                "type": "array",
                "description": "Up to 40 spoken lines, in order. Empty to keep or outline the event without lines.",
                "items": {
                    "type": "object",
                    "properties": {
                        "speaker": {"type": "string", "enum": ["npc", "farmer"]},
                        "text": {"type": "string"},
                        "feeling": {"anyOf": [{"type": "string", "enum": ["happy", "sad", "love", "angry", "unique", "neutral"]},
                                              {"type": "null"}],
                                    "description": "Their portrait while saying it; ignored for the farmer."},
                    },
                    "required": ["speaker", "text", "feeling"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["hearts", "name", "location", "premise", "conflict", "outcome", "script"],
        "additionalProperties": False,
    },
)


def _text(input, field, *, required=True, limit=8000):
    value = input.get(field)
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ToolError(f"Give {field} as text" + ("" if required else " (it may be empty)") + f" of at most {limit} characters.")
    return value.strip()


def _beats(script):
    if not isinstance(script, list) or len(script) > MAX_LINES:
        raise ToolError(f"The script holds up to {MAX_LINES} lines.")
    beats = []
    for index, line in enumerate(script, start=1):
        if not isinstance(line, dict) or line.get("speaker") not in ("npc", "farmer"):
            raise ToolError(f"Line {index}: choose speaker npc or farmer.")
        text = line.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ToolError(f"Line {index}: write some text.")
        feeling = line.get("feeling") or "neutral"
        if feeling not in FEELING_CODES:
            raise ToolError(f"Line {index}: choose a feeling from " + ", ".join(FEELING_CODES) + ".")
        beat = new_beat("dialogue")
        beat.update(actor="$npc" if line["speaker"] == "npc" else "farmer",
                    text=_curly(text.strip()) + (FEELING_CODES[feeling] if line["speaker"] == "npc" else ""))
        beats.append(beat)
    return beats


def _curly(text):
    """Scene dialogue needs curly quotation marks; models type straight ones."""
    result, opening = [], True
    for char in text:
        if char == '"':
            result.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            result.append(char)
    return "".join(result)


def _owned_by_tool(field, wrote_beats):
    """Readiness issues about what the model just wrote; the creator's drafts are notes."""
    return field in ("hearts", "name", "location", "story.relationship") or (wrote_beats and field.startswith("story.beats."))


def write_heart_event(document, input, context):
    hearts = input.get("hearts")
    if type(hearts) is not int or not 0 <= hearts <= 14:
        raise ToolError("Give hearts as a whole number from 0 to 14.")
    name = _text(input, "name", limit=100)
    premise, conflict = _text(input, "premise"), _text(input, "conflict")
    outcome = _text(input, "outcome", required=False)
    character = document["character"]
    location = input.get("location")
    if location not in known_places(document):
        raise ToolError(f"Unknown place '{location}'. Use list_places to see the places you can choose.")
    beats = _beats(input.get("script"))

    events = character.setdefault("events", [])
    index = next((i for i, row in enumerate(events) if isinstance(row, dict) and row.get("hearts") == hearts), None)
    event = normalize_event(events[index]) if index is not None else new_event(character)
    was_ready = event["story"].get("stage") == "ready"
    event.update(name=name, location=location, hearts=hearts)
    event["story"].update(premise=premise, conflict=conflict, outcome=outcome)
    if beats:
        event["story"]["beats"] = beats
        stage = "scene"
    elif was_ready or event["story"].get("stage") == "scene":
        stage = "scene"
    else:
        stage = "outline"
    event["story"]["stage"] = stage

    errors = [issue["message"] for issue in structure_issues(event, "events")]
    warnings = []
    if not errors:
        for issue in event_issues(event, character):
            if issue["level"] == "error" and _owned_by_tool(issue["field"], bool(beats)):
                errors.append(issue["message"])
            elif stage == "scene":
                warnings.append(issue["message"])
    if errors:
        raise ToolError("; ".join(errors))
    if index is not None:
        events[index] = event
    else:
        events.append(event)
    detail = f"{hearts}-heart event · {name} · {len(beats)} lines"
    if was_ready:
        detail += " · staged positions need another look"
    message = f"Done. {detail}."
    if warnings:
        message += " Notes for the creator before marking it ready: " + " ".join(warnings)
    receipt = {"section": "story", "title": "Updated Heart events", "detail": detail}
    return ToolOutcome(document, receipt, message)


TOOLS = ((SPEC, write_heart_event),)
