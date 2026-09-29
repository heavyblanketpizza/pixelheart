"""Menu pieces cut from the player's installed game, or drawn originally.

Only coordinates ship with Pixelheart. Crops are written to a private per-user
cache; when no game is available, original pieces with the same roles are drawn.
Coordinates were identified by inspecting decoded Stardew Valley 1.6.15 textures.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from .game_scene_assets import asset_path, load_texture

PIXEL_SCALE = 2
CACHE_VERSION = "2"
# role: (asset, (x, y, width, height), screen px per art px in the source, nine-slice margin after scaling)
PIECES = {
    "panel": ("Maps/MenuTiles", (0, 256, 60, 60), 4, 10),
    "wood": ("Maps/MenuTiles", (0, 1024, 64, 64), 4, 0),
    "tab": ("LooseSprites/Cursors", (16, 368, 16, 16), 1, 8),
    "button": ("LooseSprites/Cursors", (432, 439, 9, 9), 1, 6),
    "textbox": ("LooseSprites/textBox", (0, 0, 192, 48), 4, 6),
    "dropdown_arrow": ("LooseSprites/Cursors", (437, 450, 10, 11), 1, 0),
    "checkbox_off": ("LooseSprites/Cursors", (227, 425, 9, 9), 1, 0),
    "checkbox_on": ("LooseSprites/Cursors", (236, 425, 9, 9), 1, 0),
    "scroll_thumb": ("LooseSprites/Cursors", (435, 463, 6, 10), 1, 4),
    "scroll_track": ("LooseSprites/Cursors", (403, 383, 6, 6), 1, 4),
    "heart_full": ("LooseSprites/Cursors", (211, 428, 7, 6), 1, 0),
    "heart_empty": ("LooseSprites/Cursors", (218, 428, 7, 6), 1, 0),
}
FONT_ASSET = "LooseSprites/font_bold"
CURSORS_ASSET = "LooseSprites/Cursors"
# variant role: (base role, operation, amount)
VARIANTS = {
    "button_hover": ("button", "shade", 1.12),
    "button_pressed": ("button", "shade", 0.86),
    "button_disabled": ("button", "grey", 0.55),
    # (hue shift, saturation ×, value ×): a leafy green and a berry red, not neon.
    "button_primary": ("button", "hue", (52, 0.72, 0.72)),
    "button_danger": ("button", "hue", (-24, 0.75, 0.74)),
    "tab_idle": ("tab", "shade", 0.9),
    "textbox_focus": ("textbox", "shade", 1.08),
}
REQUIRED_ROLES = tuple(PIECES) + tuple(VARIANTS)

# Original palette sampled from the game's menu colors; the pieces below are drawn fresh.
OUTLINE, DEEP, FRAME, BRIGHT, MID, GOLD = "#853605", "#5b2b2a", "#dc7b05", "#fa9305", "#b14e05", "#f7ba00"
PARCHMENT, PARCHMENT_LIGHT, TEXTBOX, SHADOW = "#fdbc6e", "#ffd284", "#f9ba66", "#d4966b"
WOOD = ("#92591c", "#7e4d15", "#6d4214", "#5c3514")
HEART = (".oo.oo.", "orrorro", "orrrrro", ".orrro.", "..oro..", "...o...")


@dataclass(frozen=True)
class UiPiece:
    path: Path
    margin: int


def _scaled(image, source_scale):
    factor = PIXEL_SCALE / source_scale
    size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    return image.resize(size, Image.Resampling.NEAREST)


def _variant(image, operation, amount):
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if operation == "shade":
        rgb = rgba.convert("RGB").point(lambda value: max(0, min(255, round(value * amount))))
    elif operation == "grey":
        grey = rgba.convert("L").convert("RGB")
        rgb = Image.blend(rgba.convert("RGB"), grey, amount)
    else:
        shift, saturation, brightness = amount
        h, s, v = rgba.convert("RGB").convert("HSV").split()
        mask = s.point(lambda value: 255 if value > 60 else 0)
        recolored = (h.point(lambda value: (value + shift) % 256),
                     s.point(lambda value: round(value * saturation)),
                     v.point(lambda value: round(value * brightness)))
        rgb = Image.merge("HSV", tuple(Image.composite(new, old, mask)
                                       for new, old in zip(recolored, (h, s, v)))).convert("RGB")
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def _write(folder, images, margins):
    folder.mkdir(parents=True, exist_ok=True)
    rows = {}
    for role, image in images.items():
        image.save(folder / f"{role}.png", "PNG")
        rows[role] = [f"{role}.png", margins.get(role, 0)]
    (folder / "pieces.json").write_text(json.dumps(rows))


def _read(folder):
    try:
        rows = json.loads((folder / "pieces.json").read_text())
        pieces = {role: UiPiece(folder / name, int(margin)) for role, (name, margin) in rows.items()}
        return pieces if all(piece.path.is_file() for piece in pieces.values()) else None
    except (OSError, ValueError, TypeError):
        return None


def _publish(cache_root, name, images, margins):
    """Write into a staging folder, then rename, so a crash never leaves half a cache."""
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    final = cache_root / name
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=cache_root))
    try:
        _write(staging, images, margins)
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return _read(final)


def _with_variants(images, margins):
    for role, (base, operation, amount) in VARIANTS.items():
        if base in images:
            images[role] = _variant(images[base], operation, amount)
            margins[role] = margins.get(base, 0)


def _fingerprint(content):
    parts = [CACHE_VERSION]
    for asset in sorted({row[0] for row in PIECES.values()} | {FONT_ASSET}):
        try:
            info = asset_path(content, asset).stat()
            parts.append(f"{asset}:{info.st_size}:{info.st_mtime_ns}")
        except (OSError, ValueError):
            parts.append(f"{asset}:missing")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def build_game_ui(content, cache_root):
    """Crop every available piece from the game; missing pieces are simply absent."""
    try:
        content = Path(content).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        return {}
    name = "game-" + _fingerprint(content)
    cached = _read(Path(cache_root) / name)
    if cached is not None:
        return cached
    textures, images, margins = {}, {}, {}

    def texture(asset):
        if asset not in textures:
            textures[asset] = load_texture(content, asset)[0]
        return textures[asset]

    for role, (asset, (x, y, width, height), scale, margin) in PIECES.items():
        try:
            source = texture(asset)
        except (OSError, ValueError, RuntimeError):
            continue
        if x + width > source.width or y + height > source.height:
            continue
        images[role] = _scaled(source.crop((x, y, x + width, y + height)), scale)
        margins[role] = margin
    _with_variants(images, margins)
    for role, asset in (("font", FONT_ASSET), ("cursors", CURSORS_ASSET)):
        try:
            images[role] = texture(asset)
        except (OSError, ValueError, RuntimeError):
            pass
    if not images:
        return {}
    return _publish(cache_root, name, images, margins) or {}


def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4)) + (255,)


def _rings(width, height, colors, fill, *, cut_corners=True):
    image = Image.new("RGBA", (width, height), _rgb(fill))
    for inset, color in enumerate(colors):
        for x in range(inset, width - inset):
            image.putpixel((x, inset), _rgb(color))
            image.putpixel((x, height - 1 - inset), _rgb(color))
        for y in range(inset, height - inset):
            image.putpixel((inset, y), _rgb(color))
            image.putpixel((width - 1 - inset, y), _rgb(color))
    if cut_corners:
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            image.putpixel(point, (0, 0, 0, 0))
    return image


def _pattern(rows, palette):
    image = Image.new("RGBA", (len(rows[0]), len(rows)), (0, 0, 0, 0))
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell in palette:
                image.putpixel((x, y), _rgb(palette[cell]))
    return image


def _original_art():
    """Original pixel art at one art pixel per image pixel; scaled ×2 on publish."""
    wood = Image.new("RGBA", (32, 32))
    for y in range(32):
        for x in range(32):
            seam = y % 8 == 7 or (x + (y // 8) * 11) % 32 == 0
            wood.putpixel((x, y), _rgb(WOOD[3] if seam else WOOD[(x // 5 + y // 8) % 3]))
    check_off = _rings(9, 9, (DEEP,), PARCHMENT_LIGHT, cut_corners=False)
    check_on = check_off.copy()
    for point in ((2, 4), (3, 5), (4, 6), (5, 5), (6, 4), (7, 3), (2, 5), (3, 6), (4, 7)):
        check_on.putpixel(point, _rgb("#3c9a1e"))
    arrow = _rings(10, 11, (DEEP, MID), BRIGHT)
    for row, (start, end) in enumerate(((3, 7), (4, 6))):
        for x in range(start, end):
            arrow.putpixel((x, 5 + row), _rgb(DEEP))
    button = _rings(9, 9, (DEEP,), BRIGHT)
    for x in range(1, 8):
        button.putpixel((x, 1), _rgb(GOLD))
        button.putpixel((x, 7), _rgb(MID))
    return {
        "panel": (_rings(15, 15, (OUTLINE, FRAME, MID), PARCHMENT), 6),
        "wood": (wood, 0),
        "tab": (_rings(16, 16, (DEEP, FRAME, MID), PARCHMENT_LIGHT), 8),
        "button": (button, 6),
        "textbox": (_rings(24, 12, (DEEP, SHADOW), TEXTBOX), 6),
        "dropdown_arrow": (arrow, 0),
        "checkbox_off": (check_off, 0),
        "checkbox_on": (check_on, 0),
        "scroll_thumb": (_rings(6, 10, (DEEP,), BRIGHT), 4),
        "scroll_track": (_rings(6, 6, (DEEP,), SHADOW), 4),
        "heart_full": (_pattern(HEART, {"o": "#5b1010", "r": "#e53d1d"}), 0),
        "heart_empty": (_pattern(HEART, {"o": "#8a6a6a"}), 0),
    }


def build_fallback_ui(cache_root):
    """Original pieces for every role, drawn once and cached."""
    name = f"original-v{CACHE_VERSION}"
    cached = _read(Path(cache_root) / name)
    if cached is not None:
        return cached
    images, margins = {}, {}
    for role, (image, margin) in _original_art().items():
        images[role] = image.resize((image.width * PIXEL_SCALE, image.height * PIXEL_SCALE), Image.Resampling.NEAREST)
        margins[role] = margin
    _with_variants(images, margins)
    return _publish(cache_root, name, images, margins) or {}


def ui_pieces(content, cache_root):
    """Complete role map preferring the game's art, plus where it came from."""
    original = build_fallback_ui(cache_root)
    game = {}
    if content is not None:
        try:
            game = build_game_ui(content, cache_root)
        except (OSError, ValueError, RuntimeError):
            game = {}
    pieces = {**original, **game}
    used = sum(1 for role in REQUIRED_ROLES if role in game)
    source = "original" if not used else "game" if used == len(REQUIRED_ROLES) else "mixed"
    return pieces, source
