"""Illustrative farmhouse framing around a spouse-room preview.

The editable insert stays six by nine tiles. These separate preview layers
provide its surrounding farmhouse context; they are not an in-game rendering
promise and never become part of the exported spouse-room map.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, UnidentifiedImageError

from .world import WorldError, asset_path, _read_asset


SPOUSE_CONTEXT_SIZE = (9, 11)
SPOUSE_CONTEXT_ORIGIN = (2, 1)
SPOUSE_CONTEXT_ROOM_SIZE = (6, 9)
SPOUSE_CONTEXT_PIXEL_SIZE = tuple(size * 16 for size in SPOUSE_CONTEXT_SIZE)


def spouse_context_asset_refs(data):
    """Yield the optional, normalized project-relative context PNG paths."""
    if data.get("spouse_context") is None:
        return
    from .interior_furniture import validate_spouse_context
    context = validate_spouse_context(data["spouse_context"])
    yield context["background_asset"]
    yield context["foreground_asset"]


def _context_image(reference, root):
    payload = _read_asset(asset_path(reference, root))
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if (image.format != "PNG" or image.mode != "RGBA"
                    or image.size != SPOUSE_CONTEXT_PIXEL_SIZE
                    or getattr(image, "n_frames", 1) != 1):
                raise WorldError("Spouse context layers must be static 144 × 176 RGBA PNG images.")
            image.load()
            return image.copy()
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError) as exc:
        if isinstance(exc, WorldError):
            raise
        raise WorldError(f"Cannot read spouse context image: {exc}") from exc


def _schematic_layers():
    """Draw an original geometric shell without supplying any game artwork."""
    background = Image.new("RGBA", SPOUSE_CONTEXT_PIXEL_SIZE, "#292421")
    foreground = Image.new("RGBA", SPOUSE_CONTEXT_PIXEL_SIZE)
    back = ImageDraw.Draw(background)
    front = ImageDraw.Draw(foreground)

    # A small adjoining bedroom glimpse establishes the open western side.
    back.rectangle((0, 16, 31, 63), fill="#99866c")
    for x in (5, 13, 21, 29):
        back.line((x, 17, x, 62), fill="#a79378")
    back.rectangle((0, 58, 31, 63), fill="#716048")
    for y in range(64, 160, 16):
        for x in (0, 16):
            fill = "#958065" if (x // 16 + y // 16) % 2 else "#9d886c"
            back.rectangle((x, y, x + 15, y + 15), fill=fill)
            back.line((x, y + 15, x + 15, y + 15), fill="#78664f")
            back.line((x + 15, y, x + 15, y + 15), fill="#877257")

    # Ceiling, east edge and the short western pillar are fixed farmhouse
    # context. Nothing here is an editable wall within the room insert.
    back.rectangle((0, 2, 139, 15), fill="#8e7658")
    back.rectangle((0, 5, 135, 10), fill="#b5a080")
    back.line((0, 4, 134, 4), fill="#ccb797")
    back.line((0, 14, 137, 14), fill="#5c4c39")
    back.rectangle((128, 12, 141, 155), fill="#8e7658")
    back.rectangle((130, 16, 136, 151), fill="#b5a080")
    back.line((129, 17, 129, 149), fill="#ccb797")
    back.line((139, 17, 139, 154), fill="#5c4c39")
    for y in (48, 96, 144):
        back.line((130, y, 137, y), fill="#927b5d")
    back.rectangle((16, 16, 31, 77), fill="#7d654c")
    back.rectangle((18, 17, 28, 73), fill="#a08b6d")
    back.line((18, 74, 28, 74), fill="#66513e")
    back.line((20, 18, 20, 71), fill="#b7a184")

    # Keep the complete authored 6 × 9 rectangle transparent. In particular,
    # its last floor row must remain visible above the foreground trim.
    left, top = (coordinate * 16 for coordinate in SPOUSE_CONTEXT_ORIGIN)
    width, height = (size * 16 for size in SPOUSE_CONTEXT_ROOM_SIZE)
    back.rectangle((left, top, left + width - 1, top + height - 1), fill=(0, 0, 0, 0))

    # The top eight pixels of local floor row 8 remain clear, matching the
    # illustrated cutaway boundary rather than blocking the entire row.
    front.rectangle((32, 152, 139, 159), fill="#8e7658")
    front.line((32, 152, 137, 152), fill="#5c4c39")
    front.line((32, 153, 137, 153), fill="#ccb797")
    front.rectangle((32, 154, 137, 156), fill="#b5a080")
    front.line((32, 158, 140, 158), fill="#5c4c39")
    front.line((32, 159, 141, 159), fill="#292421")
    front.rectangle((0, 160, 143, 175), fill="#292421")
    return background, foreground


def spouse_context_layers(data, root):
    """Return owned RGBA ``(background, foreground)`` preview images.

    Native optional assets replace the schematic pair together. Missing or
    invalid declared artwork raises an error instead of silently substituting
    a different frame. Callers should close both returned images.
    """
    references = tuple(spouse_context_asset_refs(data))
    if not references:
        return _schematic_layers()
    background = _context_image(references[0], root)
    try:
        foreground = _context_image(references[1], root)
    except Exception:
        background.close()
        raise
    return background, foreground
