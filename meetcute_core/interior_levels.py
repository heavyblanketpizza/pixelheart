"""One-storey raised rooms connected by an explicit, same-map stair passage.

A four-row neck contains an upper landing, two tread rows and a lower landing.
This preserves the game's three-row wall face between the two floor regions.
It is a map connection, not a warp or an additional gameplay floor.
"""
from copy import deepcopy
import uuid

STAIRWAY_WIDTH = 2
STAIRWAY_HEIGHT = 4


def room_level(room):
    return room.get("level", 0)


def _sides(room):
    x, y, width, height = (room[key] for key in ("x", "y", "width", "height"))
    return {(tx, ty) for tx in (x-1, x+width) for ty in range(y, y+height)}


def validate_levels(data, enabled=None):
    """Validate saved topology, or a proposed optional-room configuration."""
    from .interiors import InteriorError, room_cells
    from .interior_architecture_rules import architecture_rule
    rooms = {room["id"]: room for room in data["rooms"]}
    active = {room["id"] for room in data["rooms"] if room["enabled"]} if enabled is None else set(enabled)
    definitions = {piece["id"]: piece for piece in data.get("architecture_catalog", [])}
    connectors = [room for room in data["rooms"] if room.get("kind") == "stairway"]
    for room in data["rooms"]:
        if type(room_level(room)) is not int or room_level(room) not in (0, 1):
            raise InteriorError("Room levels are ground floor or one raised level.")
        if data["kind"] != "residence" and (room_level(room) or room.get("kind") == "stairway"):
            raise InteriorError("Raised rooms belong to a residence floorplan.")
    all_floor = set().union(*(room_cells(room) for room in data["rooms"]))
    linked = {}
    for stair in connectors:
        upper_id, lower_id = stair.get("upper_room_id"), stair.get("lower_room_id")
        upper = rooms.get(upper_id) if isinstance(upper_id, str) else None
        lower = rooms.get(lower_id) if isinstance(lower_id, str) else None
        if (upper is None or lower is None or upper.get("kind") == "stairway" or lower.get("kind") == "stairway"
                or room_level(upper) != 1 or room_level(lower) != 0):
            raise InteriorError("A stairway must link a raised room to a ground-floor room below it.")
        if (stair["width"], stair["height"], room_level(stair)) != (STAIRWAY_WIDTH, STAIRWAY_HEIGHT, 0):
            raise InteriorError("A stairway needs a two-tile-wide passage with an upper landing, two tread rows and a lower landing.")
        x, y = stair["x"], stair["y"]
        north = {(tx, y-1) for tx in range(x, x+2)}
        south = {(tx, y+4) for tx in range(x, x+2)}
        if not north <= room_cells(upper) or not south <= room_cells(lower):
            raise InteriorError("Keep the stairway joined to its raised room above and ground-floor room below.")
        if y != upper["y"]+upper["height"] or y+4 != lower["y"]:
            raise InteriorError("The stairway connects the raised room's bottom edge to the lower room's top edge.")
        if _sides(stair) & all_floor:
            raise InteriorError("Keep both sides of the stair passage outside the floorplan; flat-room partitions cannot create a raised connection.")
        if (stair["optional"], stair["enabled"]) != (upper["optional"], upper["enabled"]):
            raise InteriorError("Enable or disable the raised room and its stairway together.")
        if stair["id"] in active and not {upper_id, lower_id} <= active:
            raise InteriorError("A stairway needs both linked rooms in this room configuration.")
        if upper_id in active and stair["id"] not in active:
            raise InteriorError("The raised room and its stairway must remain available together.")
        if upper_id in linked:
            raise InteriorError("Each raised room has one dedicated stairway.")
        linked[upper_id] = stair["id"]
        pieces = [piece for piece in data.get("architecture", []) if piece["room_id"] == stair["id"]]
        if len(pieces) != 1:
            raise InteriorError("Keep one matching stair piece in the dedicated stairway.")
        piece = pieces[0]
        definition = definitions.get(piece["piece_id"])
        if (definition is None or architecture_rule(definition) != "steps_corridor"
                or (definition["width"], definition["height"]) != (2, 2)
                or (piece["x"], piece["y"]) != (x, y+1)):
            raise InteriorError("Keep the two tread rows centred between the stairway's upper and lower landings.")
        if any(wall["room_id"] == stair["id"] for wall in data.get("partitions", [])):
            raise InteriorError("The dedicated stairway must stay clear of interior walls.")
    for room in data["rooms"]:
        if room_level(room) == 1 and room["id"] not in linked:
            raise InteriorError("A raised room needs a dedicated stairway to the ground floor.")
    ordinary = [room for room in data["rooms"] if room.get("kind") != "stairway"]
    for index, first in enumerate(ordinary):
        first_cells = room_cells(first)
        edge = {(x+dx, y+dy) for x, y in first_cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} - first_cells
        for second in ordinary[index+1:]:
            if room_level(first) != room_level(second) and edge & room_cells(second):
                raise InteriorError("Ground-floor and raised rooms must meet through their stairway, without a flat-floor bypass.")


def stair_connection_issues(data, item, definition, enabled=None):
    rooms = {room["id"]: room for room in data["rooms"]}
    room = rooms.get(item["room_id"])
    if (room is not None and room.get("kind") == "stairway"
            and (definition["width"], definition["height"]) == (2, 2)
            and (item["x"], item["y"]) == (room["x"], room["y"]+1)):
        return []
    return [{"placement_id": item["id"], "piece_id": item["piece_id"], "code": "stairs_connection",
             "message": f"{definition['name']}: connect a raised room to the ground floor using its dedicated stairway; partitions on one flat floor are not a level change.",
             "cells": [[item["x"]+x, item["y"]+y] for y in range(definition["height"]) for x in range(definition["width"])]}]


def stair_connection_clearance(data, item, definition):
    from .interior_architecture_rules import architecture_clearance_cells
    result = architecture_clearance_cells(item, definition)
    room = next((room for room in data["rooms"] if room["id"] == item["room_id"]), None)
    if room is not None and room.get("kind") == "stairway":
        result |= {(x, y) for x in range(room["x"], room["x"]+2) for y in (room["y"]-1, room["y"]+4)}
    return result


def raised_room_candidate(data, x, y, width, height, *, name="Raised room", optional=False, allow_rebase=False):
    from .interiors import InteriorError, _integer, normalize_interior
    from .interior_architecture_rules import architecture_rule, validate_architecture_rules
    original = normalize_interior(data)
    if original["kind"] != "residence":
        raise InteriorError("Raised rooms belong to a residence floorplan.")
    for value in (x, y):
        _integer(value, -96, 96, "Room positions use whole tile coordinates.")
    for value in (width, height):
        _integer(value, 1, 96, "Room dimensions must be 1–96 whole tiles.")
    if width < 2:
        raise InteriorError("A raised room needs room for a two-tile-wide stairway.")
    stairs = next((piece for piece in original.get("architecture_catalog", [])
                   if architecture_rule(piece) == "steps_corridor" and (piece["width"], piece["height"]) == (2, 2)), None)
    if stairs is None:
        raise InteriorError("Connect the architectural library's two-tile-wide steps before adding a raised room.")
    targets = []
    for lower in original["rooms"]:
        if room_level(lower) or lower.get("kind") == "stairway" or not lower["enabled"]:
            continue
        left, right = max(x, lower["x"]), min(x+width, lower["x"]+lower["width"])
        target_y = lower["y"]-4-height
        if right-left >= 2 and abs(target_y-y) <= 1:
            center = (left+right-2)//2
            targets.append((abs(target_y-y), lower, target_y, list(sorted(range(left, right-1), key=lambda position: abs(position-center)))))
    if not targets:
        raise InteriorError("Place the raised room four tiles above a ground-floor room, with space for a two-tile-wide stairway.")
    error = None
    for _, lower, target_y, positions in sorted(targets, key=lambda target: target[0]):
        for stair_x in positions:
            candidate = deepcopy(original)
            upper_id, stair_id = uuid.uuid4().hex, uuid.uuid4().hex
            candidate["rooms"].extend([
                dict(id=upper_id, name=name, x=x, y=target_y, width=width, height=height, level=1, optional=optional, enabled=True),
                dict(id=stair_id, name="Stairway", kind="stairway", level=0, x=stair_x, y=target_y+height, width=2, height=4,
                     upper_room_id=upper_id, lower_room_id=lower["id"], optional=optional, enabled=True)])
            candidate.setdefault("architecture", []).append(dict(id=uuid.uuid4().hex, piece_id=stairs["id"], x=stair_x,
                                                                  y=target_y+height+1, room_id=stair_id))
            shift_x = max(0, 1-min(room["x"] for room in candidate["rooms"])) if allow_rebase else 0
            shift_y = max(0, 4-min(room["y"] for room in candidate["rooms"])) if allow_rebase else 0
            if shift_x or shift_y:
                for record in (*candidate["rooms"], *candidate["furniture"], *candidate.get("architecture", []), *candidate.get("partitions", [])):
                    record["x"] += shift_x
                    record["y"] += shift_y
                for key in ("entry", "spouse_stand", "doorway"):
                    if key in candidate:
                        candidate[key][0] += shift_x
                        candidate[key][1] += shift_y
            for dimension, coordinate, shift in (("width", "x", shift_x), ("height", "y", shift_y)):
                candidate[dimension] = min(96, max(candidate[dimension]+shift, max(room[coordinate]+room[dimension] for room in candidate["rooms"])+1))
            try:
                candidate = normalize_interior(candidate)
                validate_architecture_rules(candidate, before=original)
                return candidate
            except InteriorError as exc:
                if error is None:
                    error = exc
    raise error


def set_room_enabled_candidate(data, room_id, enabled):
    from .interiors import InteriorError, normalize_interior
    from .interior_architecture_rules import validate_architecture_rules
    candidate = normalize_interior(data)
    room = next((room for room in candidate["rooms"] if room["id"] == room_id), None)
    if room is None:
        raise InteriorError("Select a room first.")
    if room.get("kind") == "stairway":
        room_id = room["upper_room_id"]
    for room in candidate["rooms"]:
        if room["id"] == room_id or room.get("upper_room_id") == room_id:
            room["enabled"] = enabled
    candidate = normalize_interior(candidate)
    validate_architecture_rules(candidate, before=data)
    return candidate


def remove_raised_room_candidate(data, room_id):
    from .interiors import InteriorError, normalize_interior, room_cells, placement_cells
    from .interior_architecture_rules import validate_architecture_rules
    candidate = normalize_interior(data)
    room = next((room for room in candidate["rooms"] if room["id"] == room_id), None)
    if room is None or room_level(room) != 1:
        raise InteriorError("Select a raised room to remove together with its stairway.")
    connectors = [record for record in candidate["rooms"] if record.get("upper_room_id") == room_id]
    removed = {room_id, *(record["id"] for record in connectors)}
    if any(piece["room_id"] == room_id for piece in candidate.get("architecture", [])):
        raise InteriorError("Move or remove the raised room's architectural pieces before removing it.")
    cells = set().union(*(room_cells(record) | {(x, y-distance) for x, y in room_cells(record) for distance in (1, 2, 3)}
                          for record in (room, *connectors)))
    definitions = {piece["id"]: piece for piece in candidate["catalog"]}
    if any(placement_cells(item, definitions[item["item_id"]]) & cells for item in candidate["furniture"]):
        raise InteriorError("Move or remove furniture from the raised room and stairway before removing them.")
    candidate["rooms"] = [record for record in candidate["rooms"] if record["id"] not in removed]
    candidate["architecture"] = [piece for piece in candidate.get("architecture", []) if piece["room_id"] not in removed]
    candidate["partitions"] = [wall for wall in candidate.get("partitions", []) if wall["room_id"] not in removed]
    for identity in removed:
        candidate.get("room_styles", {}).pop(identity, None)
    candidate = normalize_interior(candidate)
    validate_architecture_rules(candidate, before=data)
    return candidate
