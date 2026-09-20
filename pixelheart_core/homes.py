"""Home assignment, exact route migration, and actionable home diagnostics.

The NPC's Home data describes its default spawn, not a bed, a spouse room, or
an automatic schedule rewrite. Maps stay in the existing portable world model.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET

from .validation import validate_draft
from .world import WorldError, _layer_gids, asset_path, exported_location_id, map_bundle, new_location, relative_path


HOME_FACINGS = ("up", "right", "down", "left")


def _stops(character):
    schedule = character.get("schedule", [])
    for index, stop in enumerate(schedule if isinstance(schedule, list) else []):
        if isinstance(stop, dict):
            yield f"schedule.{index}", stop
    life = character.get("life", {})
    routines = life.get("routines", []) if isinstance(life, dict) else []
    for index, routine in enumerate(routines if isinstance(routines, list) else []):
        stops = routine.get("stops", []) if isinstance(routine, dict) else []
        for number, stop in enumerate(stops if isinstance(stops, list) else []):
            if isinstance(stop, dict):
                yield f"life.routines.{index}.stops.{number}", stop


def _at_home(stop, character):
    return (stop.get("location") != "bed"
            and stop.get("location") == character.get("home_map")
            and type(stop.get("x")) is int and type(stop.get("y")) is int
            and type(character.get("home_x")) is int and type(character.get("home_y")) is int
            and stop["x"] == character["home_x"] and stop["y"] == character["home_y"])


def matching_home_stops(character):
    """Return field paths for authored stops at the current exact home tile."""
    return [path for path, stop in _stops(character) if _at_home(stop, character)]


def assign_home(character, home_map, x, y, facing="down", *, move_route_stops=False):
    """Return an independent draft; move exact old-home stops only on request.

    Route IDs, time, facing, activity and all extension metadata stay intact.
    A route's own facing is deliberate and independent of the spawn facing.
    """
    fields = validate_draft({"home_map": home_map, "home_x": x,
                             "home_y": y, "home_facing": facing})
    result = copy.deepcopy(character)
    if move_route_stops:
        for _, stop in _stops(result):
            if _at_home(stop, character):
                stop.update(location=fields["home_map"], x=x, y=y)
    result.update(fields)
    return result


def new_home_location(character, world=None):
    """Make a unique 12×12 interior draft matching the home painter preset."""
    locations = world.get("locations", []) if isinstance(world, dict) else []
    used = {str(item.get("internal_name", "")).casefold()
            for item in locations if isinstance(item, dict)}
    from .locations import VANILLA_LOCATIONS
    used.update(item.id.casefold() for item in VANILLA_LOCATIONS)
    stem = re.sub(r"[^A-Za-z0-9_]", "", str(character.get("internal_name") or "Character"))
    if not stem or not stem[0].isalpha():
        stem = "Character" + stem
    base = stem[:36] + "Home"
    internal, number = base, 2
    while internal.casefold() in used:
        suffix = str(number)
        internal = base[:40 - len(suffix)] + suffix
        number += 1
    display = str(character.get("name") or "Character").strip() or "Character"
    result = {**new_location(), "name": (display + "’s home")[:80], "internal_name": internal,
              "entry_x": 6, "entry_y": 10, "exit_x": 6, "exit_y": 11}
    occupied = set()
    for item in locations:
        entrance = item.get("entrance", {}) if isinstance(item, dict) else {}
        if isinstance(entrance, dict) and entrance.get("map") == "Town" and not item.get("spouse_room"):
            for x, y in (("x", "y"), ("arrival_x", "arrival_y")):
                if type(entrance.get(x)) is int and type(entrance.get(y)) is int:
                    occupied.add((entrance[x], entrance[y]))
    # Suggested coordinates need in-game verification, just like new_location's
    # existing Town default. Avoid creating a known collision with another draft.
    column = next(x for x in range(32, 1001) if (x, 62) not in occupied and (x, 63) not in occupied)
    result["entrance"].update(x=column, arrival_x=column)
    return result


def _home_tile_obstacles(xml, bundle, tile):
    """Conservative source-map hints, not a replacement for game pathfinding."""
    width, height = bundle["width"], bundle["height"]
    if not (0 <= tile[0] < width and 0 <= tile[1] < height):
        return []
    tilesets = []
    for declaration in xml.findall("tileset"):
        tileset = declaration
        if declaration.get("source"):
            tileset = ET.fromstring(bundle["files"][relative_path(declaration.get("source")).as_posix()])
        tilesets.append((int(declaration.get("firstgid", "1")), tileset))
    tilesets.sort(key=lambda item: item[0])
    result = []
    for layer in xml.findall("layer"):
        name = layer.get("name")
        if name not in ("Back", "Buildings"):
            continue
        if any(float(layer.get(key, "0")) != 0 for key in ("offsetx", "offsety", "x", "y")):
            continue
        gid = _layer_gids(layer, width, height)[tile[1] * width + tile[0]] & 0x0FFFFFFF
        properties = set()
        selected = next((item for item in reversed(tilesets) if item[0] <= gid), None) if gid else None
        if selected:
            first, tileset = selected
            cell = next((item for item in tileset.findall("tile") if item.get("id") == str(gid - first)), None)
            if cell is not None:
                properties = {item.get("name") for item in cell.findall("properties/property")}
        if name == "Back" and not gid:
            result.append("The home tile has no floor on the Back layer. Paint a floor or choose a clear tile inside the room.")
        elif name == "Back" and "NoPath" in properties:
            result.append("The home tile is marked NoPath, which excludes NPC routes. Choose another tile or remove that property in Tiled.")
        elif name == "Buildings" and gid and not properties & {"Passable", "NPCPassable"}:
            result.append("The home tile contains a Buildings tile that may block the NPC. Choose an open floor tile and check movement in-game.")
    return result


def home_issues(character, world=None, project_root=None, primary=None):
    """Inspect home authoring without preventing a mapless draft from saving.

    Missing map assets are warnings here; world export validation already blocks
    missing assets. Unknown external maps remain supported without guessed data.
    """
    issues = []

    def add(level, field, message, code):
        issues.append({"level": level, "field": field, "message": message, "code": code})

    # Export historically accepts numeric strings and tiles through 4095,
    # while editable project drafts use integer controls through 1000. Keep
    # this shared inspector compatible with both input boundaries.
    home_map = character.get("home_map")
    if not isinstance(home_map, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", home_map.strip()):
        add("error", "home_map", "Enter a home map's internal name, such as Town or BusStop.", "home_invalid")
    coordinates = []
    for field in ("home_x", "home_y"):
        value = character.get(field)
        try:
            if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
                raise ValueError
            coordinate = int(value)
            if not 0 <= coordinate <= 4095:
                raise ValueError
        except (TypeError, ValueError):
            add("error", field, "Home tile coordinates must be whole numbers from 0 to 4095.", "home_invalid")
        else:
            coordinates.append(coordinate)
    facing = character.get("home_facing", "down")
    if not isinstance(facing, str) or facing not in HOME_FACINGS:
        add("error", "home_facing", "Choose up, right, down, or left for the home direction.", "home_invalid")
    if issues:
        return issues

    home_map = home_map.strip()
    tile = tuple(coordinates)
    primary = primary or character
    locations = world.get("locations", []) if isinstance(world, dict) else []
    locations = [item for item in locations if isinstance(item, dict) and isinstance(item.get("internal_name"), str)]
    location = next((item for item in locations
                     if home_map in (item["internal_name"], exported_location_id(item, primary))), None)
    home_names = {home_map}
    if location:
        home_names.update((location["internal_name"], exported_location_id(location, primary)))
        if location.get("spouse_room"):
            add("error", "home_map", "A spouse-room section belongs inside the farmhouse. Choose a standalone place for this home.", "home_spouse_room")
            return issues
        triggers = {(location.get("exit_x"), location.get("exit_y"))}
        for other in locations:
            entrance = other.get("entrance", {})
            if not other.get("spouse_room") and isinstance(entrance, dict) and entrance.get("map") in home_names:
                triggers.add((entrance.get("x"), entrance.get("y")))
        if not location.get("map") or project_root is None:
            add("warning", "home_map", "Design or import this home's map before exporting. You can save the home assignment as a draft.", "home_map_missing")
        else:
            try:
                bundle = map_bundle(asset_path(location["map"], project_root))
                width, height = bundle["width"], bundle["height"]
                if not (0 <= tile[0] < width and 0 <= tile[1] < height):
                    add("error", "home_x", f"The home tile is outside this {width}×{height} map. Choose X 0–{width - 1} and Y 0–{height - 1}.", "home_out_of_bounds")
                xml = ET.fromstring(bundle["files"][bundle["entry"]])
                properties = {item.get("name"): item.get("value", item.text or "")
                              for item in xml.findall("properties/property")}
                if "Outdoors" in properties:
                    add("warning", "home_map", "This map has an Outdoors property. Verify its indoor or outdoor behavior in-game for the home you intend.", "home_outdoors")
                try:
                    for message in _home_tile_obstacles(xml, bundle, tile):
                        add("warning", "home_x", message, "home_tile_obstacle")
                except (WorldError, KeyError, TypeError, ValueError, ET.ParseError):
                    # Rich or unfamiliar maps remain exportable without a
                    # guessed collision verdict from this small inspector.
                    pass
                for key in ("Warp", "NPCWarp"):
                    words = properties.get(key, "").split()
                    for offset in range(0, len(words) - 4, 5):
                        try:
                            triggers.add((int(words[offset]), int(words[offset + 1])))
                        except ValueError:
                            continue
            except (WorldError, OSError, ValueError, ET.ParseError) as exc:
                add("warning", "home_map", "This home's map cannot be inspected: " + str(exc), "home_map_missing")
        if tile in triggers:
            add("warning", "home_x", "The home tile is on a door or exit warp. Place the NPC on a clear tile inside the room so they can remain at home.", "home_on_warp")

    schedule = character.get("schedule", [])
    if isinstance(schedule, list) and schedule and isinstance(schedule[-1], dict):
        last = schedule[-1]
        if isinstance(last.get("location"), str) and last["location"] not in home_names | {"bed"}:
            add("warning", "schedule", "The daily route ends away from this home. Home assignment changes the morning spawn; add or move a final route stop to bring them home at night.", "home_route_away")
    return issues
