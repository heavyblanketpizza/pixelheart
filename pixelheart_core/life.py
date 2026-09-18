"""Typed dialogue, routine and married-life rules compiled to Content Patcher.

Conditions update at the start of each day, matching the game's daily dialogue
and schedule caches. Later matching rules override earlier ones. Draft rules are
stored but never exported. The compiler has no filesystem, Qt or network access.

Format references:
https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/tokens.md
https://stardewvalleywiki.com/Modding:Schedule_data
https://stardewvalleywiki.com/Modding:Dialogue#Marriage_dialogue
"""
from __future__ import annotations

from copy import deepcopy
import re
import uuid

from .story import event_game_id, exported_npc_id


SEASONS = ("spring", "summer", "fall", "winter")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = dict(zip(WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
RELATIONSHIPS = ("any", "unmarried", "dating", "married")
MOMENTS = {"morning": "Morning at home", "rainy_morning": "Rainy morning at home",
           "evening": "Evening at home", "rainy_evening": "Rainy evening at home"}
COLLECTIONS = ("dialogues", "routines", "spouse_dialogue")
_MAP = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_NPC = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,191}\Z")
_FACING = {"up": 0, "right": 1, "down": 2, "left": 3}


class LifeValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(" ".join(item["message"] for item in issues if item["level"] == "error"))


def new_conditions():
    return {"season": "any", "weather": "any", "weekday": "any", "min_hearts": 0,
            "relationship": "any", "after_event_id": "", "min_house_upgrade": 0}


def new_life_record(kind="dialogues", character=None):
    if kind not in COLLECTIONS:
        raise ValueError("Choose dialogue, a routine, or spouse dialogue.")
    result = {"id": str(uuid.uuid4()), "name": "New " + ("routine" if kind == "routines" else "conversation"),
              "enabled": False, "conditions": new_conditions()}
    if kind == "routines":
        result["stops"] = deepcopy((character or {}).get("schedule", []))
    else:
        result["text"] = ""
    if kind == "spouse_dialogue":
        result["moment"] = "morning"
        result["conditions"]["relationship"] = "married"
    return result


def normalize_life(value=None):
    """Copy the extension, retaining metadata and adding only missing defaults."""
    if value is None:
        value = {}
    result = deepcopy(value)
    if not isinstance(result, dict):
        return result
    for kind in COLLECTIONS:
        result.setdefault(kind, [])
        if isinstance(result[kind], list):
            for index, record in enumerate(result[kind]):
                if not isinstance(record, dict):
                    continue
                entry = {**new_life_record(kind), **record}
                if isinstance(entry.get("conditions"), dict):
                    entry["conditions"] = {**new_conditions(), **entry["conditions"]}
                result[kind][index] = entry
    return result


def _valid_text(value, maximum):
    return (isinstance(value, str) and len(value) <= maximum and "\x00" not in value
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def life_structure_issues(value):
    """Bound imported data, allowing empty text and routes while drafting."""
    issues = []

    def add(field, message):
        issues.append({"level": "error", "field": "life" + ("." + field if field else ""), "message": message})

    if not isinstance(value, dict):
        add("", "Daily life must be an object.")
        return issues
    for kind in COLLECTIONS:
        rows = value.get(kind, [])
        if not isinstance(rows, list) or len(rows) > 100:
            add(kind, "Use a list with at most 100 rules.")
            continue
        seen = set()
        for index, row in enumerate(rows):
            path = f"{kind}.{index}"
            if not isinstance(row, dict):
                add(path, "Each daily-life rule must be an object.")
                continue
            for key, maximum in (("id", 100), ("name", 100), ("text", 8000)):
                if key in row and not _valid_text(row[key], maximum):
                    add(path + "." + key, f"Enter valid text of at most {maximum} characters.")
            identity = row.get("id")
            if identity is not None:
                if not isinstance(identity, str) or not identity or identity in seen:
                    add(path + ".id", "Each rule needs a unique nonempty ID.")
                else:
                    seen.add(identity)
            if "enabled" in row and type(row["enabled"]) is not bool:
                add(path + ".enabled", "Choose whether to include this rule in the mod.")
            conditions = row.get("conditions", {})
            if not isinstance(conditions, dict):
                add(path + ".conditions", "Conditions must be an object.")
                continue
            options = {"season": ("any", *SEASONS), "weather": ("any", "sunny", "rainy", "snowy"),
                       "weekday": ("any", *WEEKDAYS), "relationship": RELATIONSHIPS}
            for key, choices in options.items():
                if key in conditions and conditions[key] not in choices:
                    add(path + ".conditions." + key, "Choose one of: " + ", ".join(choices) + ".")
            for key, limit in (("min_hearts", 14), ("min_house_upgrade", 3)):
                if key in conditions and (type(conditions[key]) is not int or not 0 <= conditions[key] <= limit):
                    add(path + ".conditions." + key, f"Enter a whole number from 0 to {limit}.")
            if not _valid_text(conditions.get("after_event_id", ""), 100):
                add(path + ".conditions.after_event_id", "Choose a story event.")
            if kind == "spouse_dialogue" and (not isinstance(row.get("moment", "morning"), str) or row.get("moment", "morning") not in MOMENTS):
                add(path + ".moment", "Choose a supported married-life moment.")
            if kind == "routines":
                stops = row.get("stops", [])
                if not isinstance(stops, list) or len(stops) > 100:
                    add(path + ".stops", "Use a list with at most 100 stops.")
                    continue
                for stop_index, stop in enumerate(stops):
                    stop_path = f"{path}.stops.{stop_index}"
                    if not isinstance(stop, dict):
                        add(stop_path, "Each stop must be an object.")
                        continue
                    for key, limit in (("location", 80), ("activity", 300), ("id", 100)):
                        if key in stop and not _valid_text(stop[key], limit):
                            add(stop_path + "." + key, f"Enter valid text of at most {limit} characters.")
                    for key in ("x", "y"):
                        if key in stop and (type(stop[key]) is not int or not 0 <= stop[key] <= 1000):
                            add(stop_path + "." + key, "Tile coordinates must be whole numbers from 0 to 1000.")
                    if "time" in stop and not (type(stop["time"]) is int or _valid_text(stop["time"], 5)):
                        add(stop_path + ".time", "Choose a time from 06:00 to 26:00.")
                    facing = stop.get("facing", "down")
                    if not ((type(facing) is int and 0 <= facing <= 3) or (isinstance(facing, str) and facing in (*_FACING, "0", "1", "2", "3"))):
                        add(stop_path + ".facing", "Choose a facing direction.")
    return issues


def _time(value):
    if isinstance(value, str):
        value = value.replace(":", "")
        if not value.isdecimal():
            raise ValueError
        value = int(value)
    if type(value) is not int or not 600 <= value <= 2600 or value % 100 >= 60 or value % 10:
        raise ValueError
    return value


def _plain_dialogue_error(text):
    if any(part in text for part in ("{{", "}}", "[", "]")):
        return "Use plain dialogue and expression tags; tokens and bracketed item commands are not supported."
    if any(tag not in {"h", "s", "a", "u", "l", "b"} and not tag.isdecimal()
           for tag in re.findall(r"\$([A-Za-z]+|[0-9]+)", text)):
        return "Use expression tags and #$b# line breaks; advanced dialogue commands need a separate editor."
    if re.search(r"%(?:fork|revealtaste)", text, re.I):
        return "Use plain dialogue without event-fork or gift-discovery commands."
    if any(match.end() < len(text) and text[match.end()] not in "#\r\n" for match in re.finditer(r"\$[0-9]+", text)):
        return "Put numeric portrait expressions at the end of a line."
    return ""


def life_issues(character):
    raw = character.get("life", {})
    issues = life_structure_issues(raw)
    if issues:
        return issues
    life = normalize_life(raw)
    events = {row.get("id"): row for row in character.get("events", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}

    def add(path, message, level="error"):
        issues.append({"level": level, "field": "life." + path, "message": message})

    for kind in COLLECTIONS:
        for index, row in enumerate(life[kind]):
            path = f"{kind}.{index}"
            if not row["enabled"]:
                continue
            conditions = row["conditions"]
            if not row["name"].strip():
                add(path + ".name", "Give this rule a name so you can find it later.")
            if character.get("romanceable") is False and (conditions["relationship"] in ("dating", "married") or kind == "spouse_dialogue" or conditions["min_hearts"] > 10):
                add(path + ".conditions.relationship", "Enable romance for dating, marriage, or more than ten hearts; otherwise keep this rule as a draft.")
            previous = conditions["after_event_id"]
            if previous:
                prior = events.get(previous)
                prior_story = prior.get("story", {}) if prior is not None else {}
                if not isinstance(prior_story, dict):
                    prior_story = {}
                if prior is None:
                    add(path + ".conditions.after_event_id", "Choose an existing story event, or remove this requirement.")
                elif prior_story.get("stage") != "ready":
                    add(path + ".conditions.after_event_id", "Mark the required story event ready before including this rule.")
                elif prior_story.get("repeat", "once") != "once":
                    add(path + ".conditions.after_event_id", "Choose a one-time story event. Repeating events forget their completion each morning.")
            if kind != "routines":
                if not row["text"].strip():
                    add(path + ".text", "Write their dialogue before including this rule.")
                elif error := _plain_dialogue_error(row["text"]):
                    add(path + ".text", error)
                if kind == "spouse_dialogue" and conditions["relationship"] not in ("any", "married"):
                    add(path + ".conditions.relationship", "Spouse dialogue plays while married. Choose Married or Any relationship.")
            else:
                if not row["stops"]:
                    add(path + ".stops", "Add at least one destination to this routine.")
                last_time = -1
                for stop_index, stop in enumerate(row["stops"]):
                    stop_path = f"{path}.stops.{stop_index}"
                    try:
                        time = _time(stop.get("time"))
                        if time <= last_time:
                            add(stop_path + ".time", "Routine times must increase without duplicate times.")
                        last_time = time
                    except ValueError:
                        add(stop_path + ".time", "Choose a ten-minute time from 06:00 to 26:00.")
                    if not _MAP.fullmatch(stop.get("location", "")):
                        add(stop_path + ".location", "Choose a map for each destination.")
                    for axis in ("x", "y"):
                        if type(stop.get(axis)) is not int:
                            add(stop_path + "." + axis, "Choose a tile for each destination.")
                if conditions["relationship"] == "married" and row["stops"] and row["stops"][-1].get("location") != "bed":
                    add(path + ".stops", "This married routine does not finish at home. Use Return to farmhouse or verify the last destination in-game.", "warning")
                add(path, "Routines are chosen each morning. Test paths after sleeping; in co-op, the host's conditions control shared NPC schedules.", "warning")
    return issues


def compile_conditions(conditions, character, npc_id):
    conditions = {**new_conditions(), **conditions}
    result = {}
    if conditions["season"] != "any":
        result["Season"] = conditions["season"]
    if conditions["weather"] != "any":
        result["Weather |locationContext=Default"] = {"sunny": "Sun, Wind", "rainy": "Rain, Storm, GreenRain", "snowy": "Snow"}[conditions["weather"]]
    if conditions["weekday"] != "any":
        result["DayOfWeek"] = DAY_NAMES[conditions["weekday"]]
    if conditions["min_hearts"]:
        result[f"Hearts:{npc_id}"] = "{{Range: " + str(conditions["min_hearts"]) + ", 14}}"
    if conditions["relationship"] != "any":
        result[f"Relationship:{npc_id}"] = {"unmarried": "Unmet, Friendly, Dating, Engaged, Divorced", "dating": "Dating", "married": "Married"}[conditions["relationship"]]
    if conditions["min_house_upgrade"]:
        result["FarmhouseUpgrade"] = "{{Range: " + str(conditions["min_house_upgrade"]) + ", 3}}"
    if conditions["after_event_id"]:
        event = next(row for row in character.get("events", []) if row.get("id") == conditions["after_event_id"])
        result["HasSeenEvent"] = event_game_id(event, character)
    return result


def compile_route(stops):
    return "/".join(f"{_time(stop['time'])} {stop['location']} {stop['x']} {stop['y']} {_FACING.get(stop.get('facing', 'down'), stop.get('facing', 2))}" for stop in stops)


def compile_life(character, npc_id=None):
    issues = life_issues(character)
    if any(issue["level"] == "error" for issue in issues):
        raise LifeValidationError(issues)
    npc_id = npc_id or exported_npc_id(character)
    if not isinstance(npc_id, str) or not _NPC.fullmatch(npc_id):
        raise LifeValidationError([{"level": "error", "field": "internal_name", "message": "Use a valid NPC ID."}])
    life = normalize_life(character.get("life", {}))
    patches, files = [], {}
    if any(row["enabled"] for row in life["spouse_dialogue"]) or any(row["enabled"] and row["conditions"]["relationship"] == "married" for row in life["dialogues"]):
        files["assets/marriage-dialogue.json"] = {}
        patches.append({"Action": "Load", "Target": f"Characters/Dialogue/MarriageDialogue{npc_id}", "FromFile": "assets/marriage-dialogue.json"})
    for kind in COLLECTIONS:
        for row in life[kind]:
            if not row["enabled"]:
                continue
            conditions = row["conditions"]
            when = compile_conditions(conditions, character, npc_id)
            if kind == "routines":
                route = compile_route(row["stops"])
                if conditions["relationship"] == "married":
                    # Date-specific marriage keys apply on rainy days too. The
                    # weekday marriage keys are intentionally dry-weather-only.
                    entries = {f"marriage_{season}_{day}": route for season in SEASONS for day in range(1, 29)}
                else:
                    entries = {key: route for key in (*SEASONS, "rain", "rain2")}
                target = f"Characters/schedules/{npc_id}"
            elif kind == "dialogues":
                days = WEEKDAYS if conditions["weekday"] == "any" else (conditions["weekday"],)
                if conditions["relationship"] == "married":
                    entries = {}
                    for moment in MOMENTS:
                        entries.update(_spouse_entries(moment, row["text"], npc_id))
                    target = f"Characters/Dialogue/MarriageDialogue{npc_id}"
                else:
                    entries = {day: row["text"] for day in days}
                    target = f"Characters/Dialogue/{npc_id}"
            else:
                entries = _spouse_entries(row["moment"], row["text"], npc_id)
                target = f"Characters/Dialogue/MarriageDialogue{npc_id}"
            patch = {"Action": "EditData", "Target": target, "Entries": entries}
            if when:
                patch["When"] = when
            patches.append(patch)
    return {"patches": patches, "files": files}


def _spouse_entries(moment, text, npc_id):
    prefix = {"morning": "Indoor_Day", "rainy_morning": "Rainy_Day", "evening": "Indoor_Night", "rainy_evening": "Rainy_Night"}[moment]
    entries = {f"{prefix}_{index}": text for index in range(6 if moment == "rainy_evening" else 5)}
    if moment in ("evening", "rainy_evening"):
        entries[f"{prefix}_{npc_id}"] = text
    return entries
