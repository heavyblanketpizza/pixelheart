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


def footprint(definition, rotation=0):
    size = definition.get("rotation_footprints", {}).get(str(rotation), definition.get("footprint"))
    if not size:
        raise InteriorError("Set the furniture's footprint before placing it.")
    return tuple(size)


WALL_FURNITURE = {"painting", "window", "sconce"}


def wall_cells(data, enabled=None):
    floor = floor_cells(data, enabled)
    return {(x, y-distance) for x, y in floor if (x, y-1) not in floor
            for distance in (1, 2, 3) if y-distance >= 0 and (x, y-distance) not in floor}


def placement_cells(item, definition):
    width, height = footprint(definition, item["rotation"])
    return {(x, y) for y in range(item["y"], item["y"] + height)
            for x in range(item["x"], item["x"] + width)}


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


def reachable_tiles(data):
    """Conservative authoring reachability from the entry, using supplied bounds."""
    definitions = {item["id"]: item for item in data["catalog"]}
    cells = floor_cells(data)
    for item in data["furniture"]:
        definition = definitions[item["item_id"]]
        if definition["kind"] != "rug":
            cells.difference_update(placement_cells(item, definition))
    entry = tuple(data["entry"])
    seen = {entry} if entry in cells else set()
    queue = deque(seen)
    while queue:
        x, y = queue.popleft()
        for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if point in cells and point not in seen:
                seen.add(point)
                queue.append(point)
    return seen


def normalize_interior(value):
    """Validate bounded, portable authoring data without resolving local assets."""
    from .interior_furniture import validate_definition, FurnitureValidationError
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
        if definition.get("placement") == "outdoors":
            raise InteriorError("Outdoor-only furniture cannot be placed in an interior.")
        cells = placement_cells(item, definition)
        region = wall_cells(data) if definition["kind"] in WALL_FURNITURE else floor
        if not cells <= region:
            raise InteriorError("Place the whole furniture footprint on its room's wall or floor.")
        if definition["kind"] != "rug":
            if solid & cells:
                raise InteriorError("Furniture footprints overlap. Rugs may go underneath furniture.")
            solid.update(cells)
    anchors = [tuple(data["entry"])]
    if data["kind"] == "spouse":
        anchors.append(tuple(data["spouse_stand"]))
    if any(anchor in solid for anchor in anchors):
        raise InteriorError("Keep the entry and spouse standing position clear.")
    if data["kind"] == "spouse":
        # Both anchors must remain mutually reachable after furnishing.
        reachable = {anchors[0]}
        queue = deque(reachable)
        while queue:
            x, y = queue.popleft()
            for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if point in floor and point not in solid and point not in reachable:
                    reachable.add(point)
                    queue.append(point)
        if anchors[1] not in reachable:
            raise InteriorError("Keep a walkable route from the entry to the spouse standing position.")
    return data


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
        self._undo.append(self.snapshot())
        del self._undo[:-50]
        self._redo.clear()
        self.data = candidate
        return True

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
        occupied = {(x, y) for x in range(max(0, room["x"]-1), room["x"]+room["width"]+1)
                    for y in range(max(0, room["y"]-3), room["y"]+room["height"]+1)}
        definitions = {d["id"]: d for d in data["catalog"]}
        if any(placement_cells(item, definitions[item["item_id"]]) & occupied for item in data["furniture"]):
            raise InteriorError("Move or remove the room's furniture before removing the room.")
        data["rooms"].remove(room)
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


def interior_asset_references(data):
    if data["atlas"]["asset"]:
        yield data["atlas"]["asset"]
    for definition in data["catalog"]:
        if definition.get("preview_asset"):
            yield definition["preview_asset"]


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
    width, height = data["width"], data["height"]
    layers = {name: [0] * (width * height) for name in ("Back", "Buildings", "Front", "Paths")}
    floor = floor_cells(data, enabled)
    style = data["style"]
    def put(layer, x, y, tile):
        if 0 <= x < width and 0 <= y < height:
            layers[layer][y * width + x] = tile + 1
    for x, y in sorted(floor):
        put("Back", x, y, style["floor"])
    for x, y in sorted(floor):
        if (x, y - 1) not in floor:
            for distance, tile in ((3, style["wall_top"]), (2, style["wall_middle"]), (1, style["wall_bottom"])):
                if (x, y-distance) not in floor:
                    put("Back", x, y-distance, tile)
                    # A transparent collision tile leaves editable Back-wall
                    # artwork visible when wallpaper changes in the game.
                    put("Buildings", x, y-distance, data["atlas"]["tile_count"])
        for nx, ny in ((x-1, y), (x+1, y), (x, y+1)):
            if (nx, ny) not in floor:
                put("Buildings", nx, ny, style["wall_bottom"])
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


def render_interior(data, project_root, elapsed_ms=0, grid=False):
    """Pixel-exact artwork preview, not a simulation of game item behavior."""
    from .interior_furniture import preview_frame, FurnitureValidationError
    data = normalize_interior(data)
    width, height = data["width"], data["height"]
    image = Image.new("RGBA", (width * 16, height * 16), "#292d30")
    draw = ImageDraw.Draw(image)
    sheet = None
    if data["atlas"]["asset"]:
        try:
            from .interior_furniture import _read_texture
            _, sheet = _read_texture(asset_path(data["atlas"]["asset"], project_root))
        except (OSError, ValueError):
            pass
    layers = map_layers(data)
    floor = floor_cells(data)
    for layer in ("Back", "Buildings"):
        for index, gid in enumerate(layers[layer]):
            if not gid:
                continue
            if layer == "Buildings" and gid == data["atlas"]["tile_count"] + 1:
                continue
            x, y = index % width * 16, index // width * 16
            if sheet is None:
                color = "#747e78" if (index % width, index // width) in floor else "#51595c"
                draw.rectangle((x, y, x+15, y+15), fill=color)
            else:
                tile = animation_tile(data, gid - 1, elapsed_ms)
                sx, sy = tile % data["atlas"]["columns"] * 16, tile // data["atlas"]["columns"] * 16
                image.alpha_composite(sheet.crop((sx, sy, sx+16, sy+16)), (x, y))
    definitions = {item["id"]: item for item in data["catalog"]}
    ordered = sorted(data["furniture"], key=lambda item: (definitions[item["item_id"]]["kind"] != "rug", item["y"] + footprint(definitions[item["item_id"]], item["rotation"])[1]))
    for item in ordered:
        definition = definitions[item["item_id"]]
        fw, fh = footprint(definition, item["rotation"])
        x, y = item["x"] * 16, item["y"] * 16
        try:
            sprite = preview_frame(definition, project_root, item["rotation"], elapsed_ms)
            image.alpha_composite(sprite, (x, y + fh * 16 - sprite.height))
        except (FurnitureValidationError, OSError):
            draw.rectangle((x, y, x+fw*16-1, y+fh*16-1), fill="#76849b", outline="#d9dfeb")
            draw.text((x+2, y+1), "?", fill="#ffffff")
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
        floor_tile = data["style"]["floor"]
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
        if not _connected(floor) or tuple(data["entry"]) not in floor:
            continue
        key = str(mask)
        map_asset = "Maps/" + identity + "_Interior_" + key
        path = prefix + "room_" + key + ".tmx"
        files[path] = interior_tmx(data, enabled=enabled, design_id=identity)
        patches.append({"Action": "Load", "Target": map_asset, "FromFile": path})
        edits = []
        for room in data["rooms"]:
            if room["id"] not in enabled:
                continue
            region = identity + "_" + room["id"]
            for x, y in sorted(room_cells(room)):
                edits.append({"Layer": "Back", "Position": {"X": x, "Y": y}, "SetProperties": {"FloorID": region}})
                if (x, y-1) not in floor and (x, y-3) not in floor:
                    edits.append({"Layer": "Back", "Position": {"X": x, "Y": y-3}, "SetProperties": {"WallID": region}})
        if data["kind"] == "spouse":
            x, y = data["spouse_stand"]
            edits.append({"Layer": "Back", "Position": {"X": x, "Y": y}, "SetProperties": {"Pixelheart.Interiors/SpouseRoom": identity}})
        patches.append({"Action": "EditMap", "Target": map_asset, "MapTiles": edits})
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
    dependencies = sorted({d["dependency"] for d in data["catalog"] if d.get("dependency") and any(f["item_id"] == d["id"] for f in data["furniture"])})
    return {"files": files, "patches": patches, "runtime": runtime,
            "map_asset": next(v["map_asset"] for v in variants if v["id"] == default),
            "dependencies": dependencies}
