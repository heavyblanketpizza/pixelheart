"""Validate and compile authored seats to Stardew's Data/ChairTiles format.

The map owns the visible chair and its collision tiles. These helpers describe
where a player sits and, only when explicitly supplied, the overlay drawn over
the seated player. A draw_tilesheet value is a game asset key, not a file path;
the caller must provide that asset separately (or use the vanilla default).
"""
from __future__ import annotations

import math
import re
from decimal import Decimal


class SeatError(ValueError):
    """A map seat cannot be represented safely by Data/ChairTiles."""


_DIRECTIONS = ("up", "right", "down", "left", "opposite")
_TYPES = ("default", "highback_chair", "custom")
_ASSET_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,191}\Z")
_OFFSETS = ("offset_x", "offset_y", "extra_height")


def _asset_key(value):
    """Return an extensionless game asset key safe inside a slash-delimited row."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise SeatError("Use a nonempty game asset name of at most 512 characters.")
    # Accept ordinary author-facing asset paths and the documented doubled
    # backslashes, then emit the ChairTiles representation below.
    parts = re.sub(r"\\{1,2}", "/", value).split("/")
    if any(part in ("", ".", "..") or not _ASSET_SEGMENT.fullmatch(part) for part in parts):
        raise SeatError("Use a relative game asset name without parent paths, spaces, or tokens.")
    if parts[-1].lower().endswith((".png", ".xnb", ".tsx", ".tmx")):
        raise SeatError("Use the overlay's game asset name without a file extension.")
    return "\\\\".join(parts)


def seat_structure_issues(seat):
    """Return relative field errors for seat options; do not mutate the input.

Identity, uniqueness, and the x/y anchor remain part of world feature validation.
Legacy seats need only their existing direction. A custom seat requires all
three offset values; omitted values must not silently imply a correct pose.
"""
    issues = []

    def add(field, message):
        issues.append({"level": "error", "field": field, "message": message})

    if not isinstance(seat, dict):
        add("", "Each seat must be an object.")
        return issues
    if seat.get("direction") not in _DIRECTIONS:
        add("direction", "Choose a seat direction: up, right, down, left, or opposite.")
    for field in ("width", "height"):
        value = seat.get(field, 1)
        if type(value) is not int or not 1 <= value <= 256:
            add(field, "Seat dimensions must be whole numbers from 1 to 256 tiles.")
    seat_type = seat.get("seat_type", "default")
    if seat_type not in _TYPES:
        add("seat_type", "Choose default, highback_chair, or custom seating.")
    for field in _OFFSETS:
        if seat_type == "custom":
            value = seat.get(field)
            lower = 0 if field == "extra_height" else -16
            if type(value) not in (int, float) or not lower <= value <= 16 or not math.isfinite(value):
                add(field, f"Custom seating requires a finite {field} value from {lower} to 16 tiles.")
        elif field in seat:
            add(field, "Offsets and extra height are only used with custom seating.")

    has_x, has_y = "draw_x" in seat, "draw_y" in seat
    if has_x != has_y:
        add("draw_x" if not has_x else "draw_y", "Supply both overlay tile coordinates, or omit both.")
    draw_x, draw_y = seat.get("draw_x", -1), seat.get("draw_y", -1)
    valid_x = type(draw_x) is int and -1 <= draw_x <= 4095
    valid_y = type(draw_y) is int and -1 <= draw_y <= 4095
    for field, valid in (("draw_x", valid_x), ("draw_y", valid_y)):
        if not valid:
            add(field, "Overlay tile coordinates must be whole numbers from 0 to 4095, or both -1 for no overlay.")
    has_overlay = valid_x and valid_y and draw_x >= 0 and draw_y >= 0
    if valid_x and valid_y and (draw_x == -1) != (draw_y == -1):
        add("draw_x", "Set both overlay coordinates to -1 to disable drawing.")
    if seat_type == "highback_chair" and not has_overlay:
        add("draw_x", "A high-backed chair requires explicit overlay tile coordinates.")
    elif seat_type == "highback_chair" and draw_y == 0:
        add("draw_y", "A high-backed overlay needs a tile above its bottom-left draw tile.")
    if seat_type == "highback_chair" and seat.get("height", 1) != 1:
        add("height", "High-backed seating currently supports a one-tile seat height.")
    if seat_type == "custom" and has_overlay and seat.get("extra_height") != 0:
        add("extra_height", "Custom seating overlays currently require extra_height 0; nonzero overlay geometry is not verified.")
    if type(seat.get("is_seasonal", False)) is not bool:
        add("is_seasonal", "Seasonal seating must be true or false.")
    elif seat.get("is_seasonal", False) and not has_overlay:
        add("is_seasonal", "Seasonal seating requires explicit overlay tiles for all four seasons.")
    if "draw_tilesheet" in seat:
        try:
            _asset_key(seat["draw_tilesheet"])
        except SeatError as exc:
            add("draw_tilesheet", str(exc))
        if not has_overlay:
            add("draw_tilesheet", "An alternate tilesheet requires explicit overlay tile coordinates.")
    return issues


def _validate(seat):
    issues = seat_structure_issues(seat)
    if issues:
        raise SeatError("; ".join(item["message"] for item in issues))


def _number(value):
    # Avoid exponent notation, nonfinite numbers, and negative zero in game data.
    if value == 0:
        return "0"
    text = format(Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def compile_seat_definition(seat):
    """Return one Data/ChairTiles value after validating all authored options.

The caller resolves the key from the actual Buildings tile's tilesheet and
coordinates. This function intentionally does not guess an overlay asset or
offset from the chair image. Legacy values retain their exact output.
"""
    _validate(seat)
    seat_type = seat.get("seat_type", "default")
    if seat_type == "custom":
        seat_type += " " + " ".join(_number(seat[field]) for field in _OFFSETS)
    fields = [str(seat.get("width", 1)), str(seat.get("height", 1)), seat["direction"], seat_type,
              str(seat.get("draw_x", -1)), str(seat.get("draw_y", -1)),
              "true" if seat.get("is_seasonal", False) else "false"]
    if "draw_tilesheet" in seat:
        fields.append(_asset_key(seat["draw_tilesheet"]))
    return "/".join(fields)


def validate_seat_geometry(seat, *, map_width, map_height, draw_columns=None, draw_rows=None):
    """Validate the seat footprint and, when known, overlay sheet bounds.

    The x/y anchor must refer to the Buildings layer; checking that tile and any
    desired footprint collision policy is the map caller's responsibility. Overlay
    dimensions must describe the asset actually drawn, not the chair's map sheet.
    Omitting them leaves overlay asset existence and bounds explicitly unverified.
    Extended custom overlay heights are rejected until their rectangle handling
    can be checked against the game runtime.
"""
    _validate(seat)
    for name, value in (("map_width", map_width), ("map_height", map_height)):
        if type(value) is not int or value < 1:
            raise SeatError(f"{name} must be a positive whole number.")
    for name in ("x", "y"):
        if type(seat.get(name)) is not int or seat[name] < 0:
            raise SeatError("Seat anchors must be nonnegative whole tile coordinates.")
    width, height = seat.get("width", 1), seat.get("height", 1)
    if seat["x"] + width > map_width or seat["y"] + height > map_height:
        raise SeatError("The seat footprint extends outside the map.")
    if (draw_columns is None) != (draw_rows is None):
        raise SeatError("Supply both overlay sheet dimensions, or omit both.")
    if draw_columns is None:
        return
    if any(type(value) is not int or value < 1 for value in (draw_columns, draw_rows)):
        raise SeatError("Overlay sheet dimensions must be positive whole tile counts.")
    draw_x, draw_y = seat.get("draw_x", -1), seat.get("draw_y", -1)
    if draw_x == -1:
        return
    overlay_width = width * (4 if seat.get("is_seasonal", False) else 1)
    # highback_chair draws an additional row above the specified draw tile.
    top = draw_y - (1 if seat.get("seat_type") == "highback_chair" else 0)
    if top < 0 or draw_x + overlay_width > draw_columns or draw_y + height > draw_rows:
        raise SeatError("The seating overlay extends outside its tilesheet.")
