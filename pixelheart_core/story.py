"""Offline story development and a deliberately bounded Stardew event compiler.

Draft prose is never executable. Only explicitly ready scenes become event data.
The supported commands and preconditions follow Stardew Valley's 1.6 event format:
https://stardewvalleywiki.com/Modding:Event_data
No functions mutate their arguments or depend on Qt, artwork, or network access.
"""
from __future__ import annotations

import copy
import hashlib
import re
import uuid


EVENT_STAGES = ("idea", "outline", "scene", "ready")
RELATIONSHIP_STAGES = ("idea", "outline", "ready")
BEAT_KINDS = ("dialogue", "emote", "move", "pause", "friendship", "choice")
EVENT_TEMPLATES = ("blank", "first_meeting", "conflict", "reconciliation")
FACING = {"up": 0, "right": 1, "down": 2, "left": 3}
EMOTES = (4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 52, 56, 60)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,191}\Z")
_MAP = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_MUSIC = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z")


class StoryValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(" ".join(issue["message"] for issue in issues if issue["level"] == "error"))


def _id():
    return str(uuid.uuid4())


def new_actor(name="$npc"):
    return {"id": _id(), "name": name, "x": 32, "y": 62, "facing": 2}


def new_beat(kind="dialogue"):
    if kind not in BEAT_KINDS:
        raise ValueError("Choose a supported scene beat.")
    result = {"id": _id(), "kind": kind, "actor": "$npc", "text": "", "x": 0,
              "y": 0, "facing": 2, "duration": 500, "amount": 25, "emote": 20}
    if kind == "choice":
        result["choices"] = [{"id": _id(), "label": "", "text": "", "friendship": 0} for _ in range(2)]
    return result


def _event_story():
    return {"stage": "idea", "premise": "", "conflict": "", "outcome": "",
            "relationship_id": "", "previous_event_id": "", "season": "any",
            "weather": "any", "time_start": 600, "time_end": 2400,
            "music": "none", "actors": [], "beats": [], "relationship": "any",
            "min_house_upgrade": 0, "repeat": "once"}


def _relationship_story():
    return {"stage": "idea", "desire": "", "tension": "", "progression": "",
            "resolution": "", "target": "farmer"}


def normalize_event(record):
    """Add editor defaults to a copy while preserving all extension metadata."""
    result = {"id": _id(), "name": "New event", "hearts": 2,
              "location": "Town", "description": "", **copy.deepcopy(record)}
    if isinstance(result.get("story", {}), dict):
        result["story"] = {**_event_story(), **result.get("story", {})}
        for collection, factory in (("actors", new_actor), ("beats", new_beat)):
            entries = result["story"][collection]
            if isinstance(entries, list):
                normalized = []
                for entry in entries:
                    if isinstance(entry, dict):
                        item = {**factory(), **entry}
                        if isinstance(item.get("facing"), str) and item["facing"] in FACING:
                            item["facing"] = FACING[item["facing"]]
                        normalized.append(item)
                    else:
                        normalized.append(entry)
                result["story"][collection] = normalized
    return result


def normalize_relationship(record):
    result = {"id": _id(), "name": "", "relation": "Friend", "description": "",
              **copy.deepcopy(record)}
    if isinstance(result.get("story", {}), dict):
        result["story"] = {**_relationship_story(), **result.get("story", {})}
    return result


def new_event(character=None, template="blank"):
    if template not in EVENT_TEMPLATES:
        raise ValueError("Choose a supported event template.")
    character = character or {}
    result = normalize_event({})
    result["location"] = character.get("home_map") or "Town"
    npc = new_actor()
    npc.update(x=character.get("home_x", 32), y=character.get("home_y", 62))
    farmer = new_actor("farmer")
    farmer.update(x=npc["x"], y=min(1000, npc["y"] + 2), facing=0)
    result["story"]["actors"] = [npc, farmer]
    templates = {
        "first_meeting": ("A small beginning", "An everyday encounter reveals an unexpected side of them.",
                          "They are hesitant to let someone new into their routine.",
                          "A small act of kindness opens the door to friendship."),
        "conflict": ("A fault line", "Something they care about is put at risk.",
                     "Two people want the same thing for different reasons.",
                     "An honest disagreement changes what they understand about each other."),
        "reconciliation": ("Finding common ground", "They meet again after a difficult moment.",
                           "Pride makes it hard to say what they really need.",
                           "They make one concrete promise and take a step toward trust."),
    }
    if template != "blank":
        name, premise, conflict, outcome = templates[template]
        result["name"] = name
        result["story"].update(stage="outline", premise=premise, conflict=conflict, outcome=outcome)
        result["story"]["beats"] = [new_beat()]
    return result


def new_relationship():
    return normalize_relationship({})


def relationship_events(relationship, character=None):
    """Plan a linked four-scene arc; repeated calls reuse existing milestones.

    Stable UUID5 identities prevent duplicate arcs when the creator asks again.
    The caller decides which returned, new records to append to their project.
    """
    relation = normalize_relationship(relationship)
    character = character or {}
    existing = {item.get("id"): item for item in character.get("events", []) if isinstance(item, dict)}
    result = []
    previous = ""
    for hearts, label, field in ((2, "First connection", "desire"), (4, "Friction", "tension"),
                                 (6, "A turning point", "progression"), (8, "A new understanding", "resolution")):
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pixelheart:relationship:{relation['id']}:{hearts}"))
        if event_id in existing:
            event = normalize_event(existing[event_id])
        else:
            event = new_event(character)
            event.update(id=event_id, name=f"{relation['name'] or 'Relationship'} · {label}", hearts=hearts)
            # A long relationship name must still fit the event title schema.
            event["name"] = event["name"][:100]
            event["description"] = relation.get("description", "")
            event["story"].update(stage="outline", relationship_id=relation["id"], previous_event_id=previous,
                                   premise=relation["story"].get(field, ""),
                                   conflict=relation["story"].get("tension", ""),
                                   outcome=relation["story"].get("resolution", "") if hearts == 8 else "")
            target = relation["story"].get("target", "farmer")
            if target and target != "farmer" and target not in ("$npc", character.get("internal_name")):
                actor = new_actor(target)
                actor.update(x=min(1000, event["story"]["actors"][0]["x"] + 2), facing=3)
                event["story"]["actors"].append(actor)
        result.append(event)
        previous = event_id
    return result


def structure_issues(record, kind="events"):
    """Validate bounded, typed authoring data without requiring completeness."""
    issues = []

    def add(field, message):
        issues.append({"level": "error", "field": field, "message": message})

    def text(obj, field, limit, prefix=""):
        if field in obj and (not isinstance(obj[field], str) or len(obj[field]) > limit or "\x00" in obj[field]
                             or any(0xD800 <= ord(ch) <= 0xDFFF for ch in obj[field])):
            add(prefix + field, f"Enter valid Unicode text of at most {limit} characters without null characters.")

    def integer(obj, field, low, high, prefix=""):
        if field in obj and (type(obj[field]) is not int or not low <= obj[field] <= high):
            add(prefix + field, f"Enter a whole number from {low} to {high}.")

    def choice(obj, field, choices, prefix=""):
        if field in obj and (not isinstance(obj[field], str) or obj[field] not in choices):
            add(prefix + field, "Choose one of: " + ", ".join(choices) + ".")

    if not isinstance(record, dict):
        add("", "Each story record must be an object.")
        return issues
    text(record, "id", 100)
    if "id" in record and not record["id"]:
        add("id", "Each story record needs a nonempty ID.")
    text(record, "name", 100 if kind == "events" else 80)
    text(record, "description", 8000 if kind == "events" else 2000)
    if kind == "events":
        text(record, "location", 80)
        integer(record, "hearts", 0, 14)
    else:
        text(record, "relation", 80)
    if "story" not in record:
        return issues
    story = record["story"]
    if not isinstance(story, dict):
        add("story", "Story development must be an object.")
        return issues
    choice(story, "stage", EVENT_STAGES if kind == "events" else RELATIONSHIP_STAGES, "story.")
    if kind == "relationships":
        for field in ("desire", "tension", "progression", "resolution"):
            text(story, field, 8000, "story.")
        text(story, "target", 192, "story.")
        return issues
    for field in ("premise", "conflict", "outcome"):
        text(story, field, 8000, "story.")
    for field in ("relationship_id", "previous_event_id", "music"):
        text(story, field, 100, "story.")
    choice(story, "season", ("any", "spring", "summer", "fall", "winter"), "story.")
    choice(story, "weather", ("any", "sunny", "rainy"), "story.")
    choice(story, "relationship", ("any", "unmarried", "dating", "married"), "story.")
    choice(story, "repeat", ("once", "daily"), "story.")
    integer(story, "min_house_upgrade", 0, 3, "story.")
    for field in ("time_start", "time_end"):
        integer(story, field, 600, 2600, "story.")
    for collection, limit in (("actors", 16), ("beats", 200)):
        entries = story.get(collection, [])
        if not isinstance(entries, list) or len(entries) > limit:
            add(f"story.{collection}", f"Use a list with up to {limit} {collection}.")
            continue
        seen = set()
        for index, entry in enumerate(entries):
            prefix = f"story.{collection}.{index}."
            if not isinstance(entry, dict):
                add(prefix[:-1], "Each entry must be an object.")
                continue
            text(entry, "id", 100, prefix)
            if "id" in entry:
                if not isinstance(entry["id"], str) or not entry["id"] or entry["id"] in seen:
                    add(prefix + "id", "Each entry needs a unique nonempty text ID.")
                elif len(entry["id"]) <= 100:
                    seen.add(entry["id"])
            if collection == "actors":
                text(entry, "name", 192, prefix)
                for field in ("x", "y"):
                    integer(entry, field, 0, 1000, prefix)
            else:
                choice(entry, "kind", BEAT_KINDS, prefix)
                text(entry, "actor", 192, prefix)
                text(entry, "text", 8000, prefix)
                for field in ("x", "y"):
                    integer(entry, field, -100, 100, prefix)
                integer(entry, "duration", 1, 60000, prefix)
                integer(entry, "amount", -1000, 1000, prefix)
                integer(entry, "emote", 0, 100, prefix)
                if entry.get("kind") == "choice":
                    options = entry.get("choices", [])
                    if not isinstance(options, list) or len(options) > 2:
                        add(prefix + "choices", "A choice has two outcomes.")
                    else:
                        for option_index, option in enumerate(options):
                            option_prefix = f"{prefix}choices.{option_index}."
                            if not isinstance(option, dict):
                                add(option_prefix[:-1], "Each choice outcome must be an object.")
                                continue
                            for field, maximum in (("id", 100), ("label", 200), ("text", 8000)):
                                text(option, field, maximum, option_prefix)
                            integer(option, "friendship", -1000, 1000, option_prefix)
            facing = entry.get("facing", 2)
            if not ((type(facing) is int and facing in range(4)) or (isinstance(facing, str) and facing in FACING)):
                add(prefix + "facing", "Choose up, right, down, or left.")
    return issues


def exported_npc_id(character):
    internal = str(character.get("internal_name", "")).strip()
    suffix = hashlib.sha256(str(character.get("id") or internal).encode()).hexdigest()[:10]
    return f"Pixelheart.{internal}_{suffix}_{internal}"


def event_game_id(event, character):
    """A stable, mod-namespaced game identity independent of title and ordering."""
    identity = str(character.get("id") or character.get("internal_name") or "")
    value = str(event.get("id") or "")
    suffix = hashlib.sha256((identity + "\x00" + value).encode()).hexdigest()[:24]
    return "{{ModId}}_story_" + suffix


def _actor_name(name, character, npc_id=None):
    if name in ("$npc", character.get("internal_name"), exported_npc_id(character)):
        return npc_id or exported_npc_id(character)
    return name


def event_issues(event, character=None):
    """Return readiness issues relative to one event, including dependencies."""
    character = character or {}
    issues = structure_issues(event)
    if issues:
        return issues
    record = normalize_event(event)
    story = record["story"]

    def add(field, message, level="error"):
        issues.append({"level": level, "field": field, "message": message})

    if not isinstance(event.get("id"), str) or not event["id"].strip():
        add("id", "Save this event with a stable ID before making it playable.")
    if not record["name"].strip():
        add("name", "Give this event a title.")
    if character.get("romanceable") is False and record["hearts"] > 10:
        add("hearts", "A villager who cannot be romanced cannot reach 11 hearts. Choose 0–10 hearts, or enable romance.")
    if character.get("romanceable") is False and story["relationship"] in ("dating", "married"):
        add("story.relationship", "Enable romance before requiring dating or marriage, or choose Any relationship.")
    if story["repeat"] == "daily":
        add("story.repeat", "This scene can play again after each new day or save reload. Players need Event Repeater 6.5.8 or later; friendship effects can be earned on every repeat.", "warning")
    if not _MAP.fullmatch(record["location"]):
        add("location", "Use the exact map ID, with letters, numbers, underscores, dots, or hyphens.")
    if not _MUSIC.fullmatch(story["music"]):
        add("story.music", "Use a music cue ID, 'none', or 'continue'.")
    for field in ("time_start", "time_end"):
        value = story[field]
        if value % 100 >= 60 or value % 10:
            add("story." + field, "Use ten-minute times from 06:00 through 26:00.")
    if story["time_start"] > story["time_end"]:
        add("story.time_end", "The end time must be at or after the start time.")
    names = {}
    for index, actor in enumerate(story["actors"]):
        resolved = _actor_name(actor["name"], character)
        if not _IDENTIFIER.fullmatch(resolved):
            add(f"story.actors.{index}.name", "Use $npc, farmer, or an NPC's exact internal ID.")
        elif resolved in names:
            add(f"story.actors.{index}.name", "Each actor may appear only once in the scene.")
        names[resolved] = actor
    if exported_npc_id(character) not in names:
        add("story.actors", "Add your NPC ($npc) to the scene cast.")
    if "farmer" not in names:
        add("story.actors", "Add farmer to set the player's starting position.")
    if not story["beats"]:
        add("story.beats", "Add at least one scene beat before marking this event ready.")
    # Simulate endpoint positions to catch paths outside the supported tile range.
    positions = {name: [actor["x"], actor["y"]] for name, actor in names.items()}
    for index, beat in enumerate(story["beats"]):
        prefix = f"story.beats.{index}."
        kind = beat["kind"]
        actor = _actor_name(beat["actor"], character)
        if kind != "pause" and actor not in names:
            add(prefix + "actor", "Choose an actor who is present in this scene.")
        if kind == "dialogue":
            if not beat["text"].strip():
                add(prefix + "text", "Write the dialogue for this beat.")
            elif any(value in beat["text"] for value in ('"', "\\", "{{", "}}")) or any(ord(ch) < 32 and ch not in "\r\n\t" for ch in beat["text"]):
                add(prefix + "text", "Use curly quotation marks (“ ”) and plain dialogue; backslashes and Content Patcher tokens are not supported.")
            # Dialogue can itself execute trigger actions. Permit expression and
            # line-break tags only, so speech cannot bypass the bounded editor.
            elif any(tag not in {"h", "s", "a", "u", "l", "b"} and not tag.isdecimal()
                     for tag in re.findall(r"\$([A-Za-z]+|[0-9]+)", beat["text"])):
                add(prefix + "text", "Use expression tags ($h, $s, $a, $u, $l, or a portrait number) and #$b# line breaks; advanced dialogue commands are not supported in scene beats.")
            elif re.search(r"%(?:fork|revealtaste)", beat["text"], re.IGNORECASE) or "[" in beat["text"] or "]" in beat["text"]:
                add(prefix + "text", "Scene dialogue cannot grant bracketed items, reveal gift tastes, or fork events. Use plain words and expression tags.")
            elif any(match.end() < len(beat["text"]) and beat["text"][match.end()] not in "#\r\n"
                     for match in re.finditer(r"\$[0-9]+", beat["text"])):
                # '$1 letter#...' changes mail state; '$1' at the end of a line
                # is a portrait. Do not let the former bypass authored effects.
                add(prefix + "text", "Put numeric portrait tags at the end of a line, before # or a line break. Mail-state commands are not supported.")
        elif kind == "emote" and beat["emote"] not in EMOTES:
            add(prefix + "emote", "Choose a supported emote ID: " + ", ".join(map(str, EMOTES)) + ".")
        elif kind == "move":
            if (beat["x"] == 0) == (beat["y"] == 0):
                add(prefix + "x", "Move along one axis at a time; set exactly one tile offset to zero.")
            elif actor in positions:
                positions[actor][0] += beat["x"]
                positions[actor][1] += beat["y"]
                if any(not 0 <= value <= 1000 for value in positions[actor]):
                    add(prefix + "x", "This move leaves the supported map tile range (0–1000).")
        elif kind == "friendship" and actor == "farmer":
            add(prefix + "actor", "Friendship points belong to an NPC; choose $npc or another NPC.")
        elif kind == "choice":
            if index != len(story["beats"]) - 1:
                add(prefix + "kind", "Place the choice at the end of the scene. Each answer has its own closing response and friendship effect.")
            if actor == "farmer":
                add(prefix + "actor", "Choose the NPC who responds to the player's answer.")
            if not beat["text"].strip() or any(char in beat["text"] for char in ('"', "\\", "#", "$", "\n", "\r", "{{", "}}")):
                add(prefix + "text", "Write one plain question without quotation marks, line breaks, or dialogue commands.")
            options = beat.get("choices", [])
            if len(options) != 2:
                add(prefix + "choices", "Write exactly two answers and their outcomes.")
            for option_index, option in enumerate(options):
                option_prefix = f"{prefix}choices.{option_index}."
                answer = option.get("label", "")
                if not answer.strip() or any(char in answer for char in ('"', "\\", "#", "$", "\n", "\r", "{{", "}}")):
                    add(option_prefix + "label", "Write a plain player answer without quotation marks, line breaks, or dialogue commands.")
                response = option.get("text", "")
                # Reuse exactly the supported dialogue validation, without a
                # recursive choice or dependencies in the synthetic scene.
                probe = copy.deepcopy(record)
                probe["story"].update(previous_event_id="", relationship_id="", repeat="once",
                                       beats=[{**new_beat(), "actor": beat["actor"], "text": response}])
                for issue in event_issues(probe, character):
                    if issue["field"] == "story.beats.0.text":
                        add(option_prefix + "text", issue["message"])
    events = [item for item in character.get("events", []) if isinstance(item, dict)]
    by_id = {item.get("id"): item for item in events if isinstance(item.get("id"), str)}
    previous = story["previous_event_id"]
    if previous:
        if previous == record["id"]:
            add("story.previous_event_id", "An event cannot require itself.")
        elif previous not in by_id:
            add("story.previous_event_id", "The prerequisite event was removed or cannot be found.")
        else:
            prior = by_id[previous]
            prior_story = prior.get("story") if isinstance(prior.get("story"), dict) else {}
            if prior_story.get("repeat", "once") != "once":
                add("story.previous_event_id", "A repeating event forgets its completion each morning. Choose a one-time event as the story prerequisite.")
            if prior_story.get("stage") != "ready":
                add("story.previous_event_id", "Mark the prerequisite event ready too, or remove this requirement.")
            if type(prior.get("hearts")) is int and prior["hearts"] > record["hearts"]:
                add("hearts", "This scene requires fewer hearts than its prerequisite; consider increasing the milestone.", "warning")
            visited = {record["id"]}
            cursor = previous
            while cursor and cursor in by_id:
                if cursor in visited:
                    add("story.previous_event_id", "These events form a prerequisite cycle that can never start.")
                    break
                visited.add(cursor)
                prior_story = by_id[cursor].get("story")
                cursor = prior_story.get("previous_event_id", "") if isinstance(prior_story, dict) else ""
                if not isinstance(cursor, str):
                    break
    relation_id = story["relationship_id"]
    if relation_id:
        relation = next((item for item in character.get("relationships", [])
                         if isinstance(item, dict) and item.get("id") == relation_id), None)
        if relation is None:
            add("story.relationship_id", "The linked relationship was removed or cannot be found.")
        elif not structure_issues(relation, "relationships"):
            target = normalize_relationship(relation)["story"]["target"]
            if target and _actor_name(target, character) not in names:
                add("story.actors", f"Add the relationship's target ({target}) to this linked scene, or choose a different relationship.")
    return issues


def story_issues(character):
    """Export-level checks: incomplete drafts warn; opted-in invalid scenes block."""
    issues = []
    if not isinstance(character, dict):
        return [{"level": "error", "field": "events", "message": "Character data must be an object."}]
    collections = {}
    for key in ("events", "relationships"):
        records = character.get(key, [])
        if not isinstance(records, list) or len(records) > 100:
            issues.append({"level": "error", "field": key, "message": "Use a list with up to 100 story records."})
            collections[key] = []
        else:
            collections[key] = records
    safe_character = {**character, **collections}
    for key, records in collections.items():
        seen = set()
        for index, record in enumerate(records):
            prefix = f"{key}.{index}"
            structural = structure_issues(record, key)
            if structural:
                issues.extend({**item, "field": prefix + ("." + item["field"] if item["field"] else "")} for item in structural)
                continue
            record_id = record.get("id")
            if record_id is not None and record_id in seen:
                issues.append({"level": "error", "field": prefix + ".id", "message": "Story IDs must be unique within each collection."})
            if record_id is not None:
                seen.add(record_id)
            story = record.get("story", {})
            ready = story.get("stage") == "ready"
            if key == "events":
                local = event_issues(record, safe_character)
                if not ready:
                    remaining = sum(item["level"] == "error" for item in local)
                    detail = f" Rehearsal has {remaining} readiness {'issue' if remaining == 1 else 'issues'} to resolve." if remaining else " Mark it ready when you want to include it."
                    issues.append({"level": "warning", "field": prefix, "message": f"{record.get('name') or 'Untitled event'} is a story draft; it stays in the project and is not playable yet." + detail})
                else:
                    issues.extend({**item, "field": prefix + ("." + item["field"] if item["field"] else "")} for item in local)
                    if not any(item["level"] == "error" for item in local):
                        issues.append({"level": "success", "field": prefix, "message": f"{record.get('name') or 'Event'} is ready to export as a playable scene."})
            else:
                relation = normalize_relationship(record)
                target = relation["story"]["target"]
                local = []
                if not relation["name"].strip():
                    local.append(("name", "Name this relationship arc."))
                if not _IDENTIFIER.fullmatch(target) or target in (character.get("internal_name"), exported_npc_id(character)):
                    local.append(("story.target", "Choose farmer or another NPC's exact internal ID as this relationship's target."))
                linked = [item for item in collections["events"] if isinstance(item, dict) and isinstance(item.get("story"), dict)
                          and item["story"].get("relationship_id") == relation["id"]]
                if ready:
                    if not linked:
                        local.append(("story.stage", "Develop at least one linked event into a ready scene to make this relationship playable."))
                    elif any(item["story"].get("stage") != "ready" for item in linked):
                        local.append(("story.stage", "Mark every linked event ready before marking the complete relationship arc ready."))
                    elif any(any(issue["level"] == "error" for issue in event_issues(item, safe_character)) for item in linked):
                        local.append(("story.stage", "Resolve the linked scenes' readiness issues before marking this relationship arc ready."))
                if ready:
                    issues.extend({"level": "error", "field": prefix + "." + field, "message": message}
                                  for field, message in local)
                else:
                    issues.append({"level": "warning", "field": prefix, "message": "Relationship notes stay in the project; their ready linked events carry the relationship into the game."})
    return issues


def compile_story(character, *, npc_id=None):
    """Return Content Patcher EditData patches for ready scenes, or fail safely."""
    issues = story_issues(character)
    if any(item["level"] == "error" for item in issues):
        raise StoryValidationError(issues)
    npc_id = npc_id or exported_npc_id(character)
    if not isinstance(npc_id, str) or not _IDENTIFIER.fullmatch(npc_id):
        raise StoryValidationError([{"level": "error", "field": "internal_name", "message": "Use a valid NPC internal ID."}])
    groups = {}
    by_id = {item["id"]: item for item in character.get("events", []) if isinstance(item, dict) and "id" in item}
    for raw in character.get("events", []):
        if raw.get("story", {}).get("stage") != "ready":
            continue
        event = normalize_event(raw)
        story = event["story"]
        conditions = [f"Friendship {npc_id} {event['hearts'] * 250}", f"Time {story['time_start']} {story['time_end']}"]
        if story["season"] != "any":
            conditions.append("Season " + story["season"])
        if story["weather"] != "any":
            conditions.append("Weather " + story["weather"])
        if story["relationship"] == "unmarried":
            conditions.append(f"GameStateQuery !PLAYER_NPC_RELATIONSHIP Current {npc_id} Married")
        elif story["relationship"] in ("dating", "married"):
            conditions.append(f"GameStateQuery PLAYER_NPC_RELATIONSHIP Current {npc_id} {story['relationship'].title()}")
        if story["min_house_upgrade"]:
            conditions.append(f"GameStateQuery PLAYER_FARMHOUSE_UPGRADE Current {story['min_house_upgrade']}")
        if story["previous_event_id"]:
            conditions.append("SawEvent " + event_game_id(by_id[story["previous_event_id"]], character))
        key = event_game_id(event, character) + "/" + "/".join(conditions)
        first = next(actor for actor in story["actors"] if _actor_name(actor["name"], character, npc_id) == npc_id)
        actors = " ".join(f"{_actor_name(actor['name'], character, npc_id)} {actor['x']} {actor['y']} {actor['facing']}" for actor in story["actors"])
        commands = [story["music"], f"{first['x']} {first['y']}", actors]
        pending_effects = [f"AddFriendshipPoints {_actor_name(beat['actor'], character, npc_id)} {beat['amount']}"
                           for beat in story["beats"] if beat["kind"] == "friendship"]
        if pending_effects:
            commands.append("setSkipActions " + "#".join(pending_effects))
        commands.append("skippable")
        for beat in story["beats"]:
            kind = beat["kind"]
            actor = _actor_name(beat["actor"], character, npc_id)
            if kind == "dialogue":
                command = "message" if actor == "farmer" else "speak " + actor
                commands.append(command + ' "' + beat["text"].replace("\r\n", "\n").replace("\r", "\n").replace("\n", "#$b#").replace("\t", " ") + '"')
            elif kind == "emote":
                commands.append(f"emote {actor} {beat['emote']}")
            elif kind == "move":
                commands.append(f"move {actor} {beat['x']} {beat['y']} {beat['facing']}")
            elif kind == "pause":
                commands.append(f"pause {beat['duration']}")
            elif kind == "friendship":
                pending_effects.pop(0)
                # Updating the skip actions on the same tick as the effect
                # prevents a one-frame window where skipping awards it twice.
                commands.extend(["beginSimultaneousCommand", f"friendship {actor} {beat['amount']}",
                                 ("setSkipActions " + "#".join(pending_effects)).rstrip(), "endSimultaneousCommand"])
            elif kind == "choice":
                # A bounded, real binary fork. Fork targets contain commands
                # without the normal music/viewport/cast header, per the game
                # event format. Both paths close the original scene.
                branch_suffix = hashlib.sha256(beat["id"].encode()).hexdigest()[:12]
                branch_id = event_game_id(event, character) + "_choice_" + branch_suffix
                first_option, second_option = beat["choices"]
                question = "#".join((beat["text"], first_option["label"], second_option["label"]))
                commands.extend([f'question fork0 "{question}"', "fork " + branch_id])
                commands.extend(_choice_outcome(second_option, actor))
                groups.setdefault(event["location"], {})[branch_id] = "/".join(_choice_outcome(first_option, actor) + ["end"])
        commands.append("end")
        groups.setdefault(event["location"], {})[key] = "/".join(commands)
    return [{"Action": "EditData", "Target": "Data/Events/" + location, "Entries": entries}
            for location, entries in groups.items()]


def _choice_outcome(option, actor):
    amount = option.get("friendship", 0)
    commands = [f"setSkipActions AddFriendshipPoints {actor} {amount}" if amount else "setSkipActions"]
    speech = option["text"].replace("\r\n", "\n").replace("\r", "\n").replace("\n", "#$b#").replace("\t", " ")
    commands.append(f'speak {actor} "{speech}"')
    if amount:
        commands.extend(["beginSimultaneousCommand", f"friendship {actor} {amount}", "setSkipActions", "endSimultaneousCommand"])
    return commands


def story_repeat_events(character, npc_id=None, *, mod_id=None):
    """Concrete IDs for Event Repeater's un-tokenized content.json reader.

    Event Repeater 6.5.8 reads List<string> RepeatEvents directly from packs;
    unlike a Content Patcher Entries key, {{ModId}} is not expanded there.
    https://github.com/MissCoriel/Event-Repeater/blob/master/ThingstoForget.cs
    https://github.com/MissCoriel/Event-Repeater/blob/master/ModEntry.cs
    """
    issues = story_issues(character)
    if any(issue["level"] == "error" for issue in issues):
        raise StoryValidationError(issues)
    repeatable = [event for event in character.get("events", [])
                  if event.get("story", {}).get("stage") == "ready" and event.get("story", {}).get("repeat", "once") == "daily"]
    if not repeatable:
        return []
    if mod_id is None:
        npc_id = npc_id or exported_npc_id(character)
        suffix = "_" + str(character.get("internal_name", "")).strip()
        if not isinstance(npc_id, str) or not npc_id.endswith(suffix):
            raise ValueError("Supply the pack's mod_id when overriding the NPC identifier.")
        mod_id = npc_id[:-len(suffix)]
    if not isinstance(mod_id, str) or not _IDENTIFIER.fullmatch(mod_id):
        raise ValueError("Use a valid content-pack ID.")
    return [event_game_id(event, character).replace("{{ModId}}", mod_id) for event in repeatable]
