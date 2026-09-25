"""Independent interior authoring model; map structure and live items stay separate.

Coordinates are 16-pixel map tiles. Rooms describe floor regions; a three-tile
north wall and one-tile perimeter are derived from their union. Preview artwork
belongs to the author and is never used as a substitute for an in-game item.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
import hashlib
import io
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

from .world import WorldError, asset_path, _write_new_file


class InteriorError(WorldError):
    """An interior edit cannot be applied without losing or invalidating data."""


def new_interior(kind="residence"):
    if kind not in ("residence", "spouse"):
        raise InteriorError("Choose a residence interior or spouse room.")
    spouse = kind == "spouse"
    return {"version": 1, "kind": kind, "width": 6 if spouse else 24,
            "height": 9 if spouse else 20,
            "rooms": [{"id": "main", "name": "Main room", "x": 0 if spouse else 2,
                       "y": 3 if spouse else 5, "width": 6 if spouse else 10,
                       "height": 6 if spouse else 8, "optional": False, "enabled": True}],
            "entry": [3, 8] if spouse else [4, 10], "spouse_stand": [3, 5],
            "atlas": {"asset": "", "columns": 0, "tile_count": 0},
            "style": {"floor": 0, "wall_top": 0, "wall_middle": 0, "wall_bottom": 0},
            "animations": [], "catalog": [], "furniture": []}


def _integer(value, low, high, message):
    if type(value) is not int or not low <= value <= high:
        raise InteriorError(message)
    return value


def _text(value, limit, message, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise InteriorError(message)
    return value


def room_cells(room):
    return {(x, y) for y in range(room["y"], room["y"] + room["height"])
            for x in range(room["x"], room["x"] + room["width"])}


def floor_cells(data, enabled=None):
    return set().union(*(room_cells(room) for room in data["rooms"]
                         if (room["enabled"] if enabled is None else room["id"] in enabled)))


def spouse_entrance_tiles(data):
    """Floor cells adjoining the farmhouse's open west side of a spouse insert.

    The farmhouse pillar blocks the first floor row at (0, 3). Rows 4–8
    connect to the bedroom; the stored ``entry`` is legacy authoring metadata.
    """
    if data["kind"] != "spouse":
        return set()
    return {(0, y) for y in range(4, 9)} & floor_cells(data)


def doorway_exit(data):
    """The exterior passage tile triggers the return warp, beyond the room floor."""
    doorway = data.get("doorway")
    return (doorway[0], doorway[1] + 1) if doorway is not None else None


def _doorway_site(data, floor, envelope, x, y):
    return ({(x, y), (x-1, y), (x+1, y), (x, y-1)} <= floor
            and not {(x-1, y+1), (x, y+1), (x+1, y+1)} & envelope
            and y+1 < data["height"])


def _validate_doorway_geometry(data, enabled=None):
    if "doorway" not in data:
        return
    if data["kind"] != "residence":
        raise InteriorError("A spouse room uses the farmhouse entrance, not a separate doorway.")
    doorway = data["doorway"]
    if not isinstance(doorway, list) or len(doorway) != 2:
        raise InteriorError("The doorway needs two tile coordinates.")
    for coordinate in doorway:
        _integer(coordinate, 0, 95, "Doorway positions use whole tile coordinates.")
    x, y = doorway
    floor = floor_cells(data, enabled)
    if not _doorway_site(data, floor, floor | wall_cells(data, enabled), x, y):
        raise InteriorError("Place the doorway on a clear bottom edge, away from corners, with space outside.")


def place_doorway(data, x, y):
    """Place a doorway and its arrival one tile inward as one atomic edit."""
    for coordinate in (x, y):
        _integer(coordinate, 0, 95, "Doorway positions use whole tile coordinates.")
    candidate = deepcopy(data)
    candidate["doorway"] = [x, y]
    candidate["entry"] = [x, y-1]
    candidate = normalize_interior(candidate)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def ensure_doorway(data):
    """Suggest an opening for an older residence without moving its arrival."""
    candidate = normalize_interior(data)
    if candidate["kind"] != "residence" or "doorway" in candidate:
        return candidate
    entry_x, entry_y = candidate["entry"]
    floor = floor_cells(candidate)
    envelope = floor | wall_cells(candidate)
    reachable = reachable_tiles(candidate)
    sites = [(x, y) for x, y in floor if _doorway_site(candidate, floor, envelope, x, y)
             and {(x, y), (x, y-1)} <= reachable]
    for x, y in sorted(sites, key=lambda p: (abs(p[0]-entry_x)+abs(p[1]-entry_y), p[1], p[0])):
        proposed = deepcopy(candidate)
        proposed["doorway"] = [x, y]
        try:
            return normalize_interior(proposed)
        except InteriorError:
            continue
    return candidate


def footprint(definition, rotation=0):
    size = definition.get("rotation_footprints", {}).get(str(rotation), definition.get("footprint"))
    if not size:
        raise InteriorError("Set the furniture's footprint before placing it.")
    return tuple(size)


WALL_FURNITURE = {"painting", "window", "sconce"}


def wall_cells(data, enabled=None):
    from .interior_layout import shell_wall_regions
    walls, _ = shell_wall_regions(data, enabled)
    return set().union(*walls.values())


def placement_cells(item, definition):
    width, height = footprint(definition, item["rotation"])
    return {(x, y) for y in range(item["y"], item["y"] + height)
            for x in range(item["x"], item["x"] + width)}


def _room_regions(data):
    """Attribute the visible floor and north wall to their authored rooms."""
    floor = floor_cells(data)
    regions = {}
    for room in data["rooms"]:
        cells = room_cells(room) if room["enabled"] else set()
        walls = {(x, y-distance) for x, y in cells if (x, y-1) not in floor
                 for distance in (1, 2, 3)
                 if y-distance >= 0 and (x, y-distance) not in floor}
        regions[room["id"]] = {"floor": cells, "wall": walls}
    return regions


def _placement_rooms(item, definition, regions):
    cells = placement_cells(item, definition)
    surface = "wall" if definition["kind"] in WALL_FURNITURE else "floor"
    owners = {identity for identity, region in regions.items() if cells & region[surface]}
    if len(owners) == 1 and cells <= regions[next(iter(owners))][surface]:
        return owners, next(iter(owners))
    return owners, None


def validate_furniture_placement(data, item, definition=None):
    """Check placement-only rules without making older saved rooms unreadable.

    Windows hang from the upper edge of a wall. Their artwork can extend above
    the collision footprint, so compare the visible top with the actual wall
    geometry rather than a fixed canvas row or the footprint's top alone.
    """
    if definition is None:
        definition = next(entry for entry in data["catalog"] if entry["id"] == item["item_id"])
    window = definition["kind"] == "window" or (
        definition["kind"] == "painting" and (
            definition["id"] == "(F)1630" or "window" in definition["name"].casefold()))
    if not window:
        return
    width, height = footprint(definition, item["rotation"])
    frames = [frame for frame in definition.get("frames", [])
              if frame["rotation"] == item["rotation"]]
    if frames:
        sprite_width, sprite_height = frames[0]["rect"][2:]
        dx, dy = frames[0].get("offset", (0, 0))
    else:
        sprite_width, sprite_height = (part * 16 for part in (definition.get("sprite_size") or (width, height)))
        dx, dy = 0, 0
    top_pixels = (item["y"] + height) * 16 - sprite_height + dy
    top = top_pixels // 16
    left = min(item["x"], (item["x"] * 16 + dx) // 16)
    right = max(item["x"] + width, (item["x"] * 16 + dx + sprite_width + 15) // 16)
    walls = wall_cells(data)
    if top_pixels % 16 or any((x, top) not in walls or (x, top - 1) in walls
                              for x in range(left, right)):
        raise InteriorError("Place windows along the top of the wall, above the lower wall trim.")


def _layout_translation(before, after):
    """Recognize a canvas rebase which preserves all authored relative positions."""
    rooms = {room["id"]: room for room in after["rooms"]}
    first = before["rooms"][0]
    if first["id"] not in rooms:
        return 0, 0
    dx, dy = rooms[first["id"]]["x"] - first["x"], rooms[first["id"]]["y"] - first["y"]
    if not (dx or dy):
        return 0, 0
    for room in before["rooms"]:
        moved = rooms.get(room["id"])
        if (moved is None or (moved["x"], moved["y"]) != (room["x"] + dx, room["y"] + dy)
                or any(moved[key] != room[key] for key in ("width", "height", "enabled"))):
            return 0, 0
    placements = {item["id"]: item for item in after["furniture"]}
    for item in before["furniture"]:
        moved = placements.get(item["id"])
        if (moved is None or (moved["x"], moved["y"]) != (item["x"] + dx, item["y"] + dy)
                or any(moved[key] != item[key] for key in ("item_id", "rotation"))):
            return 0, 0
    if any(after.get(key) != [before[key][0] + dx, before[key][1] + dy]
           for key in ("entry", "spouse_stand", "doorway") if key in before):
        return 0, 0
    return dx, dy


def _carried_room_items(before, after):
    """Find placements whose position relative to their own room is unchanged.

    Moving an entire room must not apply newer window-height rules to older
    saved furnishings. Standalone item edits still use those placement rules.
    """
    old_regions, new_regions = _room_regions(before), _room_regions(after)
    old_rooms = {room["id"]: room for room in before["rooms"]}
    new_rooms = {room["id"]: room for room in after["rooms"]}
    old_items = {item["id"]: item for item in before["furniture"]}
    old_definitions = {item["id"]: item for item in before["catalog"]}
    new_definitions = {item["id"]: item for item in after["catalog"]}
    carried = set()
    for item in after["furniture"]:
        previous = old_items.get(item["id"])
        if previous is None or any(item[key] != previous[key] for key in ("item_id", "rotation")):
            continue
        _, owner = _placement_rooms(previous, old_definitions[previous["item_id"]], old_regions)
        _, new_owner = _placement_rooms(item, new_definitions[item["item_id"]], new_regions)
        if owner is None or new_owner != owner:
            continue
        old_room, new_room = old_rooms[owner], new_rooms[owner]
        if any(old_room[key] != new_room[key] for key in ("width", "height", "enabled")):
            continue
        if all(item[key] - new_room[key] == previous[key] - old_room[key] for key in ("x", "y")):
            carried.add(item["id"])
    return carried


def _connected(cells):
    if not cells:
        return False
    seen = {next(iter(cells))}
    queue = deque(seen)
    while queue:
        x, y = queue.popleft()
        for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if point in cells and point not in seen:
                seen.add(point)
                queue.append(point)
    return seen == cells


def _walkable_tiles(data):
    """The floor left clear by structural pieces and solid furniture."""
    definitions = {item["id"]: item for item in data["catalog"]}
    from .interior_layout import partition_cells
    from .interior_architecture import architecture_cells
    cells = floor_cells(data) - partition_cells(data) - architecture_cells(data)
    if "doorway" in data:
        cells.add(doorway_exit(data))
    for item in data["furniture"]:
        definition = definitions[item["item_id"]]
        if definition["kind"] != "rug":
            cells.difference_update(placement_cells(item, definition))
    return cells


def reachable_tiles(data):
    """Walkable floor reached from a residence entry or the farmhouse opening."""
    cells = _walkable_tiles(data)
    entrances = spouse_entrance_tiles(data) if data["kind"] == "spouse" else {tuple(data["entry"])}
    seen = entrances & cells
    queue = deque(seen)
    while queue:
        x, y = queue.popleft()
        for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if point in cells and point not in seen:
                seen.add(point)
                queue.append(point)
    return seen


def spouse_access_issues(data):
    """Report a blocked farmhouse approach without making old projects unreadable."""
    if data["kind"] != "spouse" or tuple(data["spouse_stand"]) in reachable_tiles(data):
        return []
    return [{"level": "error", "field": "interior.spouse_stand",
             "message": "Keep a walkable route from the farmhouse opening on the left to the spouse standing position."}]


def validate_spouse_access(data, before=None):
    """Reject blocked access; allow unchanged or incremental repairs in old drafts.

    Loading retains older rooms that passed the former bottom-entry check.
    During repairs, removing blockers or changing finishes is safe even when
    another blocker remains. New blockers and a moved stand must leave a route.
    Saving a finished design or exporting calls this without ``before``.
    """
    issues = spouse_access_issues(data)
    if not issues:
        return
    if (before is not None and before["kind"] == "spouse"
            and before["spouse_stand"] == data["spouse_stand"]
            and spouse_access_issues(before)
            and _walkable_tiles(before) <= _walkable_tiles(data)):
        return
    raise InteriorError(issues[0]["message"])


def normalize_interior(value):
    """Validate bounded, portable authoring data without resolving local assets."""
    from .interior_furniture import validate_definition, validate_held_item, FurnitureValidationError
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise InteriorError("This interior design version is not supported.")
    data = deepcopy(value)
    if data.get("kind") not in ("residence", "spouse"):
        raise InteriorError("Choose a residence interior or spouse room.")
    for key in ("width", "height"):
        _integer(data.get(key), 6, 96, "Interior dimensions must be 6–96 tiles.")
    if data["kind"] == "spouse" and (data["width"], data["height"]) != (6, 9):
        raise InteriorError("A spouse room is exactly 6 × 9 tiles.")
    rooms = data.get("rooms")
    if not isinstance(rooms, list) or not 1 <= len(rooms) <= 16:
        raise InteriorError("An interior needs 1–16 rooms.")
    seen, occupied, optional = set(), set(), 0
    for room in rooms:
        if not isinstance(room, dict):
            raise InteriorError("Each room must be an object.")
        identity = _text(room.get("id"), 80, "Rooms need unique short IDs.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", identity) or identity in seen:
            raise InteriorError("Rooms need unique IDs using letters, numbers, dots or underscores.")
        seen.add(identity)
        _text(room.get("name"), 80, "Give each room a short name.")
        for key in ("x", "y", "width", "height"):
            _integer(room.get(key), 1 if key in ("width", "height") else 0, 96,
                     "Room rectangles use whole tile coordinates.")
        if room["y"] < 3 or room["x"] + room["width"] > data["width"] or room["y"] + room["height"] > data["height"]:
            raise InteriorError("Keep rooms inside the canvas, with three rows above for walls.")
        for key in ("optional", "enabled"):
            if type(room.get(key)) is not bool:
                raise InteriorError("Room enabled and optional settings must be true or false.")
        if not room["optional"] and not room["enabled"]:
            raise InteriorError("The permanent rooms must stay enabled.")
        optional += room["optional"]
        cells = room_cells(room)
        if occupied & cells:
            raise InteriorError("Room floor areas cannot overlap.")
        occupied.update(cells)
    if optional > 4:
        raise InteriorError("Use up to four optional rooms in the first design format.")
    if data["kind"] == "spouse" and (len(rooms) != 1 or optional):
        raise InteriorError("The spouse room has one fixed floor area.")
    floor = floor_cells(data)
    if not _connected(floor):
        raise InteriorError("Enabled rooms must share an edge so the interior is connected.")
    for key in ("entry", "spouse_stand"):
        point = data.get(key)
        if not isinstance(point, list) or len(point) != 2:
            raise InteriorError("Entry and spouse standing positions need two tile coordinates.")
        for coordinate in point:
            _integer(coordinate, 0, 95, "Standing positions use whole tile coordinates.")
    if tuple(data["entry"]) not in floor:
        raise InteriorError("The entry must be on an enabled room's floor.")
    if data["kind"] == "spouse" and tuple(data["spouse_stand"]) not in floor:
        raise InteriorError("The spouse standing position must be on the room floor.")
    _validate_doorway_geometry(data)
    from .interior_layout import validate_partitions, partition_cells
    validate_partitions(data)
    structural = partition_cells(data)
    atlas = data.get("atlas")
    if not isinstance(atlas, dict):
        raise InteriorError("The interior needs tilesheet settings.")
    reference = _text(atlas.get("asset"), 1024, "Use a project tilesheet reference.", empty=True)
    if reference:
        from .world import relative_path
        try:
            relative_path(reference)
        except WorldError as exc:
            raise InteriorError(str(exc)) from exc
    _integer(atlas.get("columns"), 0, 256, "Invalid tilesheet column count.")
    _integer(atlas.get("tile_count"), 0, 65536, "Invalid tilesheet tile count.")
    if reference and (not atlas["columns"] or not atlas["tile_count"]):
        raise InteriorError("Import the tilesheet to establish its dimensions.")
    style = data.get("style")
    if not isinstance(style, dict):
        raise InteriorError("Choose interior floor and wall tiles.")
    for key in ("floor", "wall_top", "wall_middle", "wall_bottom"):
        _integer(style.get(key), 0, max(0, atlas["tile_count"] - 1), "A surface tile is outside the tilesheet.")
    if "spouse_context" in data:
        from .interior_furniture import validate_spouse_context
        data["spouse_context"] = validate_spouse_context(data["spouse_context"])
    if "room_frame" in data:
        from .interior_furniture import ROOM_FRAME_TILES, ROOM_FRAME_JOINS, ROOM_FRAME_DOORWAY, ROOM_FRAME_PARTITIONS
        frame = data["room_frame"]
        if (not isinstance(frame, dict) or not set(ROOM_FRAME_TILES) <= set(frame)
                or set(frame) - set(ROOM_FRAME_TILES) - set(ROOM_FRAME_JOINS) - set(ROOM_FRAME_DOORWAY)
                - set(ROOM_FRAME_PARTITIONS)):
            raise InteriorError("The room frame needs all of its edge and corner tiles.")
        for tile in frame.values():
            _integer(tile, 0, max(0, atlas["tile_count"] - 1), "A room frame tile is outside the tilesheet.")
    def pattern(value, kind):
        if not isinstance(value, dict):
            raise InteriorError("A room finish needs a complete pattern.")
        width, height = (2, 2) if kind == "floor" else (1, 3)
        if type(value.get("width")) is not int or type(value.get("height")) is not int or (value["width"], value["height"]) != (width, height):
            raise InteriorError("Use a complete wallpaper or flooring pattern.")
        tiles = value.get("tiles")
        if not isinstance(tiles, list) or len(tiles) != width * height:
            raise InteriorError("A room finish has missing pattern tiles.")
        for tile in tiles:
            _integer(tile, 0, max(0, atlas["tile_count"] - 1), "A finish tile is outside the tilesheet.")
        if "surface_id" in value:
            _text(value["surface_id"], 260, "A room finish needs a short identity.")

    surfaces = data.get("surfaces", [])
    if not isinstance(surfaces, list) or len(surfaces) > 2048:
        raise InteriorError("Use a finish library of up to 2048 patterns.")
    surface_ids = set()
    for surface in surfaces:
        if not isinstance(surface, dict) or surface.get("kind") not in ("floor", "wall"):
            raise InteriorError("Choose wallpaper or flooring for each finish.")
        identity = _text(surface.get("id"), 260, "Each finish needs a short identity.")
        if identity in surface_ids:
            raise InteriorError("Finish library identities must be unique.")
        surface_ids.add(identity)
        _text(surface.get("name"), 256, "Each finish needs a name.")
        _text(surface.get("dependency", ""), 256, "Use a short provider mod identity.", empty=True)
        pattern(surface, surface["kind"])
    overrides = data.get("room_styles", {})
    if not isinstance(overrides, dict) or any(identity not in {r["id"] for r in rooms} for identity in overrides):
        raise InteriorError("Finishes must belong to an existing room.")
    for room_style in (style, *overrides.values()):
        if not isinstance(room_style, dict):
            raise InteriorError("A room needs valid finish settings.")
        for kind in ("floor", "wall"):
            if kind + "_pattern" in room_style:
                pattern(room_style[kind + "_pattern"], kind)
        for key in ("floor", "wall_top", "wall_middle", "wall_bottom"):
            if key in room_style:
                _integer(room_style[key], 0, max(0, atlas["tile_count"] - 1), "A surface tile is outside the tilesheet.")
    animations = data.get("animations")
    if not isinstance(animations, list) or len(animations) > 128:
        raise InteriorError("Use up to 128 tile animations.")
    animated = set()
    for animation in animations:
        if not isinstance(animation, dict):
            raise InteriorError("Each tile animation must be an object.")
        tile = _integer(animation.get("tile_id"), 0, max(0, atlas["tile_count"] - 1), "Animation tile is outside the tilesheet.")
        if tile in animated:
            raise InteriorError("A tile can have only one animation.")
        animated.add(tile)
        frames = animation.get("frames")
        if not isinstance(frames, list) or not 1 <= len(frames) <= 64:
            raise InteriorError("An animation needs 1–64 frames.")
        for frame in frames:
            if not isinstance(frame, dict):
                raise InteriorError("Animation frames must be objects.")
            _integer(frame.get("tile_id"), 0, max(0, atlas["tile_count"] - 1), "Animation frame is outside the tilesheet.")
            _integer(frame.get("duration_ms"), 16, 60000, "Frame duration must be 16–60000 milliseconds.")
    from .interior_architecture import validate_architecture, architecture_cells
    validate_architecture(data)
    from .interior_levels import validate_levels
    validate_levels(data)
    architectural = architecture_cells(data)
    catalog = data.get("catalog")
    if not isinstance(catalog, list) or len(catalog) > 10000:
        raise InteriorError("Use a furniture library of up to 10000 items.")
    try:
        data["catalog"] = [validate_definition(definition) for definition in catalog]
    except FurnitureValidationError as exc:
        raise InteriorError(str(exc)) from exc
    definitions = {item["id"]: item for item in data["catalog"]}
    if len(definitions) != len(catalog):
        raise InteriorError("Furniture library IDs must be unique.")
    furnishings = data.get("furniture")
    if not isinstance(furnishings, list) or len(furnishings) > 1000:
        raise InteriorError("An interior supports up to 1000 furniture placements.")
    seen, solid = set(), set()
    for item in furnishings:
        if not isinstance(item, dict):
            raise InteriorError("Furniture placements must be objects.")
        identity = _text(item.get("id"), 80, "Furniture placements need stable unique IDs.")
        if identity in seen:
            raise InteriorError("Furniture placement IDs must be unique.")
        seen.add(identity)
        if not isinstance(item.get("item_id"), str):
            raise InteriorError("Furniture placements need a text item ID.")
        definition = definitions.get(item.get("item_id"))
        if definition is None:
            raise InteriorError("Every placed furniture item must exist in the library.")
        for key in ("x", "y"):
            _integer(item.get(key), 0, 95, "Furniture positions use whole tile coordinates.")
        _integer(item.get("rotation"), 0, definition["rotations"] - 1, "This furniture rotation is unavailable.")
        metadata = item.setdefault("mod_data", {})
        if not isinstance(metadata, dict) or len(metadata) > 128 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 256 or len(v) > 4096 for k, v in metadata.items()):
            raise InteriorError("Furniture metadata needs short text keys and values.")
        if "held_item" in item:
            try:
                item["held_item"] = validate_held_item(item["held_item"], definition, definitions)
            except FurnitureValidationError as exc:
                raise InteriorError(str(exc)) from exc
        if definition.get("placement") == "outdoors":
            raise InteriorError("Outdoor-only furniture cannot be placed in an interior.")
        cells = placement_cells(item, definition)
        region = wall_cells(data) if definition["kind"] in WALL_FURNITURE else floor
        if not cells <= region:
            raise InteriorError("Place the whole furniture footprint on its room's wall or floor.")
        if cells & structural:
            raise InteriorError("Move furniture clear of the interior wall before applying this layout.")
        if cells & architectural and definition["kind"] != "rug":
            raise InteriorError("Move furniture clear of the architectural piece before applying this layout.")
        if definition["kind"] != "rug":
            if solid & cells:
                raise InteriorError("Furniture footprints overlap. Rugs may go underneath furniture.")
            solid.update(cells)
    anchors = [tuple(data["spouse_stand"] if data["kind"] == "spouse" else data["entry"])]
    if "doorway" in data:
        x, y = data["doorway"]
        anchors.extend(((x, y), (x, y-1)))
    if any(anchor in structural for anchor in anchors):
        raise InteriorError("Keep the entry, doorway approach, and spouse standing position clear of interior walls.")
    if any(anchor in solid or anchor in architectural for anchor in anchors):
        raise InteriorError("Keep the entry, doorway approach, and spouse standing position clear.")
    if data.get("partitions"):
        from .interior_layout import opening_approaches
        if not opening_approaches(data) <= reachable_tiles(data):
            raise InteriorError("Keep a clear, walkable approach on both sides of each interior opening.")
    if "doorway" in data and doorway_exit(data) not in reachable_tiles(data):
        raise InteriorError("Keep a walkable route from the arrival to the doorway.")
    # Access is an editing/export rule so older blocked spouse rooms can still
    # open for repair. Their standing tile remains protected above.
    return data


def room_edit_candidate(data, *, room_id=None, x, y, width=None, height=None,
                        name="New room", optional=True, allow_rebase=False, snap=True):
    """Build a validated room drop without changing the design or its history.

    Coordinates describe the room's floor, in tiles. Nearby drops snap by at
    most one tile onto a shared edge. Existing rooms, their styles, furnishings
    and entry retain their identities. Only an explicitly permitted canvas
    rebase shifts the rest of the house; callers enable that for unsaved plans.
    """
    original = normalize_interior(data)
    if original["kind"] == "spouse":
        raise InteriorError("The spouse room has one fixed floor area.")
    for value in (x, y):
        _integer(value, -96, 96, "Room positions use whole tile coordinates.")
    previous = next((room for room in original["rooms"] if room["id"] == room_id), None)
    if room_id is not None and previous is None:
        raise InteriorError("Select a room to move.")
    width = previous["width"] if width is None and previous else width
    height = previous["height"] if height is None and previous else height
    for value in (width, height):
        _integer(value, 1, 96, "Room dimensions must be 1–96 whole tiles.")
    if previous is not None and (x, y, width, height) == tuple(previous[key] for key in ("x", "y", "width", "height")):
        return original

    # Preserve floor and wall pieces only when they unambiguously belong to the
    # moved room. A rug or wide painting across a join requires an explicit edit.
    carried = set()
    if previous is not None:
        definitions = {entry["id"]: entry for entry in original["catalog"]}
        regions = _room_regions(original)
        for item in original["furniture"]:
            owners, owner = _placement_rooms(item, definitions[item["item_id"]], regions)
            if room_id in owners:
                if owner != room_id:
                    raise InteriorError("Move furniture spanning multiple rooms before moving this room.")
                carried.add(item["id"])

    positions = []
    if snap:
        for room in original["rooms"]:
            if room["id"] == room_id or not room["enabled"]:
                continue
            vertical = min(y + height, room["y"] + room["height"]) - max(y, room["y"])
            horizontal = min(x + width, room["x"] + room["width"]) - max(x, room["x"])
            if vertical > 0:
                for sx in (room["x"] - width, room["x"] + room["width"]):
                    if abs(sx - x) <= 1:
                        positions.append((abs(sx - x), -vertical, sx, y))
            if horizontal > 0:
                for sy in (room["y"] - height, room["y"] + room["height"]):
                    if abs(sy - y) <= 1:
                        positions.append((abs(sy - y), -horizontal, x, sy))
    # Exact valid placement wins, including a one-room residence with no joins.
    targets = [(x, y)] + [(sx, sy) for _, _, sx, sy in sorted(positions)]
    identity = room_id or uuid.uuid4().hex
    first_error = None
    for px, py in dict.fromkeys(targets):
        candidate = deepcopy(original)
        if previous is None:
            candidate["rooms"].append(dict(id=identity, name=name, x=px, y=py,
                                            width=width, height=height,
                                            optional=optional, enabled=True))
        else:
            room = next(room for room in candidate["rooms"] if room["id"] == room_id)
            room.update(x=px, y=py, width=width, height=height)
            dx, dy = px - previous["x"], py - previous["y"]
            for item in candidate["furniture"]:
                if item["id"] in carried:
                    item["x"] += dx
                    item["y"] += dy
            for partition in (*candidate.get("partitions", []), *candidate.get("architecture", [])):
                if partition["room_id"] == room_id:
                    partition["x"] += dx
                    partition["y"] += dy
            from .interior_levels import room_level
            if room_level(previous) == 1:
                for connector in candidate["rooms"]:
                    if connector.get("upper_room_id") == room_id:
                        connector["x"] += dx
                        connector["y"] += dy
                        for piece in candidate.get("architecture", []):
                            if piece["room_id"] == connector["id"]:
                                piece["x"] += dx
                                piece["y"] += dy
            old_floor = room_cells(previous)
            for anchor in (key for key in ("entry", "spouse_stand", "doorway") if key in candidate):
                if tuple(candidate[anchor]) in old_floor:
                    candidate[anchor][0] += dx
                    candidate[anchor][1] += dy
        shift_x = max(0, 1 - min(room["x"] for room in candidate["rooms"])) if allow_rebase else 0
        shift_y = max(0, 4 - min(room["y"] for room in candidate["rooms"])) if allow_rebase else 0
        if shift_x or shift_y:
            for item in (*candidate["rooms"], *candidate["furniture"], *candidate.get("partitions", []), *candidate.get("architecture", [])):
                item["x"] += shift_x
                item["y"] += shift_y
            for anchor in (key for key in ("entry", "spouse_stand", "doorway") if key in candidate):
                candidate[anchor][0] += shift_x
                candidate[anchor][1] += shift_y
        # Retain the user's canvas, extending only as needed for the room and
        # its border. At the format limit the room may reach the canvas edge.
        for dimension, coordinate, shift in (("width", "x", shift_x), ("height", "y", shift_y)):
            extent = max(room[coordinate] + room[dimension] for room in candidate["rooms"])
            candidate[dimension] = min(96, max(candidate[dimension] + shift, extent + 1))
        try:
            candidate = normalize_interior(candidate)
            from .interior_architecture_rules import validate_architecture_rules
            validate_architecture_rules(candidate, before=original)
            return candidate
        except InteriorError as exc:
            if first_error is None:
                first_error = exc
    raise first_error


class InteriorDraft:
    """All edits are atomic snapshots; rejected edits never enter history."""
    def __init__(self, data=None, kind="residence"):
        self.data = normalize_interior(data if data is not None else new_interior(kind))
        self._undo, self._redo = [], []

    def snapshot(self):
        return deepcopy(self.data)

    def apply(self, data):
        candidate = normalize_interior(data)
        if candidate == self.data:
            return False
        validate_spouse_access(candidate, before=self.data)
        from .interior_architecture_rules import validate_architecture_rules
        validate_architecture_rules(candidate, before=self.data)
        # Keep pre-existing placements intact when opening, restyling or
        # refreshing a library. New placements and deliberate moves/rotations
        # must follow current placement rules; failed edits leave history alone.
        existing = {item["id"]: item for item in self.data["furniture"]}
        dx, dy = _layout_translation(self.data, candidate)
        carried = _carried_room_items(self.data, candidate)
        for item in candidate["furniture"]:
            previous = existing.get(item["id"])
            if item["id"] not in carried and (previous is None
                    or (item["x"], item["y"]) != (previous["x"] + dx, previous["y"] + dy)
                    or any(item[key] != previous[key] for key in ("item_id", "rotation"))):
                validate_furniture_placement(candidate, item)
        self._undo.append(self.snapshot())
        del self._undo[:-50]
        self._redo.clear()
        self.data = candidate
        return True

    def update_resources(self, update):
        """Refresh library resources in every snapshot without adding an edit.

        Connecting game artwork establishes the room's editing baseline. Keep
        that connection in undo and redo states too, while retaining their
        authored layouts, finishes and furniture. Validate all replacements
        before committing so a failed refresh leaves the entire history intact.
        """
        def prepare(data):
            trial = InteriorDraft(data)
            trial.apply(update(trial.snapshot()))
            return trial.data

        candidate = prepare(self.data)
        undo = [prepare(data) for data in self._undo]
        redo = [prepare(data) for data in self._redo]
        self.data, self._undo, self._redo = candidate, undo, redo

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self.snapshot())
        self.data = self._undo.pop()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self.snapshot())
        self.data = self._redo.pop()
        return True

    def add_room(self, name, x, y, width, height, optional=True):
        data = self.snapshot()
        identity = uuid.uuid4().hex
        data["rooms"].append(dict(id=identity, name=name, x=x, y=y, width=width,
                                  height=height, optional=optional, enabled=True))
        self.apply(data)
        return identity

    def remove_room(self, identity):
        data = self.snapshot()
        room = next((r for r in data["rooms"] if r["id"] == identity), None)
        if room is None:
            raise InteriorError("Select a room to remove.")
        from .interior_levels import room_level, remove_raised_room_candidate
        if room.get("kind") == "stairway":
            raise InteriorError("Remove the linked raised room to remove its stairway.")
        if room_level(room) == 1:
            self.apply(remove_raised_room_candidate(data, identity))
            return
        occupied = {(x, y) for x in range(max(0, room["x"]-1), room["x"]+room["width"]+1)
                    for y in range(max(0, room["y"]-3), room["y"]+room["height"]+1)}
        definitions = {d["id"]: d for d in data["catalog"]}
        if any(placement_cells(item, definitions[item["item_id"]]) & occupied for item in data["furniture"]):
            raise InteriorError("Move or remove the room's furniture before removing the room.")
        if any(piece["room_id"] == identity for piece in data.get("architecture", [])):
            raise InteriorError("Move or remove the room’s architectural pieces before removing the room.")
        if "partitions" in data:
            data["partitions"] = [wall for wall in data["partitions"] if wall["room_id"] != identity]
        data["rooms"].remove(room)
        data.get("room_styles", {}).pop(identity, None)
        self.apply(data)

    def place_furniture(self, item_id, x, y, rotation=0):
        data = self.snapshot()
        definition = next((d for d in data["catalog"] if d["id"] == item_id), None)
        if definition is None:
            raise InteriorError("Choose furniture from the library.")
        identity = uuid.uuid4().hex
        data["furniture"].append(dict(id=identity, item_id=item_id, x=x, y=y,
                                     rotation=rotation, mod_data=deepcopy(definition.get("mod_data", {}))))
        self.apply(data)
        return identity

    def _edit_item(self, identity, callback):
        data = self.snapshot()
        item = next((item for item in data["furniture"] if item["id"] == identity), None)
        if item is None:
            raise InteriorError("Select a furniture item first.")
        callback(data, item)
        self.apply(data)

    def move_furniture(self, identity, x, y):
        self._edit_item(identity, lambda data, item: item.update(x=x, y=y))

    def rotate_furniture(self, identity):
        def rotate(data, item):
            definition = next(d for d in data["catalog"] if d["id"] == item["item_id"])
            item["rotation"] = (item["rotation"] + 1) % definition["rotations"]
        self._edit_item(identity, rotate)

    def remove_furniture(self, identity):
        self._edit_item(identity, lambda data, item: data["furniture"].remove(item))

    def set_held_item(self, identity, item_id, mod_data=None):
        """Put one native decoration on a table as an undoable item edit."""
        from .interior_furniture import qualified_furniture_id, FurnitureValidationError
        try:
            item_id = qualified_furniture_id(item_id)
        except FurnitureValidationError as exc:
            raise InteriorError(str(exc)) from exc
        definition = next((entry for entry in self.data["catalog"] if entry["id"] == item_id), None)
        if definition is None:
            raise InteriorError("Choose a tabletop item from the furniture library.")
        metadata = deepcopy(definition.get("mod_data", {}) if mod_data is None else mod_data)
        self._edit_item(identity, lambda data, item: item.update(held_item={"item_id": item_id, "mod_data": metadata}))

    def clear_held_item(self, identity):
        self._edit_item(identity, lambda data, item: item.pop("held_item", None))


def interior_asset_references(data):
    if data["atlas"]["asset"]:
        yield data["atlas"]["asset"]
    for definition in data["catalog"]:
        if definition.get("preview_asset"):
            yield definition["preview_asset"]
    from .interior_spouse_context import spouse_context_asset_refs
    yield from spouse_context_asset_refs(data)


def import_atlas(path, project_root):
    from .interior_furniture import import_texture, _read_texture
    _, image = _read_texture(path)
    width, height = image.size
    if width % 16 or height % 16 or width < 16 or height < 16:
        raise InteriorError("Choose a tilesheet with dimensions that are multiples of 16 pixels.")
    if width > 4096 or height > 4096:
        raise InteriorError("Interior tilesheets must be at most 4096 pixels on each side.")
    reference = import_texture(path, project_root)
    return {"asset": reference, "columns": width // 16, "tile_count": width * height // 256}


def map_layers(data, enabled=None):
    from .interior_layout import shell_floor_cells, partition_cells
    width, height = data["width"], data["height"]
    layers = {name: [0] * (width * height) for name in ("Back", "Buildings", "Front", "Paths")}
    floor = shell_floor_cells(data, enabled)
    active_rooms = [r for r in data["rooms"] if (r["enabled"] if enabled is None else r["id"] in enabled)]
    owners = {cell: room for room in active_rooms for cell in room_cells(room)}
    walls = set()
    def room_style(room):
        return {**data["style"], **data.get("room_styles", {}).get(room["id"], {})}
    def put(layer, x, y, tile):
        if 0 <= x < width and 0 <= y < height:
            layers[layer][y * width + x] = tile + 1
    for x, y in sorted(floor):
        room = owners[x, y]
        style = room_style(room)
        pattern = style.get("floor_pattern")
        tile = pattern["tiles"][((y-room["y"]) % pattern["height"]) * pattern["width"] + (x-room["x"]) % pattern["width"]] if pattern else style["floor"]
        put("Back", x, y, tile)
    for x, y in sorted(floor):
        room = owners[x, y]
        style = room_style(room)
        if (x, y - 1) not in floor:
            for distance, tile in ((3, style["wall_top"]), (2, style["wall_middle"]), (1, style["wall_bottom"])):
                if (x, y-distance) not in floor:
                    pattern = style.get("wall_pattern")
                    if pattern:
                        tile = pattern["tiles"][(3-distance)*pattern["width"] + (x-room["x"]) % pattern["width"]]
                    put("Back", x, y-distance, tile)
                    if y-distance >= 0:
                        walls.add((x, y-distance))
                    # The baseboard blocks entry into the wall. Native hanging
                    # furniture needs the upper two rows free of Buildings tiles.
                    if distance == 1:
                        put("Buildings", x, y-distance, data["atlas"]["tile_count"])
    envelope = floor | walls
    # Room walls are missing floor bounded by real room trim. Their entire
    # cavity remains impassable, including Back wall faces and empty centers;
    # Front art alone does not block movement in the game.
    for x, y in partition_cells(data, enabled, cutaway_only=True):
        put("Buildings", x, y, data["atlas"]["tile_count"])
    # Wallpaper is a three-row north-wall finish, never structural edge art.
    # Legacy/custom designs without frame artwork still keep their collision
    # boundary; transparent blockers must not repeat a wallpaper baseboard.
    for x, y in sorted(envelope):
        for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if (nx, ny) not in envelope:
                put("Buildings", nx, ny, data["atlas"]["tile_count"])

    frame = data.get("room_frame")
    if frame and data["kind"] == "residence":
        def outside(x, y, role):
            if (x, y) not in envelope:
                # A stepped ceiling shares a cell with the adjacent vertical
                # edge. Resolve that junction from occupancy on every write,
                # so left/right room order cannot overwrite it with a side.
                if (x, y+1) in envelope:
                    if (x-1, y) in envelope and "top_join_left" in frame:
                        role = "top_join_left"
                    elif (x+1, y) in envelope and "top_join_right" in frame:
                        role = "top_join_right"
                put("Front", x, y, frame[role])
                put("Buildings", x, y, data["atlas"]["tile_count"])

        for x, y in sorted(envelope):
            bottom = (x, y) in floor and (x, y+1) not in envelope
            if (x-1, y) not in envelope:
                outside(x-1, y, "bottom_left_outer" if bottom else "left")
            if (x+1, y) not in envelope:
                outside(x+1, y, "bottom_right_outer" if bottom else "right")
            if (x, y-1) not in envelope:
                outside(x, y-1, "top")
                if (x-1, y) not in envelope:
                    outside(x-1, y-1, "top_left")
                if (x+1, y) not in envelope:
                    outside(x+1, y-1, "top_right")
            if bottom:
                # The source's lower trim has a transparent floor-facing half.
                # Keep the floor underneath it and draw it in Front, as the
                # game does, so furniture and characters can pass behind it.
                left, right = (x-1, y) not in envelope, (x+1, y) not in envelope
                role = "bottom_left_inner" if left and not right else "bottom_right_inner" if right and not left else "bottom"
                if (x+1, y+1) in floor and "bottom_join_right" in frame:
                    role = "bottom_join_right"
                elif (x-1, y+1) in floor and "bottom_join_left" in frame:
                    role = "bottom_join_left"
                put("Front", x, y, frame[role])
    if "doorway" in data:
        _validate_doorway_geometry(data, enabled)
        x, y = data["doorway"]
        room = owners[x, y]
        style = room_style(room)
        pattern = style.get("floor_pattern")
        tile = (pattern["tiles"][((y+1-room["y"]) % pattern["height"]) * pattern["width"]
                                  + (x-room["x"]) % pattern["width"]]
                if pattern else style["floor"])
        put("Back", x, y+1, tile)
        for layer in ("Buildings", "Front"):
            for point_y in (y, y+1):
                layers[layer][point_y * width + x] = 0
        if frame:
            for tx, ty, role, fallback in (
                    (x-1, y, "bottom_join_right", "bottom_left_inner"),
                    (x+1, y, "bottom_join_left", "bottom_right_inner"),
                    (x-1, y+1, "door_left", "bottom_left_outer"),
                    (x+1, y+1, "door_right", "bottom_right_outer"),
                    (x, y+1, "bottom", "bottom")):
                put("Front", tx, ty, frame.get(role, frame[fallback]))
    from .interior_layout import paint_partitions
    paint_partitions(data, layers, enabled)
    from .interior_architecture import paint_architecture
    paint_architecture(data, layers, enabled)
    return layers


def animation_tile(data, tile, elapsed_ms):
    animation = next((a for a in data["animations"] if a["tile_id"] == tile), None)
    if animation is None:
        return tile
    position = max(0, int(elapsed_ms)) % sum(frame["duration_ms"] for frame in animation["frames"])
    for frame in animation["frames"]:
        if position < frame["duration_ms"]:
            return frame["tile_id"]
        position -= frame["duration_ms"]
    return tile


def interior_background(data):
    """Match the cutaway backdrop across the rendered map and its viewport."""
    return "#050304" if data.get("room_frame") and data["kind"] == "residence" else "#292d30"


def render_interior(data, project_root, elapsed_ms=0, grid=False, *, time_of_day="day", lights_on=True):
    """Render supplied artwork, animation states and observed furniture lights."""
    from .interior_furniture import preview_frame, preview_frame_offset, held_item_preview_offset, FurnitureValidationError
    data = normalize_interior(data)
    width, height = data["width"], data["height"]
    image = Image.new("RGBA", (width * 16, height * 16), interior_background(data))
    draw = ImageDraw.Draw(image)
    sheet = None
    if data["atlas"]["asset"]:
        try:
            from .interior_furniture import _read_texture
            _, sheet = _read_texture(asset_path(data["atlas"]["asset"], project_root))
        except (OSError, ValueError):
            pass
    layers = map_layers(data)
    from .interior_layout import partition_cells
    structural = partition_cells(data)
    partition_art = {(x, y) for y in range(height) for x in range(width)
                     if layers["Front"][y*width+x] and ((x, y) in structural
                        or (x, y+1) in structural or (x, y+2) in structural)}
    from .interior_layout import shell_floor_cells
    floor = shell_floor_cells(data)
    if "doorway" in data:
        floor.add(doorway_exit(data))
    def draw_layer(layer):
        for index, gid in enumerate(layers[layer]):
            if not gid:
                continue
            if layer == "Buildings" and gid == data["atlas"]["tile_count"] + 1:
                continue
            x, y = index % width * 16, index // width * 16
            if sheet is None:
                point = (index % width, index // width)
                is_floor = point in floor and not (layer == "Front" and point in partition_art)
                color = "#b2ad96" if is_floor else "#d2cdbb"
                draw.rectangle((x, y, x+15, y+15), fill=color)
                # A deliberately plain room model until the user's game art is
                # connected. Subtle seams make the space readable without a grid.
                if is_floor:
                    draw.line((x, y+15, x+15, y+15), fill="#a8a28b")
                    if (index // width) % 2 == 0:
                        draw.line((x+15, y, x+15, y+15), fill="#a8a28b")
                elif (index % width, index // width + 1) in floor:
                    draw.rectangle((x, y+12, x+15, y+15), fill="#8f927f")
            else:
                tile = animation_tile(data, gid - 1, elapsed_ms)
                sx, sy = tile % data["atlas"]["columns"] * 16, tile // data["atlas"]["columns"] * 16
                image.alpha_composite(sheet.crop((sx, sy, sx+16, sy+16)), (x, y))
    for layer in ("Back", "Buildings"):
        draw_layer(layer)
    definitions = {item["id"]: item for item in data["catalog"]}
    ordered = sorted(data["furniture"], key=lambda item: (definitions[item["item_id"]]["kind"] != "rug", item["y"] + footprint(definitions[item["item_id"]], item["rotation"])[1]))
    for item in ordered:
        definition = definitions[item["item_id"]]
        fw, fh = footprint(definition, item["rotation"])
        x, y = item["x"] * 16, item["y"] * 16
        try:
            with preview_frame(definition, project_root, item["rotation"], elapsed_ms,
                               time_of_day=time_of_day, lights_on=lights_on) as sprite:
                dx, dy = preview_frame_offset(definition, item["rotation"], elapsed_ms,
                                              time_of_day=time_of_day, lights_on=lights_on)
                image.alpha_composite(sprite, (x + dx, y + fh * 16 - sprite.height + dy))
        except (FurnitureValidationError, OSError):
            draw.rectangle((x, y, x+fw*16-1, y+fh*16-1), fill="#76849b", outline="#d9dfeb")
            draw.text((x+2, y+1), "?", fill="#ffffff")
        if "held_item" in item:
            held_definition = definitions[item["held_item"]["item_id"]]
            try:
                with preview_frame(held_definition, project_root, 0, elapsed_ms,
                                   time_of_day=time_of_day, lights_on=lights_on) as sprite:
                    dx, dy = held_item_preview_offset(definition, item["rotation"], sprite_size=sprite.size)
                    image.alpha_composite(sprite, (x + dx, y + dy))
            except (FurnitureValidationError, OSError):
                # The native item remains exportable even when its optional
                # local sprite or observed tabletop position is unresolved.
                pass
    draw_layer("Front")
    if sheet is not None:
        sheet.close()
    from .interior_lighting import apply_preview_lighting
    lit_image = apply_preview_lighting(image, data, project_root, time_of_day=time_of_day, lights_on=lights_on)
    image.close()
    image = lit_image
    draw = ImageDraw.Draw(image)
    if grid:
        for x in range(0, width * 16, 16):
            draw.line((x, 0, x, height*16), fill=(0, 0, 0, 75))
        for y in range(0, height * 16, 16):
            draw.line((0, y, width*16, y), fill=(0, 0, 0, 75))
    return image


def interior_export_issues(data, root):
    issues = []
    try:
        data = normalize_interior(data)
        validate_spouse_access(data)
        from .interior_architecture_rules import validate_architecture_rules
        validate_architecture_rules(data)
        if not data["atlas"]["asset"]:
            raise InteriorError("Import a tilesheet and choose floor/wall tiles before exporting the interior.")
        from .interior_furniture import _read_texture
        _, image = _read_texture(asset_path(data["atlas"]["asset"], root))
        width, height = image.size
        if width % 16 or height % 16 or width // 16 != data["atlas"]["columns"] or width * height // 256 != data["atlas"]["tile_count"]:
                raise InteriorError("The interior tilesheet dimensions have changed; import it again.")
        # Preview assets also belong to the portable editable project.
        for reference in interior_asset_references(data):
            if not asset_path(reference, root).is_file():
                raise InteriorError("An interior artwork file is missing: " + reference)
        for animation in data["animations"]:
            if len({frame["duration_ms"] for frame in animation["frames"]}) > 1:
                raise InteriorError("Stardew tile animations need one shared frame duration. Use equal durations before exporting.")
    except (WorldError, OSError, ValueError) as exc:
        issues.append({"level": "error", "field": "interior", "message": str(exc)})
    return issues


def _properties(parent, values):
    props = ET.SubElement(parent, "properties")
    for key, value in values.items():
        ET.SubElement(props, "property", name=key, value=str(value))


def interior_tmx(data, *, enabled=None, design_id=""):
    """Generate only structural/decorative map tiles. Furniture stays in JSON."""
    data = normalize_interior(data)
    validate_spouse_access(data)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(data, enabled=enabled)
    from .interior_levels import validate_levels
    validate_levels(data, enabled)
    atlas = data["atlas"]
    if not atlas["asset"]:
        raise InteriorError("Choose the interior tilesheet before creating a game map.")
    width, height = data["width"], data["height"]
    root = ET.Element("map", version="1.10", orientation="orthogonal", renderorder="right-down",
                      width=str(width), height=str(height), tilewidth="16", tileheight="16", infinite="0")
    _properties(root, {"Indoors": "T", "AllowBeds": "true", "AllowMiniFridges": "true"})
    tileset = ET.SubElement(root, "tileset", firstgid="1", name="pixelheart_interior", tilewidth="16", tileheight="16",
                            columns=str(atlas["columns"]), tilecount=str(atlas["tile_count"]))
    ET.SubElement(tileset, "image", source="tiles.png", width=str(atlas["columns"] * 16),
                  height=str(atlas["tile_count"] // atlas["columns"] * 16))
    for animation in data["animations"]:
        node = ET.SubElement(tileset, "tile", id=str(animation["tile_id"]))
        sequence = ET.SubElement(node, "animation")
        for frame in animation["frames"]:
            ET.SubElement(sequence, "frame", tileid=str(frame["tile_id"]), duration=str(frame["duration_ms"]))
    collision = ET.SubElement(root, "tileset", firstgid=str(atlas["tile_count"]+1),
                              name="pixelheart_collision", tilewidth="16", tileheight="16", tilecount="1", columns="1")
    ET.SubElement(collision, "image", source="collision.png", width="16", height="16")
    layers = map_layers(data, enabled)
    # A separate tileset reference scopes the marker to one coordinate while
    # preserving the original sheet and animation without changing its pixels.
    if data["kind"] == "spouse" and design_id:
        first_gid = atlas["tile_count"] + 2
        x, y = data["spouse_stand"]
        floor_tile = layers["Back"][y * width + x] - 1
        marker = ET.SubElement(root, "tileset", firstgid=str(first_gid), name="pixelheart_spouse_marker",
                               tilewidth="16", tileheight="16", tilecount=str(atlas["tile_count"]), columns=str(atlas["columns"]))
        ET.SubElement(marker, "image", source="tiles.png", width=str(atlas["columns"]*16), height=str(atlas["tile_count"]//atlas["columns"]*16))
        node = ET.SubElement(marker, "tile", id=str(floor_tile))
        _properties(node, {"Pixelheart.Interiors/SpouseRoom": design_id})
        animation = next((a for a in data["animations"] if a["tile_id"] == floor_tile), None)
        if animation:
            sequence = ET.SubElement(node, "animation")
            for frame in animation["frames"]:
                ET.SubElement(sequence, "frame", tileid=str(frame["tile_id"]), duration=str(frame["duration_ms"]))
        x, y = data["spouse_stand"]
        layers["Back"][y * width + x] = first_gid + floor_tile
        path_first = atlas["tile_count"] * 2 + 2
        paths = ET.SubElement(root, "tileset", firstgid=str(path_first), name="pixelheart_paths",
                              tilewidth="16", tileheight="16", tilecount="8", columns="8")
        ET.SubElement(paths, "image", source="paths.png", width="128", height="16")
        layers["Paths"][y * width + x] = path_first + 7
    # Native DecoratableLocation requires this vanilla sheet when it initializes
    # wall/floor regions, even when the authored finish uses a custom atlas.
    native_first = max(int(sheet.get("firstgid")) + int(sheet.get("tilecount")) for sheet in root.findall("tileset"))
    native_surfaces = ET.SubElement(root, "tileset", firstgid=str(native_first), name="walls_and_floors",
                                    tilewidth="16", tileheight="16", tilecount="688", columns="16")
    ET.SubElement(native_surfaces, "image", source="walls_and_floors", width="256", height="688")
    for index, (name, values) in enumerate(layers.items(), 1):
        node = ET.SubElement(root, "layer", id=str(index), name=name, width=str(width), height=str(height))
        if name == "Paths":
            node.set("visible", "0")
        content = ET.SubElement(node, "data", encoding="csv")
        content.text = "\n" + ",\n".join(",".join(map(str, values[y*width:(y+1)*width])) for y in range(height)) + "\n"
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def compile_interior(data, identity, npc_id, root, prefix):
    """Return maps and the versioned contract consumed by our SMAPI companion."""
    data = normalize_interior(data)
    issues = interior_export_issues(data, root)
    if issues:
        raise InteriorError(issues[0]["message"])
    optional = [room for room in data["rooms"] if room["optional"]]
    permanent = {room["id"] for room in data["rooms"] if not room["optional"]}
    files = {prefix + "tiles.png": asset_path(data["atlas"]["asset"], root).read_bytes()}
    stream = io.BytesIO()
    Image.new("RGBA", (16, 16)).save(stream, format="PNG")
    files[prefix + "collision.png"] = stream.getvalue()
    if data["kind"] == "spouse":
        # Invisible metadata sheet: Paths index 7 is the documented spouse stand marker.
        stream = io.BytesIO()
        Image.new("RGBA", (128, 16)).save(stream, format="PNG")
        files[prefix + "paths.png"] = stream.getvalue()
    variants, patches = [], []
    default_enabled = {room["id"] for room in data["rooms"] if room["enabled"]}
    default = ""
    for mask in range(1 << len(optional)):
        enabled = permanent | {room["id"] for bit, room in enumerate(optional) if mask & (1 << bit)}
        # A removed connector may split the house. Such variants are never offered.
        floor = floor_cells(data, enabled)
        from .interior_layout import partition_cells, opening_approaches
        from .interior_architecture import architecture_cells, architecture_walkable_connected
        walkable = floor - partition_cells(data, enabled) - architecture_cells(data, enabled)
        if (not architecture_walkable_connected(data, enabled)
                or data["kind"] != "spouse" and tuple(data["entry"]) not in walkable
                or not opening_approaches(data, enabled) <= walkable):
            continue
        try:
            _validate_doorway_geometry(data, enabled)
            from .interior_levels import validate_levels
            validate_levels(data, enabled)
            from .interior_architecture_rules import validate_architecture_rules
            validate_architecture_rules(data, enabled=enabled)
        except InteriorError:
            continue
        key = str(mask)
        map_asset = "Maps/" + identity + "_Interior_" + key
        path = prefix + "room_" + key + ".tmx"
        files[path] = interior_tmx(data, enabled=enabled, design_id=identity)
        patches.append({"Action": "Load", "Target": map_asset, "FromFile": path})
        edits = []
        fixture_art = architecture_cells(data, enabled, layers=("Back",))
        fixture_occupied = architecture_cells(data, enabled)
        from .interior_architecture_rules import architecture_rule, architecture_clearance_cells
        architecture_definitions = {piece["id"]: piece for piece in data.get("architecture_catalog", [])}
        for piece in data.get("architecture", []):
            if piece["room_id"] in enabled and architecture_rule(architecture_definitions[piece["piece_id"]]) == "steps_corridor":
                from .interior_levels import stair_connection_clearance
                fixture_occupied.update(stair_connection_clearance(data, piece, architecture_definitions[piece["piece_id"]]))
        for x, y in sorted(fixture_occupied):
            edits.append({"Layer": "Back", "Position": {"X": x, "Y": y}, "SetProperties": {"NoFurniture": "T"}})
        from .interior_layout import shell_floor_cells
        surface_floor = shell_floor_cells(data, enabled)
        for room in data["rooms"]:
            if room["id"] not in enabled:
                continue
            region = identity + "_" + room["id"]
            for x, y in sorted(room_cells(room) & surface_floor):
                if (x, y) not in fixture_art:
                    edits.append({"Layer": "Back", "Position": {"X": x, "Y": y}, "SetProperties": {"FloorID": region}})
                if (x, y-1) not in surface_floor and (x, y-3) not in surface_floor and not {(x, y-1), (x, y-2), (x, y-3)} & fixture_art:
                    edits.append({"Layer": "Back", "Position": {"X": x, "Y": y-3}, "SetProperties": {"WallID": region}})
        if "doorway" in data:
            threshold = tuple(data["doorway"])
            owner = next(room for room in data["rooms"] if room["id"] in enabled and threshold in room_cells(room))
            x, y = doorway_exit(data)
            edits.append({"Layer": "Back", "Position": {"X": x, "Y": y},
                          "SetProperties": {"NoFurniture": "T", "FloorID": identity + "_" + owner["id"]}})
        if data["kind"] == "spouse":
            x, y = data["spouse_stand"]
            edits.append({"Layer": "Back", "Position": {"X": x, "Y": y}, "SetProperties": {"Pixelheart.Interiors/SpouseRoom": identity}})
        # Nonempty region lists select the game's modern WallID/FloorID scan;
        # otherwise a generic DecoratableLocation uses empty legacy rectangles.
        regions = ",".join(identity + "_" + room["id"] for room in data["rooms"] if room["id"] in enabled)
        patches.append({"Action": "EditMap", "Target": map_asset, "MapProperties": {"WallIDs": regions, "FloorIDs": regions}, "MapTiles": edits})
        variants.append({"id": key, "map_asset": map_asset, "enabled_rooms": sorted(enabled)})
        if enabled == default_enabled:
            default = key
    if not default:
        # '0' is truthy; the empty string alone means no valid default.
        raise InteriorError("The default room configuration is unavailable.")
    runtime = {"version": 1, "location": identity, "spouse_npc": npc_id if data["kind"] == "spouse" else "",
               "width": data["width"], "height": data["height"], "entry": data["entry"],
               "spouse_stand": data["spouse_stand"], "spouse_marker_x": data["spouse_stand"][0],
               "spouse_marker_y": data["spouse_stand"][1], "rooms": deepcopy(data["rooms"]),
               "variants": variants, "default_variant": default, "furniture": deepcopy(data["furniture"])}
    used_surfaces = {style.get(key, {}).get("surface_id") for style in (data["style"], *data.get("room_styles", {}).values()) for key in ("floor_pattern", "wall_pattern")}
    used_furniture = {item["item_id"] for item in data["furniture"]}
    used_furniture.update(item["held_item"]["item_id"] for item in data["furniture"] if "held_item" in item)
    dependencies = sorted({d["dependency"] for d in data["catalog"] if d.get("dependency") and d["id"] in used_furniture} |
                          {d["dependency"] for d in data.get("surfaces", []) if d.get("dependency") and d["id"] in used_surfaces})
    return {"files": files, "patches": patches, "runtime": runtime,
            "map_asset": next(v["map_asset"] for v in variants if v["id"] == default),
            "dependencies": dependencies}
