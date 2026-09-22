"""Deterministic viewing conditions and imported light masks for room previews."""
from __future__ import annotations

from functools import lru_cache
import math

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .interior_furniture import _preview_atlas, _contained, FurnitureValidationError


# Viewing conditions are editor controls, separate from exported game state.
AMBIENT = {"day": (255, 255, 255), "evening": (174, 153, 181), "night": (83, 96, 139)}


@lru_cache(maxsize=32)
def _radial_bytes(size):
    """Fallback for custom libraries which describe a light without a mask."""
    radius = size / 2
    return bytes(round(255 * max(0, 1 - math.hypot(x + .5 - radius, y + .5 - radius) / radius) ** 2)
                 for y in range(size) for x in range(size))


def apply_preview_lighting(image, data, root, *, time_of_day="day", lights_on=True):
    """Tint the room and composite bounded observations from furniture lights.

    Imported masks supply their actual shape. Local viewing conditions and
    compositing remain a preview; exported items use the game's own renderer.
    The caller retains ownership of the input image, including when this
    function returns a replacement image.
    """
    if time_of_day not in AMBIENT or type(lights_on) is not bool:
        raise ValueError("Choose Day, Evening or Night and an on/off light state.")
    from .interiors import floor_cells, wall_cells
    mask = Image.new("L", image.size)
    draw = ImageDraw.Draw(mask)
    cells = floor_cells(data) | wall_cells(data)
    for x, y in cells:
        draw.rectangle((x * 16, y * 16, x * 16 + 15, y * 16 + 15), fill=255)
    rgb = image.convert("RGB")
    if time_of_day != "day":
        # Include structural trim in ambient shading, leaving canvas margins
        # unchanged so the preview blends into its surrounding viewport.
        ambient_mask = mask.copy()
        if data.get("room_frame") and data["kind"] == "residence":
            frame_draw = ImageDraw.Draw(ambient_mask)
            for x, y in cells:
                frame_draw.rectangle((x * 16 - 16, y * 16 - 16, x * 16 + 31, y * 16 + 31), fill=255)
        tint = Image.new("RGB", image.size, AMBIENT[time_of_day])
        shaded = ImageChops.multiply(rgb, tint)
        ambient = Image.composite(shaded, rgb, ambient_mask)
        rgb.close()
        rgb = ambient
        tint.close()
        shaded.close()
        ambient_mask.close()
    definitions = {item["id"]: item for item in data["catalog"]}
    is_dark = time_of_day != "day"
    for placed in data["furniture"]:
        definition = definitions[placed["item_id"]]
        for light in definition.get("preview_lights", []):
            if light["rotation"] != placed["rotation"]:
                continue
            if light["when"] == "day" and is_dark or light["when"] == "night" and not is_dark:
                continue
            # Daylight through windows remains when artificial lights are off.
            if not lights_on and not (definition["kind"] == "window" and light["when"] == "day"):
                continue
            strength = light["intensity"]
            if not strength:
                continue
            diameter = max(1, round(light["radius"] * 32))
            rect = light.get("mask_rect")
            source_size = tuple(rect[2:]) if rect else (min(128, diameter),) * 2
            longest = max(source_size)
            size = tuple(max(1, round(diameter * part / longest)) for part in source_size)
            cx = (placed["x"] + light["offset"][0]) * 16
            cy = (placed["y"] + light["offset"][1]) * 16
            left, top = round(cx - size[0] / 2), round(cy - size[1] / 2)
            bounds = max(0, left), max(0, top), min(image.width, left + size[0]), min(image.height, top + size[1])
            if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                continue
            try:
                if rect:
                    x, y, width, height = rect
                    with _preview_atlas(_contained(root, definition["preview_asset"])) as atlas:
                        if x + width > atlas.width or y + height > atlas.height:
                            raise FurnitureValidationError("A light mask extends beyond its PNG atlas.")
                        crop = atlas.crop((x, y, x + width, y + height))
                    alpha = crop.getchannel("A")
                    if light.get("mask_channel") == "alpha":
                        # Some game masks are black RGB; all of their light
                        # shape is carried by alpha, not luminance.
                        shape = alpha
                    else:
                        luminance = ImageOps.grayscale(crop)
                        shape = ImageChops.multiply(alpha, luminance)
                        alpha.close()
                        luminance.close()
                    crop.close()
                else:
                    # Bound reusable source-mask memory independently of the
                    # scene zoom or a large supplied radius.
                    shape = Image.frombytes("L", source_size, _radial_bytes(source_size[0]))
                # Resize only the visible fraction, never a 4096px halo for a
                # small room (or one entirely beyond the canvas).
                source_box = tuple((coordinate - origin) * extent / target
                                   for coordinate, origin, extent, target in zip(bounds, (left, top, left, top), source_size * 2, size * 2))
                local = shape.resize((bounds[2] - bounds[0], bounds[3] - bounds[1]),
                                     Image.Resampling.BILINEAR, box=source_box)
                shape.close()
                region_mask = mask.crop(bounds)
                clipped = ImageChops.multiply(local, region_mask)
                local.close()
                region_mask.close()
                level = clipped.point([round(value * strength) for value in range(256)])
                clipped.close()
                tint = Image.new("RGB", level.size, light["color"])
                black = Image.new("RGB", level.size)
                emission = Image.composite(tint, black, level)
                region = rgb.crop(bounds)
                lit = ImageChops.screen(region, emission)
                rgb.paste(lit, bounds[:2])
                for temporary in (level, tint, black, emission, region, lit):
                    temporary.close()
            except (OSError, FurnitureValidationError):
                # Missing preview art must not make a saved room unreadable.
                continue
    result = rgb.convert("RGBA")
    with image.getchannel("A") as alpha:
        result.putalpha(alpha)
    rgb.close()
    mask.close()
    return result
