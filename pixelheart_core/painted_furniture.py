"""Repaint furniture from the game library as a new item owned by the NPC's pack.

The painted texture keeps the game sheet's width and the piece's columns. Only
whole rows above the piece are cut, so the sprite index moves by whole rows and
every rectangle the game computes for rotations, lamp states and animation
frames still lands on the painted pixels. The game's own furniture is never
changed.

Companion library sheets are the original texture beside its mirror image;
rotations the game draws flipped use the mirror half. Painted previews are
rebuilt the same way, while the exported texture is the original half alone.
Seats also get a front texture (the game loads ``<texture>Front``) that keeps
painted pixels wherever the game's front sheet has pixels.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
from pathlib import Path
from threading import Lock

from PIL import Image

from .interior_furniture import (
    FurnitureValidationError, MAX_TEXTURE_PIXELS, _contained, _read_regular, _read_texture, _store_texture,
    MAX_TEXTURE_BYTES, validate_definition,
)
from .pixel_sheets import SheetError, encode_png, open_png

LOCAL_PREFIX = "(F)Pixelheart.Painted."
SEAT_KINDS = frozenset({"chair", "bench", "couch", "armchair"})
_UNPAINTABLE_KINDS = {
    "bed": "Beds", "bed double": "Beds", "bed child": "Beds",
    "fishtank": "Fish tanks", "randomized_plant": "Plants that change their look",
}
_TELEVISIONS = frozenset({"(F)1466", "(F)1468", "(F)1680", "(F)2326", "(F)RetroTV"})
_PLACEMENTS = {"default": -1, "indoors": 0, "outdoors": 1, "both": 2}
_SHEET_FACTS = OrderedDict()
_SHEET_FACTS_LOCK = Lock()


class PaintedFurnitureError(ValueError):
    """A piece cannot be painted, or a painting does not fit the piece."""


def _all_frames(definition):
    frames = list(definition["frames"])
    for variant in definition.get("preview_variants", {}).values():
        frames.extend(variant)
    return frames


def _sheet(definition, project_root):
    _, image = _read_texture(_contained(project_root, definition["preview_asset"]))
    return image


def _is_mirrored(image):
    if image.width % 2:
        return False
    half = image.width // 2
    left = image.crop((0, 0, half, image.height))
    right = image.crop((half, 0, image.width, image.height)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return left.tobytes() == right.tobytes()


def _sheet_facts(definition, project_root):
    """Whether a sheet is a mirrored library sheet, and its original width and height.

    Selecting a piece asks this often; the answer is cached by file identity.
    """
    path = _contained(project_root, definition["preview_asset"])
    status = path.stat()
    key = (str(path), status.st_mtime_ns, status.st_size)
    with _SHEET_FACTS_LOCK:
        facts = _SHEET_FACTS.get(key)
        if facts is not None:
            _SHEET_FACTS.move_to_end(key)
            return facts
    with _sheet(definition, project_root) as image:
        facts = (_is_mirrored(image), image.width // 2, image.height)
    with _SHEET_FACTS_LOCK:
        _SHEET_FACTS[key] = facts
        while len(_SHEET_FACTS) > 32:
            _SHEET_FACTS.popitem(last=False)
    return facts


def _original_rect(rect, width):
    """A frame's rectangle on the original half; flipped views come from the mirror."""
    x, y, w, h = rect
    if x >= width:
        x = 2 * width - x - w
    return x, y, w, h


def paintable(definition, project_root):
    """Why this piece can't be painted, or None when it can."""
    try:
        definition = validate_definition(definition)
    except FurnitureValidationError as exc:
        return str(exc)
    if definition["kind"] in _UNPAINTABLE_KINDS:
        return f"{_UNPAINTABLE_KINDS[definition['kind']]} can't be painted yet."
    if definition["id"] in _TELEVISIONS:
        return "Televisions can't be painted yet."
    if not definition["frames"] or not definition["preview_asset"]:
        return "This piece has no picture from your game library to paint."
    if definition["sprite_size"] is None or definition["footprint"] is None:
        return "This piece's size isn't known, so it can't be painted."
    try:
        mirrored, width, height = _sheet_facts(definition, project_root)
    except (FurnitureValidationError, OSError):
        return "This piece's picture is missing. Refresh your game library, then try again."
    if not mirrored:
        return "Only furniture from your connected game library can be painted."
    for frame in _all_frames(definition):
        x, y, w, h = frame["rect"]
        if x % 16 or y % 16 or w % 16 or h % 16:
            return "This piece doesn't line up with the game's 16-pixel grid, so it can't be painted."
        if x < width < x + w or x + w > 2 * width or y + h > height:
            return "This piece's picture doesn't fit its sheet, so it can't be painted."
    rotation_zero = next((frame for frame in definition["frames"] if frame["rotation"] == 0), None)
    if rotation_zero is None or rotation_zero["rect"][0] >= width:
        return "This piece's picture doesn't fit its sheet, so it can't be painted."
    return None


def painting_box(definition, width):
    """The smallest box around every frame of the piece, on the original half."""
    rects = [_original_rect(frame["rect"], width) for frame in _all_frames(definition)]
    return (min(x for x, _, _, _ in rects), min(y for _, y, _, _ in rects),
            max(x + w for x, _, w, _ in rects), max(y + h for _, y, _, h in rects))


def _checked(definition, project_root):
    reason = paintable(definition, project_root)
    if reason:
        raise PaintedFurnitureError(reason)
    return validate_definition(definition)


def painting_canvas(definition, project_root):
    """The picture the painter opens: the piece's own frames, neighbors cleared.

    A painted piece's canvas is exactly its band, so saved layers match again.
    """
    definition = _checked(definition, project_root)
    with _sheet(definition, project_root) as sheet:
        width = sheet.width // 2
        box = painting_box(definition, width)
        crop = sheet.crop(box)
    if "painted_from" in definition:
        return crop
    keep = Image.new("L", crop.size, 0)
    for x, y, w, h in {_original_rect(tuple(frame["rect"]), width) for frame in _all_frames(definition)}:
        keep.paste(255, (x - box[0], y - box[1], x - box[0] + w, y - box[1] + h))
    canvas = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    canvas.paste(crop, (0, 0), keep)
    crop.close()
    return canvas


def _shift_frames(frames, top):
    return [{**frame, "rect": [frame["rect"][0], frame["rect"][1] - top, *frame["rect"][2:]]} for frame in frames]


def _shift_lights(lights, top, bottom):
    """Keep lights whose masks lie in the band; drop masks (or overlays) outside it."""
    result = []
    for light in lights:
        light = deepcopy(light)
        mask = light.get("mask_rect")
        if mask is not None:
            if top <= mask[1] and mask[1] + mask[3] <= bottom:
                light["mask_rect"] = [mask[0], mask[1] - top, mask[2], mask[3]]
            elif light.get("blend") == "overlay":
                continue
            else:
                light.pop("mask_rect")
                light.pop("mask_channel", None)
        result.append(light)
    return result


def build_painted(definition, canvas_png, project_root, *, front_sheet=None, price=None):
    """Store a painting as a new furniture definition beside the piece it came from.

    ``front_sheet`` is the game's ``<texture>Front`` image in the same
    coordinates as the source definition's original half (for a painted piece,
    already cut by its row offset). ``price`` defaults to the original's.
    """
    source = _checked(definition, project_root)
    with _sheet(source, project_root) as sheet:
        width = sheet.width // 2
    left, top, right, bottom = painting_box(source, width)
    try:
        canvas = open_png(canvas_png)
    except SheetError as exc:
        raise PaintedFurnitureError(str(exc)) from exc
    with canvas:
        if canvas.size != (right - left, bottom - top):
            raise PaintedFurnitureError("The painted picture doesn't match this piece's size.")
        band_height = bottom - top
        if width * 2 * band_height > MAX_TEXTURE_PIXELS:
            raise PaintedFurnitureError("This piece's sheet is too large to paint.")
        band = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
        band.paste(canvas, (left, 0))
        front = None
        if source["kind"] in SEAT_KINDS:
            front = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
            if front_sheet is not None and front_sheet.width >= right and front_sheet.height >= bottom:
                with front_sheet.crop((left, top, right, bottom)).convert("RGBA") as region:
                    mask = region.getchannel("A").point(lambda value: 255 if value else 0)
                front.paste(canvas, (left, 0), mask)
    texture = encode_png(band)
    digest = hashlib.sha256(texture).hexdigest()[:12]
    atlas = Image.new("RGBA", (width * 2, band_height), (0, 0, 0, 0))
    atlas.paste(band, (0, 0))
    with band.transpose(Image.Transpose.FLIP_LEFT_RIGHT) as mirror:
        atlas.paste(mirror, (width, 0))
    band.close()
    try:
        preview_asset = _store_texture(encode_png(atlas), project_root)
        front_asset = _store_texture(encode_png(front), project_root) if front is not None else ""
    finally:
        atlas.close()
        if front is not None:
            front.close()
    origin = source.get("painted_from")
    x0, y0 = next(frame for frame in source["frames"] if frame["rotation"] == 0)["rect"][:2]
    columns = width // 16
    if origin is None:
        origin = {"id": source["id"], "texture": source["texture"], "sprite_index": y0 // 16 * columns + x0 // 16,
                  "row_offset": 0, "price": 0}
    origin = {**origin, "row_offset": origin["row_offset"] + top // 16}
    if price is not None:
        origin["price"] = price
    painted = deepcopy(source)
    painted.update(
        id=LOCAL_PREFIX + digest,
        name=source["name"] if "painted_from" in source else ("Painted " + source["name"])[:256],
        sprite_index=(y0 - top) // 16 * columns + x0 // 16,
        texture=f"Pixelheart/Painted/{digest}",
        preview_asset=preview_asset,
        frames=_shift_frames(source["frames"], top),
        dependency="",
        painted_from=origin,
    )
    if "preview_variants" in source:
        painted["preview_variants"] = {key: _shift_frames(frames, top) for key, frames in source["preview_variants"].items()}
    if "preview_lights" in source:
        painted["preview_lights"] = _shift_lights(source["preview_lights"], top, bottom)
    if front_asset:
        painted["front_asset"] = front_asset
    else:
        painted.pop("front_asset", None)
    return validate_definition(painted)


def painted_texture_png(definition, project_root):
    """The texture the game loads: the original half of the painted preview."""
    definition = validate_definition(definition)
    with _sheet(definition, project_root) as sheet:
        with sheet.crop((0, 0, sheet.width // 2, sheet.height)) as band:
            return encode_png(band)


def painted_front_png(definition, project_root):
    definition = validate_definition(definition)
    if not definition.get("front_asset"):
        return None
    return _read_regular(_contained(project_root, definition["front_asset"]), MAX_TEXTURE_BYTES, "front texture")


def local_hex(definition):
    identity = definition["id"]
    if not identity.startswith(LOCAL_PREFIX):
        raise PaintedFurnitureError("This isn't a painted piece.")
    return identity[len(LOCAL_PREFIX):]


def is_painted(definition):
    return bool(definition.get("painted_from")) and definition["id"].startswith(LOCAL_PREFIX)


def exported_furniture_id(definition, npc_id):
    """The qualified item ID the exported pack gives this painted piece."""
    return f"(F){npc_id}_Furniture_{local_hex(definition)}"


def texture_asset(definition, npc_id):
    return f"Mods/{npc_id}/Furniture/{local_hex(definition)}"


def native_record(definition, npc_id):
    """The ``Data/Furniture`` key and slash-delimited record for a painted piece."""
    definition = validate_definition(definition)
    key = exported_furniture_id(definition, npc_id)[3:]
    sprite_width, sprite_height = definition["sprite_size"]
    footprint_width, footprint_height = definition["footprint"]
    kind = definition["kind"].replace("/", " ")
    name = definition["name"].replace("/", "-")
    fields = [key, kind, f"{sprite_width} {sprite_height}", f"{footprint_width} {footprint_height}",
              str(definition["rotations"]), str(definition["painted_from"]["price"]),
              str(_PLACEMENTS[definition["placement"]]), name, str(definition["sprite_index"]),
              # Slashes separate the record's fields, so the asset name uses backslashes.
              texture_asset(definition, npc_id).replace("/", "\\"), "true"]
    return key, "/".join(fields)


def game_extras(content_root, definition):
    """The game's front sheet (seats only) and price for the piece a painting starts from.

    Either may be None: modded furniture, or a game that isn't connected.
    """
    if content_root is None:
        return None, None
    try:
        # The game's readers compare fully resolved paths (macOS folders can be symlinks).
        content_root = Path(content_root).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, None
    from .game_scene_assets import asset_path, load_texture
    from .local_templates import _read_bounded
    from .xnb_preview import MAX_PACKED, string_dictionary
    definition = validate_definition(definition)
    origin = definition.get("painted_from") or {"id": definition["id"], "texture": definition["texture"], "row_offset": 0}
    front = price = None
    if definition["kind"] in SEAT_KINDS:
        try:
            image, _, _ = load_texture(content_root, origin["texture"] + "Front")
            offset = origin["row_offset"] * 16
            front = image.crop((0, offset, image.width, image.height)) if offset else image
        except (OSError, ValueError, RuntimeError):
            front = None
    if "painted_from" not in definition:
        try:
            payload = _read_bounded(content_root, asset_path(content_root, "Data/Furniture"), MAX_PACKED, None)
            record = string_dictionary(payload, maximum=10_000).get(origin["id"][3:])
            price = int(record.split("/")[5]) if record else None
            if price is not None and price < 0:
                price = None
        except (OSError, ValueError, IndexError, RuntimeError):
            price = None
    return front, price
