"""What they say: everyday lines, with a provenance notice on every generated line."""
from __future__ import annotations

import re

from pixelheart_core.dialogue_keys import DAYS, HEARTS, SEASONS, build_trigger
from pixelheart_core.dialogue_templates import (apply_dialogue_examples, dialogue_conflicts,
                                                dialogue_has_advanced_commands, overwrite_dialogue_examples)
from pixelheart_core.provenance import validate_source

from ..catalog import item_names
from ..providers import ToolSpec
from . import ToolError, ToolOutcome
from .routine import known_places


TEXT_RULE = ("Use only $h $s $l $a $u feelings (nothing for neutral), #$b# for a new box and @ for the farmer's name; "
             "no other $ or # commands and no {{ tokens.")
_UNKNOWN_CODE = re.compile(r"\$n\b")
ATTRIBUTION = "Written by an AI model from the creator's own description in Pixelheart. Review before sharing."
OCCASIONS = ("introduction", "weekday", "date", "place", "gift", "birthday_gift")


def _nullable_enum(kind, choices, description):
    return {"anyOf": [{"type": kind, "enum": list(choices)}, {"type": "null"}], "description": description}


WHEN_SCHEMA = {
    "type": "object",
    "properties": {
        "occasion": {"type": "string", "enum": list(OCCASIONS),
                     "description": "introduction: first meeting. weekday: a day of the week (optionally one season and a minimum hearts). "
                                    "date: one calendar day. place: at a location (optionally on one weekday). gift: when given an item. "
                                    "birthday_gift: a birthday gift they like or dislike."},
        "day": _nullable_enum("string", DAYS, "Weekday, for weekday and place occasions."),
        "season": _nullable_enum("string", SEASONS, "Season, for weekday (optional) and date occasions."),
        "hearts": _nullable_enum("integer", HEARTS, "Minimum hearts for a weekday line: 0, 2, 4, 6, 8 or 10."),
        "date": {"type": ["integer", "null"], "description": "Day of the month, 1 to 28, for the date occasion."},
        "location": {"type": ["string", "null"], "description": "Place id from list_places, for the place occasion."},
        "item": {"type": ["string", "null"], "description": "Item id from search_items, for the gift occasion."},
        "liked": {"type": ["boolean", "null"], "description": "For birthday_gift: true when they like the gift."},
    },
    "required": ["occasion", "day", "season", "hearts", "date", "location", "item", "liked"],
    "additionalProperties": False,
}
SPEC = ToolSpec(
    "write_dialogue",
    "Write everyday dialogue lines, each for a plain-language occasion. Write a whole set at once "
    "(the introduction and all seven weekdays, for example). " + TEXT_RULE + " "
    "Mode fill keeps lines the creator already wrote; replace overwrites them.",
    {
        "type": "object",
        "properties": {
            "lines": {"type": "array", "description": "1 to 60 lines.",
                      "items": {"type": "object", "properties": {"when": WHEN_SCHEMA, "text": {"type": "string"}},
                                "required": ["when", "text"], "additionalProperties": False}},
            "mode": {"type": "string", "enum": ["fill", "replace"]},
        },
        "required": ["lines", "mode"],
        "additionalProperties": False,
    },
)


def occasion_key(when: dict, places: dict, item_ids=None) -> str:
    """The game's dialogue key for an occasion, or a ToolError the model can act on."""
    if not isinstance(when, dict):
        raise ToolError("Each line needs a 'when' object.")
    occasion = when.get("occasion")
    spec = {"occasion": occasion}
    if occasion == "weekday":
        spec.update(day=when.get("day"), season=when.get("season") or "", hearts=when.get("hearts") or 0)
    elif occasion == "date":
        spec.update(season=when.get("season"), day=when.get("date"))
    elif occasion == "place":
        location = when.get("location")
        if location not in places:
            raise ToolError(f"Unknown place '{location}'. Use list_places to see the places you can choose.")
        spec.update(location=location, day=when.get("day") or "")
    elif occasion == "gift":
        item = str(when.get("item") or "").strip()
        bare = item[3:] if item.startswith("(O)") else item
        if item_ids is not None and bare not in item_ids:
            raise ToolError(f"'{item}' isn't an item id. Use search_items to find it.")
        if item and not item.startswith("("):
            item = "(O)" + item
        spec.update(item=item)
    elif occasion == "birthday_gift":
        spec.update(liked=when.get("liked") is not False)
    elif occasion != "introduction":
        raise ToolError("Choose an occasion: " + ", ".join(OCCASIONS) + ".")
    try:
        return build_trigger(spec)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def check_text(text, index) -> None:
    """The everyday-dialogue text rules, as a ToolError naming the line."""
    if not isinstance(text, str) or not text.strip():
        raise ToolError(f"Line {index}: write some text.")
    if "{{" in text or dialogue_has_advanced_commands(text) or _UNKNOWN_CODE.search(text):
        raise ToolError(f"Line {index}: {TEXT_RULE}")


def provenance_source(model_label: str) -> dict:
    label = re.sub(r"[/\\:]", "-", str(model_label or "").strip())[:200] or "unknown model"
    try:
        return validate_source({"provider": "ai-designer", "source_name": f"AI designer · {label}", "attribution": ATTRIBUTION})
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def write_dialogue(document, input, context):
    lines, mode = input.get("lines"), input.get("mode")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 60:
        raise ToolError("Give between 1 and 60 lines.")
    if mode not in ("fill", "replace"):
        raise ToolError("Choose mode fill or replace.")
    places = known_places(document)
    item_ids = set(item_names(document))
    source = provenance_source(context.model_label)
    examples = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            raise ToolError(f"Line {index} must be an object with when and text.")
        text = line.get("text")
        check_text(text, index)
        examples.append({"trigger": occasion_key(line.get("when"), places, item_ids), "text": text, "source": source})
    character = document["character"]
    records = character.get("dialogues", [])
    try:
        if mode == "fill":
            conflicts = dialogue_conflicts(records, examples)
            decisions = {trigger: "replace" for trigger, rows in conflicts.items()
                         if all(not str(row.get("text", "")).strip() for row in rows)}
            kept = len(conflicts) - len(decisions)
            updated = apply_dialogue_examples(records, examples, decisions)
        else:
            kept = 0
            updated = overwrite_dialogue_examples(records, examples)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    character["dialogues"] = updated
    detail = f"{len(examples) - kept} lines written · {kept} kept"
    receipt = {"section": "dialogue", "title": "Updated Conversations", "detail": detail}
    message = f"Done. {detail}."
    if kept:
        message += " Kept lines already had text; use mode replace to overwrite them."
    return ToolOutcome(document, receipt, message)


TOOLS = ((SPEC, write_dialogue),)
