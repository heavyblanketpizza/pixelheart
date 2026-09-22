"""Embed observed wall/floor patterns into a design's own immutable atlas.

The source library contains actual PNG crops resolved by the game. This module
only assembles those crops and applies their tile references; it supplies no
game artwork and does not guess texture coordinates from item IDs.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path

from PIL import Image

from .interior_furniture import FurnitureValidationError, _read_texture, validate_surface
from .interiors import InteriorError, normalize_interior
from .world import WorldError, _write_new_file, asset_path


MAX_SURFACES = 2048
MAX_ATLAS_PIXELS = 4096


def _cached_surfaces(value, tile_count):
    if not isinstance(value, list) or len(value) > MAX_SURFACES:
        raise InteriorError("Use a library with up to 2048 wall and floor patterns.")
    result, identities = [], set()
    for surface in value:
        if not isinstance(surface, dict):
            raise InteriorError("Each wall or floor pattern needs its tile references.")
        identity, kind = surface.get("id"), surface.get("kind")
        expected = (1, 3) if kind == "wall" else (2, 2) if kind == "floor" else None
        if (not isinstance(identity, str) or not identity or len(identity) > 260
                or identity in identities or expected is None
                or not identity.startswith("(WP)" if kind == "wall" else "(FL)")):
            raise InteriorError("Wall and floor patterns need unique installed item IDs.")
        identities.add(identity)
        if (type(surface.get("width")) is not int or type(surface.get("height")) is not int
                or (surface["width"], surface["height"]) != expected):
            raise InteriorError("Wallpaper patterns are 1 × 3 tiles; floors are 2 × 2 tiles.")
        tiles = surface.get("tiles")
        if (not isinstance(tiles, list) or len(tiles) != expected[0] * expected[1]
                or any(type(tile) is not int or not 0 <= tile < tile_count for tile in tiles)):
            raise InteriorError("A pattern tile is outside the design's tilesheet.")
        name, dependency = surface.get("name"), surface.get("dependency", "")
        if (not isinstance(name, str) or not name or len(name) > 256
                or not isinstance(dependency, str) or len(dependency) > 256):
            raise InteriorError("Patterns need a short name and an optional mod dependency.")
        result.append(deepcopy(surface))
    return result


def _pattern(surface):
    return {"width": surface["width"], "height": surface["height"],
            "tiles": list(surface["tiles"]), "surface_id": surface["id"]}


def _set_style(style, surface):
    kind, tiles = surface["kind"], surface["tiles"]
    style[kind + "_pattern"] = _pattern(surface)
    if kind == "wall":
        style.update(wall_top=tiles[0], wall_middle=tiles[1], wall_bottom=tiles[2])
    else:
        style["floor"] = tiles[0]


def stage_surface_library(data, surfaces, root):
    """Return a detached design with validated patterns embedded in its atlas.

    Existing columns and tile indexes never change. New, distinct 16-pixel
    tiles append in row-major order; patterns contain explicit indexes so even
    a one-column atlas works. Existing surface IDs are refreshed without
    discarding other library entries. Applied patterns retain their current
    appearance until the user chooses the refreshed swatch.

    All validation and PNG generation finish before one content-addressed
    asset is written. Callers should pass their staging directory while a
    designer is open, and only accept the resulting design on Save.
    """
    candidate = normalize_interior(data)
    if not isinstance(surfaces, list) or len(surfaces) > MAX_SURFACES:
        raise InteriorError("Use a library with up to 2048 wall and floor patterns.")
    try:
        incoming = [validate_surface(surface) for surface in surfaces]
    except FurnitureValidationError as exc:
        raise InteriorError(str(exc)) from exc
    if len({surface["id"] for surface in incoming}) != len(incoming):
        raise InteriorError("The imported wall and floor patterns contain duplicate IDs.")
    atlas = candidate["atlas"]
    existing = _cached_surfaces(candidate.get("surfaces", []), atlas["tile_count"])
    merged = {surface["id"]: surface for surface in existing}
    if len(set(merged) | {surface["id"] for surface in incoming}) > MAX_SURFACES:
        raise InteriorError("Use a library with up to 2048 wall and floor patterns.")
    if not incoming:
        return candidate

    old_image = None
    columns = atlas["columns"] if atlas["asset"] else 8
    old_count = atlas["tile_count"] if atlas["asset"] else 0
    fingerprints, additions = {}, []
    try:
        if atlas["asset"]:
            _, old_image = _read_texture(asset_path(atlas["asset"], root))
            if (old_image.width != columns * 16 or old_image.height % 16
                    or old_image.width > MAX_ATLAS_PIXELS or old_image.height > MAX_ATLAS_PIXELS
                    or old_image.width * old_image.height // 256 != old_count):
                raise InteriorError("The design's tilesheet dimensions no longer match its tile references.")
            for tile in range(old_count):
                x, y = tile % columns * 16, tile // columns * 16
                with old_image.crop((x, y, x + 16, y + 16)) as image:
                    fingerprints.setdefault(hashlib.sha256(image.tobytes()).digest(), tile)

        from .interior_furniture import preview_surface
        for surface in incoming:
            indexes = []
            with preview_surface(surface, root) as image:
                width, height = image.width // 16, image.height // 16
                for y in range(height):
                    for x in range(width):
                        with image.crop((x * 16, y * 16, x * 16 + 16, y * 16 + 16)) as tile:
                            pixels = tile.tobytes()
                        digest = hashlib.sha256(pixels).digest()
                        index = fingerprints.get(digest)
                        if index is None:
                            index = old_count + len(additions)
                            if (index // columns + 1) * 16 > MAX_ATLAS_PIXELS:
                                raise InteriorError("These patterns would make the tilesheet taller than 4096 pixels. Import fewer patterns.")
                            fingerprints[digest] = index
                            additions.append(pixels)
                        indexes.append(index)
            merged[surface["id"]] = {
                "id": surface["id"], "name": surface["name"], "kind": surface["kind"],
                "width": width, "height": height, "tiles": indexes,
                "dependency": surface.get("dependency", ""),
            }
        candidate["surfaces"] = list(merged.values())
        if not additions:
            return normalize_interior(candidate)

        rows = (old_count + len(additions) + columns - 1) // columns
        with Image.new("RGBA", (columns * 16, rows * 16)) as output:
            if old_image is not None:
                output.paste(old_image, (0, 0))
            for offset, pixels in enumerate(additions):
                tile = old_count + offset
                with Image.frombytes("RGBA", (16, 16), pixels) as image:
                    output.paste(image, (tile % columns * 16, tile // columns * 16))
            payload = io.BytesIO()
            output.save(payload, format="PNG")
        raw = payload.getvalue()
        reference = "world_assets/interior_surfaces/" + hashlib.sha256(raw).hexdigest() + ".png"
        candidate["atlas"] = {"asset": reference, "columns": columns, "tile_count": columns * rows}
        candidate = normalize_interior(candidate)
        _write_new_file(asset_path(reference, Path(root)), raw)
        return candidate
    except (FurnitureValidationError, WorldError, OSError, ValueError) as exc:
        if isinstance(exc, InteriorError):
            raise
        raise InteriorError(str(exc)) from exc
    finally:
        if old_image is not None:
            old_image.close()


def apply_surface(data, surface_id, room_id=None):
    """Apply a stored swatch to one room, or all rooms, without mutating data."""
    candidate = normalize_interior(data)
    surfaces = _cached_surfaces(candidate.get("surfaces", []), candidate["atlas"]["tile_count"])
    surface = next((entry for entry in surfaces if entry["id"] == surface_id), None)
    if surface is None:
        raise InteriorError("Choose a wallpaper or floor pattern from the library.")
    if room_id is not None and room_id not in {room["id"] for room in candidate["rooms"]}:
        raise InteriorError("Choose an existing room to decorate.")
    overrides = candidate.setdefault("room_styles", {})
    if not isinstance(overrides, dict):
        raise InteriorError("Room finishes must be stored by room.")
    if room_id is None:
        _set_style(candidate["style"], surface)
        clear = ("wall_pattern", "wall_top", "wall_middle", "wall_bottom") if surface["kind"] == "wall" else ("floor_pattern", "floor")
        for identity in list(overrides):
            if not isinstance(overrides[identity], dict):
                raise InteriorError("Room finishes must contain wallpaper or floor choices.")
            for key in clear:
                overrides[identity].pop(key, None)
            if not overrides[identity]:
                del overrides[identity]
    else:
        style = overrides.setdefault(room_id, {})
        if not isinstance(style, dict):
            raise InteriorError("Room finishes must contain wallpaper or floor choices.")
        _set_style(style, surface)
    return normalize_interior(candidate)
