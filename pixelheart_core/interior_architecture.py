"""Portable, static architectural map pieces assembled from the user's artwork.

Pieces retain separate Back, Buildings and Front tiles. A Buildings tile is a
physical blocker; no source-map actions, warps or gameplay behavior are copied.
Coordinates identify the upper-left corner of the artwork, in 16-pixel tiles.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import re
import uuid

from PIL import Image

LAYERS = ("Back", "Buildings", "Front")
MAX_PIECES = 2048


def validate_architecture_definition(value, tile_count=None, *, external=False):
    from .interiors import InteriorError, _integer, _text
    if not isinstance(value, dict):
        raise InteriorError("Each architectural piece needs a definition.")
    identity = _text(value.get("id"), 260, "Architectural pieces need a short identity.")
    result = {"id": identity,
              "name": _text(value.get("name"), 256, "Give the architectural piece a name."),
              "category": _text(value.get("category", "Fixtures"), 80, "Choose an architectural category."),
              "placement": value.get("placement")}
    from .interior_architecture_rules import RULES
    if "rules" in value:
        if not isinstance(value["rules"], str) or value["rules"] not in RULES:
            raise InteriorError("Choose a supported architectural placement rule.")
        result["rules"] = value["rules"]
    if result["placement"] not in ("floor", "wall"):
        raise InteriorError("Architectural pieces belong on the floor or north wall.")
    for key in ("width", "height"):
        result[key] = _integer(value.get(key), 1, 32, "Architectural pieces must be 1–32 tiles wide and high.")
    if external:
        from .world import relative_path, WorldError
        reference = _text(value.get("preview_asset"), 1024, "An architectural piece needs its local PNG atlas.")
        try:
            relative_path(reference)
        except WorldError as exc:
            raise InteriorError(str(exc)) from exc
        result.update(preview_asset=reference,
                      columns=_integer(value.get("columns"), 1, 256, "Invalid architectural atlas columns."),
                      tile_count=_integer(value.get("tile_count"), 1, 65536, "Invalid architectural atlas tile count."))
        tile_count = result["tile_count"]
        if tile_count % result["columns"]:
            raise InteriorError("The architectural atlas must contain complete rows of tiles.")
    if type(tile_count) is not int or tile_count < 1:
        raise InteriorError("Import the architectural artwork before using a piece.")
    layers = value.get("layers")
    if not isinstance(layers, dict) or set(layers) - set(LAYERS):
        raise InteriorError("Architectural pieces use Back, Buildings and Front tile layers.")
    result["layers"] = {}
    occupied = False
    for layer in LAYERS:
        tiles = layers.get(layer, [None] * (result["width"] * result["height"]))
        if not isinstance(tiles, list) or len(tiles) != result["width"] * result["height"]:
            raise InteriorError("Every architectural layer must match the piece's dimensions.")
        for tile in tiles:
            if tile is not None:
                _integer(tile, 0, tile_count-1, "An architectural tile is outside its atlas.")
                occupied = True
        result["layers"][layer] = list(tiles)
    if not occupied:
        raise InteriorError("An architectural piece needs at least one artwork tile.")
    return result


def architecture_bounds(item, definition):
    return item["x"], item["y"], definition["width"], definition["height"]


def piece_cells(item, definition, layers=LAYERS):
    return {(item["x"] + index % definition["width"], item["y"] + index // definition["width"])
            for layer in layers for index, tile in enumerate(definition["layers"][layer]) if tile is not None}


def architecture_cells(data, enabled=None, *, layers=("Buildings",)):
    active = ({room["id"] for room in data["rooms"] if room["enabled"]}
              if enabled is None else set(enabled))
    definitions = {piece["id"]: piece for piece in data.get("architecture_catalog", [])}
    return set().union(*(piece_cells(item, definitions[item["piece_id"]], layers)
                        for item in data.get("architecture", []) if item["room_id"] in active))


def _regions(data):
    from .interiors import room_cells
    from .interior_layout import partition_span, shell_floor_cells, shell_wall_regions
    enabled = {room["id"] for room in data["rooms"]}
    all_floor = shell_floor_cells(data, enabled)
    walls, _ = shell_wall_regions(data, enabled)
    regions = {}
    for room in data["rooms"]:
        floor = room_cells(room) & all_floor
        wall = walls[room["id"]]
        for partition in data.get("partitions", []):
            if (partition["room_id"] != room["id"] or partition["axis"] != "horizontal"
                    or partition.get("thickness", 1) > 1):
                continue
            gaps = {index for gap in partition["openings"]
                    for index in range(gap["offset"], gap["offset"] + gap["width"])}
            wall.update((x, y-offset) for index, (x, y) in enumerate(partition_span(partition))
                        if index not in gaps for offset in (0, 1, 2))
        regions[room["id"]] = (floor, wall)
    return regions


def validate_architecture(data):
    """Validate all definitions and placements, including dormant optional rooms."""
    from .interiors import InteriorError, _integer, _text
    from .interior_layout import partition_cells
    catalog = data.get("architecture_catalog", [])
    if not isinstance(catalog, list) or len(catalog) > MAX_PIECES:
        raise InteriorError("Use an architectural library with up to 2048 pieces.")
    normalized = [validate_architecture_definition(piece, data["atlas"]["tile_count"]) for piece in catalog]
    definitions = {piece["id"]: piece for piece in normalized}
    if len(definitions) != len(normalized):
        raise InteriorError("Architectural library identities must be unique.")
    if "architecture_catalog" in data:
        data["architecture_catalog"] = normalized
    placements = data.get("architecture", [])
    if not isinstance(placements, list) or len(placements) > 512:
        raise InteriorError("An interior supports up to 512 architectural pieces.")
    seen, occupied = set(), set()
    regions = _regions(data)
    partitions = partition_cells(data, {room["id"] for room in data["rooms"]})
    for item in placements:
        if not isinstance(item, dict):
            raise InteriorError("Architectural placements must be objects.")
        identity = _text(item.get("id"), 80, "Architectural placements need unique short IDs.")
        if identity in seen or not re.fullmatch(r"[A-Za-z0-9_.-]+", identity):
            raise InteriorError("Architectural placements need stable, unique IDs.")
        seen.add(identity)
        piece_id, room_id = item.get("piece_id"), item.get("room_id")
        if not isinstance(piece_id, str) or piece_id not in definitions:
            raise InteriorError("Choose an architectural piece from the library.")
        if not isinstance(room_id, str) or room_id not in regions:
            raise InteriorError("Each architectural piece must belong to an existing room.")
        for key in ("x", "y"):
            _integer(item.get(key), 0, 95, "Architectural positions use whole tile coordinates.")
        definition = definitions[piece_id]
        if item["x"] + definition["width"] > data["width"] or item["y"] + definition["height"] > data["height"]:
            raise InteriorError("Keep the whole architectural piece inside the canvas.")
        cells = piece_cells(item, definition)
        floor, wall = regions[room_id]
        from .interior_architecture_rules import architecture_rule
        cap = {(tx, ty-1) for tx, ty in wall if (tx, ty-1) not in wall and (tx, ty-1) not in floor and ty > 0}
        allowed = floor | wall | (cap if architecture_rule(definition) == "hearth" else set())
        if not cells <= allowed:
            raise InteriorError("Keep the architectural piece within one room's floor and north wall.")
        if definition["placement"] == "wall" and not cells & wall:
            raise InteriorError("Place this architectural piece against its room's north wall.")
        if definition["placement"] == "floor" and not cells & floor:
            raise InteriorError("Place this architectural piece on its room's floor.")
        wall_mounted = definition["placement"] == "wall" or architecture_rule(definition) in {"wall_backed", "hearth", "column", "counter_end"}
        if cells & partitions and (not wall_mounted or not cells & partitions <= wall):
            raise InteriorError("Move the architectural piece clear of the interior wall.")
        from .interior_layout import partition_opening_rectangle
        for partition in data.get("partitions", []):
            gap_cells = set()
            for gap in partition["openings"]:
                gx, gy, gw, gh = partition_opening_rectangle(partition, gap, visual=True)
                gap_cells.update((tx, ty) for ty in range(gy, gy+gh) for tx in range(gx, gx+gw))
            if cells & gap_cells:
                raise InteriorError("Keep architectural artwork clear of interior openings.")
        if cells & occupied:
            raise InteriorError("Architectural pieces cannot overlap one another.")
        occupied.update(cells)
    if placements and not architecture_walkable_connected(data):
        # Storage must still open spouse designs accepted by the old bottom-
        # entry rule. Editing and export enforce actual farmhouse access via
        # validate_spouse_access; this fallback exists only for legacy loading.
        if (data["kind"] != "spouse"
                or not _architecture_walkable_from(data, None, {tuple(data["entry"])})):
            raise InteriorError("Keep a walkable route between rooms around architectural pieces.")


def architecture_walkable_connected(data, enabled=None):
    """Allow enclosed air under tall artwork, while keeping usable floor joined.

    Native sinks and counters differ in their upper collision rows. A sink
    between solid counters can enclose a passable tile hidden under its Front
    artwork; that is not an inaccessible room. Front tiles remain traversable
    during the flood fill, so passages beneath arches still connect normally.
    Spouse floor sections may connect through separate farmhouse openings.
    """
    from .interiors import spouse_entrance_tiles
    entrances = spouse_entrance_tiles(data) if data["kind"] == "spouse" else {tuple(data["entry"])}
    return _architecture_walkable_from(data, enabled, entrances)


def _architecture_walkable_from(data, enabled, entrances):
    from .interiors import floor_cells
    from .interior_layout import partition_cells
    walkable = floor_cells(data, enabled) - partition_cells(data, enabled) - architecture_cells(data, enabled)
    seen = entrances & walkable
    queue = list(seen)
    while queue:
        x, y = queue.pop()
        for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if point in walkable and point not in seen:
                seen.add(point)
                queue.append(point)
    return bool(seen) and walkable - architecture_cells(data, enabled, layers=("Front",)) <= seen


def architecture_candidate(data, piece_id, x, y, *, placement_id=None, room_id=None):
    from .interiors import InteriorError, normalize_interior, validate_spouse_access
    candidate = normalize_interior(data)
    definition = next((piece for piece in candidate.get("architecture_catalog", []) if piece["id"] == piece_id), None)
    if definition is None:
        raise InteriorError("Choose an architectural piece from the library.")
    placements = candidate.setdefault("architecture", [])
    previous = next((piece for piece in placements if piece["id"] == placement_id), None)
    if placement_id is not None and previous is None:
        raise InteriorError("Select an architectural piece to move.")
    from .interiors import _integer
    for coordinate in (x, y):
        _integer(coordinate, 0, 95, "Architectural positions use whole tile coordinates.")
    if previous is not None:
        owner = next(room for room in candidate["rooms"] if room["id"] == previous["room_id"])
        if owner.get("kind") == "stairway":
            if y != previous["y"] or piece_id != previous["piece_id"]:
                raise InteriorError("Slide the connected stairs horizontally between their linked rooms; their height is fixed by the level connection.")
            owner["x"] += x-previous["x"]
            room_id = owner["id"]
    item = dict(id=previous["id"] if previous else uuid.uuid4().hex, piece_id=piece_id, x=x, y=y, room_id=room_id)
    if room_id is None:
        cells = piece_cells(item, definition)
        from .interior_architecture_rules import architecture_rule
        active = {room["id"] for room in candidate["rooms"] if room["enabled"]}
        possible = [identity for identity, (floor, wall) in _regions(candidate).items()
                    if identity in active and cells <= floor | wall | ({(tx, ty-1) for tx, ty in wall if (tx, ty-1) not in wall and (tx, ty-1) not in floor and ty > 0} if architecture_rule(definition) == "hearth" else set())
                    and cells & (wall if definition["placement"] == "wall" else floor)]
        if len(possible) != 1:
            raise InteriorError("Place the whole architectural piece in one enabled room.")
        item["room_id"] = possible[0]
    elif room_id not in {room["id"] for room in candidate["rooms"] if room["enabled"]}:
        raise InteriorError("Place architectural pieces in an enabled room.")
    if previous is None:
        placements.append(item)
    else:
        placements[placements.index(previous)] = item
    candidate = normalize_interior(candidate)
    validate_spouse_access(candidate, before=data)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def remove_architecture_candidate(data, placement_id):
    from .interiors import InteriorError, normalize_interior, validate_spouse_access
    candidate = normalize_interior(data)
    item = next((piece for piece in candidate.get("architecture", []) if piece["id"] == placement_id), None)
    if item is None:
        raise InteriorError("Select an architectural piece to remove.")
    if any(room["id"] == item["room_id"] and room.get("kind") == "stairway" for room in candidate["rooms"]):
        raise InteriorError("Remove the linked raised room to remove its stairway and steps together.")
    candidate["architecture"].remove(item)
    candidate = normalize_interior(candidate)
    validate_spouse_access(candidate, before=data)
    from .interior_architecture_rules import validate_architecture_rules
    validate_architecture_rules(candidate, before=data)
    return candidate


def paint_architecture(data, layers, enabled=None):
    active = ({room["id"] for room in data["rooms"] if room["enabled"]}
              if enabled is None else set(enabled))
    definitions = {piece["id"]: piece for piece in data.get("architecture_catalog", [])}
    for item in data.get("architecture", []):
        if item["room_id"] not in active:
            continue
        definition = definitions[item["piece_id"]]
        for layer in LAYERS:
            for index, tile in enumerate(definition["layers"][layer]):
                if tile is not None:
                    x, y = item["x"] + index % definition["width"], item["y"] + index // definition["width"]
                    layers[layer][y * data["width"] + x] = tile + 1


def architecture_preview(definition, data, root):
    """Return a detached transparent catalogue thumbnail using staged map tiles."""
    from .interiors import InteriorError
    from .interior_furniture import _read_texture
    from .world import asset_path
    definition = validate_architecture_definition(definition, data["atlas"]["tile_count"])
    _, atlas = _read_texture(asset_path(data["atlas"]["asset"], root))
    output = Image.new("RGBA", (definition["width"] * 16, definition["height"] * 16))
    try:
        columns = data["atlas"]["columns"]
        for layer in LAYERS:
            for index, tile in enumerate(definition["layers"][layer]):
                if tile is not None:
                    x, y = tile % columns * 16, tile // columns * 16
                    with atlas.crop((x, y, x+16, y+16)) as crop:
                        output.alpha_composite(crop, (index % definition["width"] * 16, index // definition["width"] * 16))
        return output
    except Exception:
        output.close()
        raise
    finally:
        atlas.close()


def _restore_kitchen_top_row(candidate, previous, replacement):
    """Keep native kitchen bases fixed when refreshing the old cropped pieces.

    This only recognizes the original two-row crop, with identical lower
    artwork and collision. Custom replacements still use normal validation.
    The caller validates the complete candidate before writing any new atlas.
    """
    from .interior_architecture_rules import architecture_rule
    if (previous is None or replacement["id"] not in {"stardew.sink", "stardew.refrigerator"}
            or previous["width"] != 1 or replacement["width"] != 1
            or previous["height"] != 2 or replacement["height"] != 3
            or previous["placement"] != "floor" or replacement["placement"] != "floor"
            or architecture_rule(previous) != "wall_backed" or architecture_rule(replacement) != "wall_backed"
            or replacement["layers"]["Front"][0] is None
            or replacement["layers"]["Back"][0] is not None
            or replacement["layers"]["Buildings"][0] is not None
            or any(replacement["layers"][layer][1:] != previous["layers"][layer] for layer in LAYERS)):
        return
    for item in candidate.get("architecture", []):
        if item["piece_id"] == replacement["id"]:
            item["y"] -= 1


def _retire_unused_cabinet_fragment(candidate, definitions, incoming):
    """Stop offering the old half-cabinet while retaining existing placements."""
    from .interior_architecture_rules import architecture_rule
    if not any(piece["id"] == "stardew.sink" and piece["height"] == 3 for piece in incoming):
        return
    cabinet = definitions.get("stardew.wall-cabinet")
    sink = definitions["stardew.sink"]
    if (cabinet is not None and cabinet["width"] == cabinet["height"] == 1
            and cabinet["placement"] == "wall" and architecture_rule(cabinet) == "wall_art"
            and cabinet["layers"] == {"Back": [None], "Buildings": [None], "Front": [sink["layers"]["Front"][0]]}
            and not any(item["piece_id"] == cabinet["id"] for item in candidate.get("architecture", []))):
        del definitions[cabinet["id"]]


def stage_architecture_library(data, definitions, root):
    """Preflight pieces and merge their pixels into one immutable design atlas."""
    from .interiors import InteriorError, normalize_interior
    from .interior_furniture import _read_texture, FurnitureValidationError
    from .world import asset_path, _write_new_file, WorldError
    candidate = normalize_interior(data)
    if not isinstance(definitions, list) or len(definitions) > MAX_PIECES:
        raise InteriorError("Use an architectural library with up to 2048 pieces.")
    incoming = [validate_architecture_definition(piece, external=True) for piece in definitions]
    if len({piece["id"] for piece in incoming}) != len(incoming):
        raise InteriorError("Architectural library identities must be unique.")
    merged = {piece["id"]: piece for piece in candidate.get("architecture_catalog", [])}
    if len(set(merged) | {piece["id"] for piece in incoming}) > MAX_PIECES:
        raise InteriorError("Use an architectural library with up to 2048 pieces.")
    if not incoming:
        return candidate
    old_image = None
    atlas = candidate["atlas"]
    columns = atlas["columns"] if atlas["asset"] else 8
    old_count = atlas["tile_count"] if atlas["asset"] else 0
    fingerprints, additions = {}, []
    try:
        if atlas["asset"]:
            _, old_image = _read_texture(asset_path(atlas["asset"], root))
            if old_image.size != (columns*16, old_count//columns*16) or old_count % columns:
                raise InteriorError("The design's tilesheet dimensions no longer match its tile references.")
            animated = {entry["tile_id"] for entry in candidate["animations"]}
            for tile in range(old_count):
                if tile in animated:
                    continue
                x, y = tile % columns * 16, tile // columns * 16
                with old_image.crop((x, y, x+16, y+16)) as crop:
                    fingerprints.setdefault(hashlib.sha256(crop.tobytes()).digest(), tile)
        for piece in incoming:
            _, source = _read_texture(asset_path(piece["preview_asset"], root))
            try:
                if source.size != (piece["columns"]*16, piece["tile_count"]//piece["columns"]*16):
                    raise InteriorError("The architectural atlas dimensions do not match its tile references.")
                mapping = {}
                for tile in sorted({tile for layer in piece["layers"].values() for tile in layer if tile is not None}):
                    x, y = tile % piece["columns"] * 16, tile // piece["columns"] * 16
                    with source.crop((x, y, x+16, y+16)) as crop:
                        pixels = crop.tobytes()
                    digest = hashlib.sha256(pixels).digest()
                    index = fingerprints.get(digest)
                    if index is None:
                        index = old_count + len(additions)
                        if (index//columns+1)*16 > 4096:
                            raise InteriorError("These pieces would make the tilesheet taller than 4096 pixels.")
                        fingerprints[digest] = index
                        additions.append(pixels)
                    mapping[tile] = index
                staged = {key: deepcopy(value) for key, value in piece.items()
                          if key not in ("preview_asset", "columns", "tile_count")}
                staged["layers"] = {layer: [None if tile is None else mapping[tile] for tile in tiles]
                                    for layer, tiles in piece["layers"].items()}
                _restore_kitchen_top_row(candidate, merged.get(piece["id"]), staged)
                merged[piece["id"]] = staged
            finally:
                source.close()
        _retire_unused_cabinet_fragment(candidate, merged, incoming)
        candidate["architecture_catalog"] = list(merged.values())
        if not additions:
            return normalize_interior(candidate)
        rows = (old_count + len(additions) + columns-1)//columns
        with Image.new("RGBA", (columns*16, rows*16)) as output:
            if old_image is not None:
                output.paste(old_image, (0, 0))
            for offset, pixels in enumerate(additions):
                tile = old_count + offset
                with Image.frombytes("RGBA", (16, 16), pixels) as crop:
                    output.paste(crop, (tile % columns*16, tile//columns*16))
            stream = io.BytesIO()
            output.save(stream, format="PNG")
        raw = stream.getvalue()
        reference = "world_assets/interior_architecture/" + hashlib.sha256(raw).hexdigest() + ".png"
        candidate["atlas"] = {"asset": reference, "columns": columns, "tile_count": columns*rows}
        candidate = normalize_interior(candidate)
        _write_new_file(asset_path(reference, root), raw)
        return candidate
    except (FurnitureValidationError, WorldError, OSError, ValueError) as exc:
        if isinstance(exc, InteriorError):
            raise
        raise InteriorError(str(exc)) from exc
    finally:
        if old_image is not None:
            old_image.close()
