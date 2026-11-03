"""Atomic floorplan edits and explicit, portable interior partitions.

A partition's origin is its leftmost/topmost occupied tile. Room walls reserve
a cutaway cavity, framed by the same shell as the outside of a room. Older
one-tile dividers keep their dimensions and supplied trim. Openings are spans
along the segment and cross its complete thickness.
"""
from copy import deepcopy
import re
import uuid


def native_partition_thickness(axis):
    """Space for two side edges, or a void/cap/three-row north wall."""
    return 5 if axis == "horizontal" else 2


def partition_rectangle(partition, *, visual=False):
    horizontal = partition["axis"] == "horizontal"
    thickness = partition.get("thickness", 1)
    x, y = partition["x"], partition["y"]
    width, height = ((partition["length"], thickness) if horizontal
                     else (thickness, partition["length"]))
    if visual and horizontal and thickness == 1:
        y, height = y - 2, 3
    return x, y, width, height


def partition_opening_rectangle(partition, gap, *, visual=False):
    x, y, width, height = partition_rectangle(partition, visual=visual)
    if partition["axis"] == "horizontal":
        return x + gap["offset"], y, gap["width"], height
    return x, y + gap["offset"], width, gap["width"]


def _rectangle_cells(rectangle):
    x, y, width, height = rectangle
    return {(tx, ty) for ty in range(y, y + height) for tx in range(x, x + width)}


def partition_footprint(partition, *, include_openings=False):
    cells = _rectangle_cells(partition_rectangle(partition))
    if not include_openings:
        for gap in partition["openings"]:
            cells.difference_update(_rectangle_cells(partition_opening_rectangle(partition, gap)))
    return cells


def partition_span(partition):
    """Return the whole segment, including its openings, in authored order."""
    horizontal = partition["axis"] == "horizontal"
    return [(partition["x"] + (offset if horizontal else 0),
             partition["y"] + (0 if horizontal else offset))
            for offset in range(partition["length"])]


def partition_cells(data, enabled=None, *, cutaway_only=False):
    """Physical wall cells for the selected room configuration."""
    active = {room["id"] for room in data["rooms"] if room["enabled"]} if enabled is None else set(enabled)
    cells = set()
    for partition in data.get("partitions", []):
        if partition["room_id"] not in active or cutaway_only and partition.get("thickness", 1) == 1:
            continue
        cells.update(partition_footprint(partition))
    return cells


def shell_floor_cells(data, enabled=None):
    """Visible floor, with room-wall cavities removed before framing."""
    from .interiors import floor_cells
    return floor_cells(data, enabled) - partition_cells(data, enabled, cutaway_only=True)


def shell_wall_regions(data, enabled=None):
    """North wall faces belong to the room whose floor is immediately south."""
    from .interiors import room_cells
    active = {room["id"] for room in data["rooms"] if room["enabled"]} if enabled is None else set(enabled)
    floor = shell_floor_cells(data, enabled)
    walls, caps = {}, {}
    for room in data["rooms"]:
        if room["id"] not in active:
            continue
        heads = {point for point in room_cells(room) & floor if (point[0], point[1]-1) not in floor}
        walls[room["id"]] = {(x, y-d) for x, y in heads for d in (1, 2, 3)
                              if y >= d and (x, y-d) not in floor}
        caps[room["id"]] = {(x, y-4) for x, y in heads if y >= 4 and (x, y-4) not in floor}
    return walls, caps


def validate_partitions(data):
    """Validate schema and floor ownership, without resolving furniture/assets."""
    from .interiors import InteriorError, _integer, _connected, floor_cells, room_cells
    partitions = data.get("partitions", [])
    if not isinstance(partitions, list) or len(partitions) > 128:
        raise InteriorError("A floorplan supports up to 128 interior walls.")
    if partitions and data["kind"] != "residence":
        raise InteriorError("Interior walls belong to residence floorplans.")
    rooms = {room["id"]: room for room in data["rooms"]}
    seen = set()
    for partition in partitions:
        if not isinstance(partition, dict):
            raise InteriorError("Each interior wall needs a valid segment.")
        identity = partition.get("id")
        if (not isinstance(identity, str) or len(identity) > 80
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", identity) or identity in seen):
            raise InteriorError("Interior walls need stable, unique IDs.")
        seen.add(identity)
        owner = partition.get("room_id")
        room = rooms.get(owner) if isinstance(owner, str) else None
        if room is None:
            raise InteriorError("Each interior wall must belong to an existing room.")
        if partition.get("axis") not in ("horizontal", "vertical"):
            raise InteriorError("Draw an interior wall horizontally or vertically.")
        for key in ("x", "y"):
            _integer(partition.get(key), 0, 95, "Wall positions use whole tile coordinates.")
        length = _integer(partition.get("length"), 1, 96, "Interior walls must be 1–96 tiles long.")
        thickness = partition.get("thickness", 1)
        if type(thickness) is not int or thickness not in (1, native_partition_thickness(partition["axis"])):
            raise InteriorError("Room walls need two columns or five rows; slim dividers use one tile.")
        if not _rectangle_cells(partition_rectangle(partition)) <= room_cells(room):
            raise InteriorError("Keep the entire wall inside its room; resize or move the wall first.")
        openings = partition.get("openings")
        if not isinstance(openings, list) or len(openings) > 32:
            raise InteriorError("A wall supports up to 32 openings.")
        occupied = set()
        for gap in openings:
            if not isinstance(gap, dict):
                raise InteriorError("Each opening needs a position and width.")
            start = _integer(gap.get("offset"), 0, length-1, "Place openings within their wall.")
            width = _integer(gap.get("width"), 1, length, "Openings need a positive tile width.")
            cells = set(range(start, start + width))
            if start + width > length or cells & occupied:
                raise InteriorError("Keep openings separate and within their wall.")
            occupied.update(cells)
        if len(occupied) == length:
            raise InteriorError("An opening must leave some wall; remove the wall instead.")
    if partitions:
        floor = floor_cells(data) - partition_cells(data)
        if tuple(data["entry"]) not in floor:
            raise InteriorError("Keep the arrival position clear of interior walls.")
        if not _connected(floor):
            raise InteriorError("Keep rooms connected through a doorway or wider opening.")


def resize_room_candidate(data, room_id, x, y, width, height):
    """Resize a room without moving any of its contents or authored anchors."""
    from .interiors import InteriorError, _integer, normalize_interior
    candidate = normalize_interior(data)
    if candidate["kind"] != "residence":
        raise InteriorError("The spouse room has one fixed floor area.")
    room = next((room for room in candidate["rooms"] if room["id"] == room_id), None)
    if room is None:
        raise InteriorError("Select a room to resize.")
    for value in (x, y):
        _integer(value, 0, 95, "Room positions use whole tile coordinates.")
    for value in (width, height):
        _integer(value, 1, 96, "Room dimensions must be 1–96 whole tiles.")
    doorway = candidate.get("doorway")
    if (doorway is not None and room["x"] <= doorway[0] < room["x"] + room["width"]
            and doorway[1] == room["y"] + room["height"] - 1):
        old_y = doorway[1]
        doorway[1] = y + height - 1
        if candidate["entry"] == [doorway[0], old_y - 1]:
            candidate["entry"][1] = doorway[1] - 1
    room.update(x=x, y=y, width=width, height=height)
    candidate["width"] = min(96, max(candidate["width"], x + width + 1))
    candidate["height"] = min(96, max(candidate["height"], y + height + 1))
    candidate = normalize_interior(candidate)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def corridor_candidate(data, x, y, width, height, *, name="Hallway", allow_rebase=False):
    """A narrow, permanent floor region uses the same room and export contract."""
    from .interiors import room_edit_candidate
    candidate = room_edit_candidate(data, x=x, y=y, width=width, height=height,
                                    name=name, optional=False, allow_rebase=allow_rebase)
    candidate["rooms"][-1]["kind"] = "hallway"
    return candidate


def partition_candidate(data, room_id, axis, x, y, length, *, opening_width=1, openings=None, partition_id=None, thickness=None):
    """Create or replace a wall atomically; unchanged IDs survive later edits."""
    from .interiors import InteriorError, _integer, normalize_interior
    _integer(length, 1, 96, "Interior walls must be 1–96 tiles long.")
    _integer(opening_width, 0, 96, "Opening widths must be whole tile counts.")
    if openings is None:
        if opening_width >= length:
            raise InteriorError("Leave at least one wall tile beside the opening.")
        openings = ([{"offset": (length-opening_width)//2, "width": opening_width}]
                    if opening_width else [])
    candidate = normalize_interior(data)
    partitions = candidate.setdefault("partitions", [])
    previous = next((wall for wall in partitions if wall["id"] == partition_id), None)
    if partition_id is not None and previous is None:
        raise InteriorError("Select an interior wall to edit.")
    wall = deepcopy(previous) if previous else {"id": uuid.uuid4().hex}
    wall.update(room_id=room_id, axis=axis, x=x, y=y, length=length,
                openings=deepcopy(openings if openings is not None else []))
    if thickness is not None:
        wall["thickness"] = thickness
    if previous is None:
        partitions.append(wall)
    else:
        partitions[partitions.index(previous)] = wall
    candidate = normalize_interior(candidate)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def opening_candidate(data, partition_id, offset, width):
    """Replace the selected wall's opening; width zero makes it solid."""
    from .interiors import InteriorError, _integer, normalize_interior
    candidate = normalize_interior(data)
    wall = next((wall for wall in candidate.get("partitions", []) if wall["id"] == partition_id), None)
    if wall is None:
        raise InteriorError("Select an interior wall for the opening.")
    _integer(offset, 0, wall["length"]-1, "Place the opening within its wall.")
    _integer(width, 0, wall["length"], "Openings need a whole tile width.")
    wall["openings"] = [{"offset": offset, "width": width}] if width else []
    candidate = normalize_interior(candidate)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def remove_partition_candidate(data, partition_id):
    from .interiors import InteriorError, normalize_interior
    candidate = normalize_interior(data)
    wall = next((wall for wall in candidate.get("partitions", []) if wall["id"] == partition_id), None)
    if wall is None:
        raise InteriorError("Select an interior wall to remove.")
    candidate["partitions"].remove(wall)
    candidate = normalize_interior(candidate)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def paint_partitions(data, layers, enabled=None):
    """Use the same native tile layers for editor rendering and exported maps."""
    active = {room["id"] for room in data["rooms"] if room["enabled"]} if enabled is None else set(enabled)
    rooms = {room["id"]: room for room in data["rooms"]}
    width, height = data["width"], data["height"]
    frame = data.get("room_frame", {})
    def put(layer, x, y, tile):
        if 0 <= x < width and 0 <= y < height:
            layers[layer][y*width+x] = tile + 1
    # Horizontal faces rise above their collision base in Front. Vertical
    # structural trim draws last to finish corner and T-shaped junctions.
    walls = sorted(data.get("partitions", []), key=lambda wall: wall["axis"] == "vertical")
    for wall in walls:
        if wall["room_id"] not in active or wall.get("thickness", 1) > 1:
            continue
        room = rooms[wall["room_id"]]
        style = {**data["style"], **data.get("room_styles", {}).get(room["id"], {})}
        gaps = {index for gap in wall["openings"] for index in range(gap["offset"], gap["offset"]+gap["width"])}
        for index, (x, y) in enumerate(partition_span(wall)):
            if index in gaps:
                continue
            put("Buildings", x, y, data["atlas"]["tile_count"])
            at_start = index == 0 or index-1 in gaps
            at_end = index == wall["length"]-1 or index+1 in gaps
            if wall["axis"] == "horizontal":
                pattern = style.get("wall_pattern")
                for row, key in enumerate(("wall_top", "wall_middle", "wall_bottom")):
                    tile = pattern["tiles"][row * pattern["width"] + (x-room["x"]) % pattern["width"]] if pattern else style[key]
                    if (at_start or at_end) and "partition_vertical" in frame:
                        tile = frame["partition_vertical"]
                    elif row == 0 and "partition_cap" in frame:
                        tile = frame["partition_cap"]
                    put("Front", x, y-2+row, tile)
            else:
                if "partition_vertical" in frame:
                    tile = frame.get("partition_cap", frame["partition_vertical"]) if at_start or at_end else frame["partition_vertical"]
                else:
                    role = "top_left" if at_start else "bottom_left_outer" if at_end else "left"
                    tile = frame.get(role, style["wall_middle"])
                put("Front", x, y, tile)


def opening_approaches(data, enabled=None):
    """Tiles explicitly reserved for passing through authored wall openings."""
    active = {room["id"] for room in data["rooms"] if room["enabled"]} if enabled is None else set(enabled)
    points = set()
    for wall in data.get("partitions", []):
        if wall["room_id"] not in active:
            continue
        dx, dy = (0, 1) if wall["axis"] == "horizontal" else (1, 0)
        for gap in wall["openings"]:
            cells = _rectangle_cells(partition_opening_rectangle(wall, gap))
            points.update(cells)
            points.update((x-dx, y-dy) for x, y in cells)
            points.update((x+dx, y+dy) for x, y in cells)
    return points
