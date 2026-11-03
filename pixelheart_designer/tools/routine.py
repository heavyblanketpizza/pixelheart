"""Where they go: places the character can use, and their daily routine.

``build_stops`` and ``stops_detail`` are shared with the conditional-routine tool in ``life.py``, so both
routines are validated the same way and read the same in receipts.
"""
from __future__ import annotations

import uuid

from pixelheart_core.game_clock import game_minutes, game_time
from pixelheart_core.locations import LOCATIONS_BY_ID, VANILLA_LOCATIONS
from pixelheart_core.validation import DraftValidationError, validate_nested

from ..providers import ToolSpec
from . import ToolError, ToolOutcome


MAX_STOPS = 30
STOPS_SCHEMA = {
    "type": "array",
    "description": f"1 to {MAX_STOPS} stops.",
    "items": {
        "type": "object",
        "properties": {
            "time": {"type": "string", "description": "When they arrive, such as 9:00 or 900."},
            "location": {"type": "string", "description": "A place id from list_places."},
            "activity": {"type": ["string", "null"], "description": "What they do there, in a few words."},
        },
        "required": ["time", "location", "activity"],
        "additionalProperties": False,
    },
}
PLACES_SPEC = ToolSpec(
    "list_places",
    "List the places a stop or a dialogue occasion can use: the game's locations as 'id · name · note', "
    "then the places the creator designed. Use the ids exactly as listed.",
    {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)
ROUTINE_SPEC = ToolSpec(
    "plan_routine",
    "Replace their everyday routine with timed stops, in time order. Times run from 6:00 AM to 2:00 AM "
    "in ten-minute steps, written like 9:00 or 900 (1:00 AM is 2500). Use place ids from list_places. "
    "The creator places each stop on the map afterwards.",
    {"type": "object", "properties": {"stops": STOPS_SCHEMA}, "required": ["stops"], "additionalProperties": False},
)


def _project_places(document):
    world = document.get("world") if isinstance(document, dict) and isinstance(document.get("world"), dict) else {}
    rows = world.get("locations") if isinstance(world.get("locations"), list) else []
    return {row["internal_name"]: row.get("name") or "Untitled place" for row in rows
            if isinstance(row, dict) and row.get("internal_name") and not row.get("spouse_room")}


def known_places(document) -> dict[str, str]:
    """Vanilla location ids plus the project's own places, mapped to display names."""
    places = {location.id: location.name for location in LOCATIONS_BY_ID.values()}
    places.update(_project_places(document))
    return places


def list_places(document, input, context):
    lines = [f"{location.id} · {location.name}" + (f" · {location.note}" if location.note else "")
             for location in VANILLA_LOCATIONS]
    designed = _project_places(document)
    lines.append("")
    if designed:
        lines.append("Places you designed:")
        lines.extend(f"{place_id} · {name}" for place_id, name in designed.items())
    else:
        lines.append("Places you designed: none yet")
    return ToolOutcome(document, None, "\n".join(lines))


def build_stops(document, stops, existing=()) -> list[dict]:
    """The model's stops as validated schedule records, or a ToolError it can act on.

    Each stop borrows its tile from an ``existing`` stop at the same place when there is one, else the home
    tile when the place is the character's home map, else the editor's default; the creator places them
    on the map afterwards.
    """
    if not isinstance(stops, list) or not 1 <= len(stops) <= MAX_STOPS:
        raise ToolError(f"Give between 1 and {MAX_STOPS} stops.")
    character = document["character"]
    places = known_places(document)
    existing = [row for row in existing if isinstance(row, dict)]
    records, previous = [], None
    for index, stop in enumerate(stops):
        if not isinstance(stop, dict):
            raise ToolError(f"Stop {index + 1} must be an object with time, location and activity.")
        minutes = game_minutes(stop.get("time", ""))
        if minutes is None:
            raise ToolError(f"Stop {index + 1}: enter a game time such as 9:00 or 900, "
                            "in ten-minute steps from 6:00 AM to 2:00 AM.")
        if previous is not None and minutes <= previous:
            raise ToolError("Stops must be in time order.")
        previous = minutes
        location = stop.get("location")
        if location not in places:
            raise ToolError(f"Unknown place '{location}'. Use list_places to see the places you can choose.")
        activity = stop.get("activity")
        activity = activity.strip() if isinstance(activity, str) else ""
        match = next((row for row in existing if row.get("location") == location), None)
        if match is not None:
            x, y, facing = match.get("x", 32), match.get("y", 62), match.get("facing", "down")
        elif location == character.get("home_map"):
            x, y, facing = character.get("home_x", 32), character.get("home_y", 62), "down"
        else:
            x, y, facing = 32, 62, "down"
        records.append({"id": str(uuid.uuid4()), "time": game_time(minutes), "location": location,
                        "x": x, "y": y, "facing": facing, "activity": activity})
    try:
        return validate_nested("schedule", records)
    except DraftValidationError as exc:
        raise ToolError("; ".join(exc.errors.values())) from None


def stops_detail(count) -> str:
    """The receipt detail both routine tools share."""
    return f"{count} stop{'' if count == 1 else 's'} · place {'it' if count == 1 else 'them'} on the map"


def plan_routine(document, input, context):
    character = document["character"]
    schedule = character.get("schedule")
    character["schedule"] = build_stops(document, input.get("stops"), schedule if isinstance(schedule, list) else ())
    detail = stops_detail(len(character["schedule"]))
    receipt = {"section": "schedule", "title": "Updated Daily routine", "detail": detail}
    message = (f"Done. {detail}. Stops stand on a default tile until the creator places them on the map "
               "in Daily routine.")
    return ToolOutcome(document, receipt, message)


TOOLS = ((PLACES_SPEC, list_places), (ROUTINE_SPEC, plan_routine))
