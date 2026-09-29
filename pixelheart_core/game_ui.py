"""Pixelheart's warm-white menu pieces, plus a few borrowed from the player's game.

The paper pieces are drawn originally. When a game is connected, its hearts, bold
lettering (re-inked to match) and Cursors atlas are copied into a private per-user
cache; only coordinates ship with Pixelheart. Coordinates were identified by
inspecting decoded Stardew Valley 1.6.15 textures.
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
# Bump CACHE_VERSION when the borrowed game pieces change, PAPER_VERSION when the drawn ones do.
CACHE_VERSION = "4"
PAPER_VERSION = "2"
# role: (asset, (x, y, width, height)), cropped at one art pixel per texture pixel.
GAME_PIECES = {
    "heart_full": ("LooseSprites/Cursors", (211, 428, 7, 6)),
    "heart_empty": ("LooseSprites/Cursors", (218, 428, 7, 6)),
}
FONT_ASSET = "LooseSprites/font_bold"
CURSORS_ASSET = "LooseSprites/Cursors"
REQUIRED_ROLES = (
    "panel", "sidebar", "tab", "tab_idle", "button", "button_hover", "button_pressed", "button_disabled",
    "button_primary", "button_danger", "textbox", "textbox_focus", "dropdown_arrow", "checkbox_off",
    "checkbox_on", "scroll_thumb", "scroll_track", "heart_full", "heart_empty",
)

# Warm white and one ink.
INK, INK_LIFT, DANGER = "#26221d", "#3d3830", "#9e3a2b"
PAPER, PAPER_HOVER, PAPER_PRESSED, PAPER_SHADE = "#fdfbf7", "#f3f0ea", "#e6e1d8", "#e4dfd6"
PAGE, SIDEBAR, PANEL_RULE, FIELD_RULE = "#f5f2ec", "#edeae3", "#cfc9be", "#aca598"
THUMB, TRACK = "#c4bdb1", "#ebe7e0"
HEART = (".oo.oo.", "orrorro", "orrrrro", ".orrro.", "..oro..", "...o...")


@dataclass(frozen=True)
class UiPiece:
    path: Path
    margin: int


def _enlarged(image):
    return image.resize((image.width * PIXEL_SCALE, image.height * PIXEL_SCALE), Image.Resampling.NEAREST)


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


def _inked(font):
    """The game's two-tone bold font as ink, with its drop shadow kept as a soft grey."""
    rgba = font.convert("RGBA")
    dark = rgba.convert("L").point(lambda value: 255 if value < 128 else 0)
    result = Image.composite(Image.new("RGBA", rgba.size, _rgb(INK)), Image.new("RGBA", rgba.size, _rgb(PANEL_RULE)), dark)
    result.putalpha(rgba.getchannel("A"))
    return result


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


def _fingerprint(content):
    parts = [CACHE_VERSION]
    for asset in sorted({row[0] for row in GAME_PIECES.values()} | {FONT_ASSET, CURSORS_ASSET}):
        try:
            info = asset_path(content, asset).stat()
            parts.append(f"{asset}:{info.st_size}:{info.st_mtime_ns}")
        except (OSError, ValueError):
            parts.append(f"{asset}:missing")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def build_game_ui(content, cache_root):
    """Copy the game's hearts, re-inked bold font and Cursors atlas; missing pieces are simply absent."""
    try:
        content = Path(content).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        return {}
    name = "game-" + _fingerprint(content)
    cached = _read(Path(cache_root) / name)
    if cached is not None:
        return cached
    textures, images = {}, {}

    def texture(asset):
        if asset not in textures:
            textures[asset] = load_texture(content, asset)[0]
        return textures[asset]

    for role, (asset, (x, y, width, height)) in GAME_PIECES.items():
        try:
            source = texture(asset)
        except (OSError, ValueError, RuntimeError):
            continue
        if x + width <= source.width and y + height <= source.height:
            images[role] = _enlarged(source.crop((x, y, x + width, y + height)))
    for role, asset, prepare in (("font", FONT_ASSET, _inked), ("cursors", CURSORS_ASSET, None)):
        try:
            images[role] = prepare(texture(asset)) if prepare else texture(asset)
        except (OSError, ValueError, RuntimeError):
            pass
    if not images:
        return {}
    return _publish(cache_root, name, images, {}) or {}


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


def _paper_art():
    """Ink on warm white, so the player's own portraits and sprites carry the color."""
    def box(ring, fill, *, top=None, bottom=None, size=(9, 9), cut=True):
        image = _rings(*size, (ring,), fill, cut_corners=cut)
        for y, color in ((1, top), (size[1] - 2, bottom)):
            for x in range(1, size[0] - 1) if color else ():
                image.putpixel((x, y), _rgb(color))
        return image

    check_on = box(INK, INK)
    for point in ((2, 4), (3, 5), (4, 6), (5, 5), (6, 4), (7, 3), (2, 5), (3, 6), (4, 7)):
        check_on.putpixel(point, _rgb(PAPER))
    arrow = Image.new("RGBA", (10, 11), (0, 0, 0, 0))
    for row, (start, end) in enumerate(((2, 8), (3, 7), (4, 6))):
        for x in range(start, end):
            arrow.putpixel((x, 4 + row), _rgb(INK))
    return {
        "panel": (box(PANEL_RULE, PAPER), 4),
        "sidebar": (Image.new("RGBA", (4, 4), _rgb(SIDEBAR)), 0),
        "tab": (box(INK, PAPER), 6),
        "tab_idle": (box(PANEL_RULE, PAGE), 6),
        "button": (box(INK, PAPER, bottom=PAPER_SHADE), 6),
        "button_hover": (box(INK, PAPER_HOVER, bottom=PAPER_SHADE), 6),
        "button_pressed": (box(INK, PAPER_PRESSED, top=PAPER_SHADE), 6),
        "button_disabled": (box(PANEL_RULE, PAGE), 6),
        "button_primary": (box(INK, INK, top=INK_LIFT), 6),
        "button_danger": (box(DANGER, PAPER, bottom=PAPER_SHADE), 6),
        "textbox": (box(FIELD_RULE, PAPER), 6),
        "textbox_focus": (box(INK, PAPER), 6),
        "dropdown_arrow": (arrow, 0),
        "checkbox_off": (box(INK, PAPER), 0),
        "checkbox_on": (check_on, 0),
        "scroll_thumb": (box(THUMB, THUMB, size=(6, 10)), 4),
        "scroll_track": (box(TRACK, TRACK, size=(6, 6)), 4),
        "heart_full": (_pattern(HEART, {"o": "#5b1010", "r": "#e53d1d"}), 0),
        "heart_empty": (_pattern(HEART, {"o": FIELD_RULE}), 0),
    }


def build_paper_ui(cache_root):
    """Every role drawn once and cached, with no game art."""
    name = f"paper-v{PAPER_VERSION}"
    cached = _read(Path(cache_root) / name)
    if cached is not None:
        return cached
    images, margins = {}, {}
    for role, (image, margin) in _paper_art().items():
        images[role] = _enlarged(image)
        margins[role] = margin
    return _publish(cache_root, name, images, margins) or {}


def ui_pieces(content, cache_root):
    """Every role the stylesheet needs, with the game's hearts and lettering when available."""
    pieces = dict(build_paper_ui(cache_root))
    if content is not None:
        try:
            pieces.update(build_game_ui(content, cache_root))
        except (OSError, ValueError, RuntimeError):
            pass
    return pieces
