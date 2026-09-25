"""Find and migrate authored place references without changing live editor data.

Only fields which identify maps are considered. Notes, dialogue text, artwork
paths, actor names, and other extension metadata are never rewritten.
"""
from copy import deepcopy
import re

from .locations import LOCATIONS_BY_ID
from .world import WorldError, exported_location_id


def _rows(value):
    return enumerate(value) if isinstance(value, list) else ()


def _character_references(character, prefix=(), *, include_home=True):
    """Yield (path, description, value), including disabled authored drafts."""
    if not isinstance(character, dict):
        return
    name = character.get("name") or "The NPC"
    if include_home:
        yield prefix + ("home_map",), f"{name}'s home position", character.get("home_map")
    for index, stop in _rows(character.get("schedule")):
        if isinstance(stop, dict):
            time = stop.get("time") or f"stop {index + 1}"
            yield prefix + ("schedule", index, "location"), f"{name}'s daily route at {time}", stop.get("location")
    for index, event in _rows(character.get("events")):
        if isinstance(event, dict):
            title = event.get("name") or f"scene {index + 1}"
            yield prefix + ("events", index, "location"), f"{name}'s scene “{title}”", event.get("location")
    life = character.get("life")
    if not isinstance(life, dict):
        return
    for kind, label in (("routines", "routine"), ("dialogues", "story reaction"),
                        ("spouse_dialogue", "marriage dialogue")):
        for index, row in _rows(life.get(kind)):
            if not isinstance(row, dict):
                continue
            title = row.get("name") or f"{label} {index + 1}"
            path = prefix + ("life", kind, index)
            if kind == "routines":
                for number, stop in _rows(row.get("stops")):
                    if isinstance(stop, dict):
                        yield (path + ("stops", number, "location"),
                               f"{name}'s routine “{title}”, stop {number + 1}", stop.get("location"))
            # The current editor has no location condition. Preserve imported
            # extensions with explicit scalar map/location fields safely;
            # after_event_id and arbitrary condition text are not map IDs.
            conditions = row.get("conditions")
            if isinstance(conditions, dict):
                for key in ("map", "location"):
                    yield (path + ("conditions", key),
                           f"{name}'s {label} “{title}”, location condition", conditions.get(key))


def _references(place, character, world, *, include_primary_home, include_own_entrance):
    aliases = {place["internal_name"], exported_location_id(place, character)}
    candidates = list(_character_references(character, include_home=include_primary_home))
    for index, bundled in _rows(world.get("characters")):
        if isinstance(bundled, dict):
            candidates.extend(_character_references(bundled.get("character"), ("world", "characters", index, "character")))
    for index, location in _rows(world.get("locations")):
        if not isinstance(location, dict):
            continue
        if not include_own_entrance and location.get("id") == place.get("id"):
            continue
        entrance = location.get("entrance")
        if isinstance(entrance, dict):
            name = location.get("name") or f"place {index + 1}"
            candidates.append((("world", "locations", index, "entrance", "map"),
                               f"“{name}” entrance and return destination", entrance.get("map")))
    for path, description, value in candidates:
        if isinstance(value, str) and value in aliases:
            yield path, description, value


def place_references(place, character, world, *, include_primary_home=True, include_own_entrance=True):
    """Return exact short/exported map-ID usages as field/description/value dicts.

    ``character`` must be the current main character, including unsaved editor
    values; ``world`` supplies bundled characters and connected places. Fields
    use the project's validation paths (e.g. ``schedule.0.location`` or
    ``world.characters.0.character.home_map``). Inputs are never mutated.
    """
    return [{"field": ".".join(map(str, path)), "description": description, "value": value}
            for path, description, value in _references(
                place, character, world, include_primary_home=include_primary_home,
                include_own_entrance=include_own_entrance)]


def place_removal_blockers(place, character, world):
    """Return references the user must move before removing this place.

    The main home position is excluded because the caller restores its fallback
    home; bundled NPC homes remain blockers. The removed place's own entrance
    cannot outlive it, so it is excluded too. Draft scenes and rules count.
    """
    return place_references(place, character, world, include_primary_home=False, include_own_entrance=False)


def rename_place(character, world, place_id, internal_name):
    """Return ``(character_copy, world_copy)`` with a place and its usages renamed.

    Both short and exported aliases retain their form. The new ID must be legal,
    non-vanilla, and unique (case-insensitively). References to an ambiguous
    existing ID are rejected rather than guessed; unused duplicate draft IDs
    can still be repaired. Validation and
    migration complete on copies before either result can be applied by a GUI.
    Unknown metadata is preserved, and invalid requests leave inputs untouched.
    """
    if not isinstance(internal_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", internal_name):
        raise WorldError("Use a map ID starting with a letter, followed by up to 39 letters, digits, or underscores.")
    if internal_name.casefold() in {name.casefold() for name in LOCATIONS_BY_ID}:
        raise WorldError("Use a new place ID instead of an existing vanilla map name.")
    locations = world.get("locations", [])
    matches = [(index, place) for index, place in _rows(locations)
               if isinstance(place, dict) and place.get("id") == place_id]
    if len(matches) != 1:
        raise WorldError("Choose an existing place with a unique record ID before renaming it.")
    index, place = matches[0]
    old_name = place["internal_name"]
    references = list(_references(place, character, world, include_primary_home=True, include_own_entrance=True))
    for other_index, other in _rows(locations):
        if other_index == index or not isinstance(other, dict):
            continue
        other_name = str(other.get("internal_name", "")).casefold()
        if other_name == old_name.casefold() and references:
            raise WorldError("This place shares its map ID with another place. Move references to a different location before renaming it.")
        if other_name == internal_name.casefold():
            raise WorldError("Another place already uses this map ID. Choose a different ID.")
    updated_character, updated_world = deepcopy(character), deepcopy(world)
    updated_place = updated_world["locations"][index]
    updated_place["internal_name"] = internal_name
    aliases = {old_name: internal_name,
               exported_location_id(place, character): exported_location_id(updated_place, updated_character)}
    for path, _description, value in references:
        target = updated_world if path[0] == "world" else updated_character
        relative = path[1:] if path[0] == "world" else path
        for key in relative[:-1]:
            target = target[key]
        target[relative[-1]] = aliases[value]
    return updated_character, updated_world
