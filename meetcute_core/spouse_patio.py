"""The spouse's 4×4 patio behind the farmhouse.

A patio cell refers to a tile in one of the patio's sheets: a game tilesheet,
named but never copied, or the creator's own PNG. The game places the area at
the farm's ``SpouseAreaLocation`` after marriage and puts the spouse on the
Paths tile with index 7. Game artwork is read only to preview the patio.
"""
from __future__ import annotations

import copy
import hashlib
import io
import re
import xml.etree.ElementTree as ET

from PIL import Image, UnidentifiedImageError

from .world import WorldError, _read_asset, _write_new_file, asset_path, relative_path

SIZE = 4
LAYERS = ("Back", "Buildings", "Front")
DEFAULT_STAND = (2, 3)
DEFAULT_CORNER = (69, 6)
MAX_SHEETS = 8
MAX_FRAMES = 32
STAND_TILE = 7
INTERNAL_NAME = "SpousePatio"
OWN_KEY = "own"
# The game's tilesheets offered as patio pieces, when the installation has them.
GAME_SHEETS = (("Outdoors", "spring_outdoorsTileSheet"), ("Town", "spring_town"),
               ("Indoor pieces", "townInterior"))
SEASONS = ("spring", "summer", "fall", "winter")
_KEY = re.compile(r"[A-Za-z0-9_]{1,48}\Z")
_IMAGE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,63}\Z")


def new_patio():
    return {"sheets": {}, "layers": {name: [None] * (SIZE * SIZE) for name in LAYERS},
            "stand": list(DEFAULT_STAND), "animation": {"frames": [], "offset": [0, 0]}}


def sheet_key(image):
    """The tileset name for a game sheet: its image name, made safe."""
    return re.sub(r"[^A-Za-z0-9_]", "_", image)[:48] or "sheet"


def game_image_name(image):
    """A tBIN tilesheet image as a bare Content/Maps asset name, or None."""
    image = str(image).replace("\\", "/")
    image = image[5:] if image.startswith("Maps/") else image
    if image.lower().endswith((".png", ".xnb")):
        image = image[:-4]
    return image if _IMAGE.fullmatch(image) else None


def _int(value, low, high):
    return type(value) is int and low <= value <= high


def patio_structure_issues(patio):
    """Shape problems as (field, message) pairs; field is relative to the patio."""
    issues = []
    if not isinstance(patio, dict):
        return [("", "The spouse patio must be an object.")]
    sheets = patio.get("sheets", {})
    if not isinstance(sheets, dict) or len(sheets) > MAX_SHEETS:
        issues.append(("sheets", f"A patio can use up to {MAX_SHEETS} tilesheets."))
        sheets = {}
    tile_counts = {}
    for key, sheet in sheets.items():
        field = "sheets." + str(key)
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(sheet, dict):
            issues.append((field, "Each patio tilesheet needs a short name and details."))
            continue
        if not (_int(sheet.get("columns"), 1, 256) and _int(sheet.get("rows"), 1, 256)):
            issues.append((field, "Patio tilesheets have 1 to 256 columns and rows of 16-pixel tiles."))
            continue
        if sheet.get("source") == "game":
            if not isinstance(sheet.get("image"), str) or game_image_name(sheet["image"]) != sheet["image"]:
                issues.append((field + ".image", "Use a game tilesheet's name from Content/Maps."))
                continue
        elif sheet.get("source") == "own":
            try:
                relative_path(sheet.get("asset"))
            except WorldError as exc:
                issues.append((field + ".asset", str(exc)))
                continue
        else:
            issues.append((field + ".source", "A patio tilesheet comes from the game or from your own PNG."))
            continue
        tile_counts[key] = sheet["columns"] * sheet["rows"]
    layers = patio.get("layers", {})
    if not isinstance(layers, dict) or set(layers) - set(LAYERS):
        issues.append(("layers", "A patio has Back, Buildings and Front layers."))
        layers = {}
    for name, cells in layers.items():
        if not isinstance(cells, list) or len(cells) != SIZE * SIZE:
            issues.append(("layers." + name, "Each patio layer has 16 cells."))
            continue
        for cell in cells:
            if cell is None:
                continue
            if (not isinstance(cell, list) or len(cell) != 2 or cell[0] not in tile_counts
                    or not _int(cell[1], 0, tile_counts[cell[0]] - 1)):
                issues.append(("layers." + name, "A patio cell refers to a missing tile."))
                break
    stand = patio.get("stand", list(DEFAULT_STAND))
    if not isinstance(stand, list) or len(stand) != 2 or not all(_int(v, 0, SIZE - 1) for v in stand):
        issues.append(("stand", "The standing spot must be inside the patio."))
    animation = patio.get("animation", {})
    if not isinstance(animation, dict):
        issues.append(("animation", "The patio activity must be an object."))
    else:
        frames = animation.get("frames", [])
        if (not isinstance(frames, list) or len(frames) > MAX_FRAMES
                or not all(isinstance(f, list) and len(f) == 2 and _int(f[0], 0, 999) and _int(f[1], 1, 60000)
                           for f in frames)):
            issues.append(("animation.frames", f"Use up to {MAX_FRAMES} frames, each a sprite and 1 to 60000 milliseconds."))
        offset = animation.get("offset", [0, 0])
        if not isinstance(offset, list) or len(offset) != 2 or not all(_int(v, -128, 128) for v in offset):
            issues.append(("animation.offset", "The animation offset is up to 128 pixels each way."))
    return issues


def normalize_patio(patio):
    issues = patio_structure_issues(patio)
    if issues:
        raise WorldError(issues[0][1])
    result = new_patio()
    result["sheets"] = copy.deepcopy(patio.get("sheets", {}))
    for name in LAYERS:
        if name in patio.get("layers", {}):
            result["layers"][name] = copy.deepcopy(patio["layers"][name])
    result["stand"] = list(patio.get("stand", DEFAULT_STAND))
    animation = patio.get("animation", {})
    result["animation"] = {"frames": copy.deepcopy(animation.get("frames", [])),
                           "offset": list(animation.get("offset", [0, 0]))}
    return result


def used_sheets(patio):
    return sorted({cell[0] for cells in patio["layers"].values() for cell in cells if cell})


def prune_sheets(patio):
    """Forget sheets no cell uses, so the palette choice never ships an unused sheet."""
    used = set(used_sheets(patio))
    patio["sheets"] = {key: sheet for key, sheet in patio["sheets"].items() if key in used}
    return patio


def _layer(layer):
    if layer not in LAYERS:
        raise WorldError("Choose the ground, objects or above-characters layer.")
    return layer


def paint(patio, layer, x, y, key, sheet, tiles):
    """Stamp rows of tile indices with their top-left at (x, y), clipped to the patio."""
    cells = patio["layers"][_layer(layer)]
    patio["sheets"].setdefault(key, dict(sheet))
    changed = False
    for row, line in enumerate(tiles):
        for column, tile in enumerate(line):
            cx, cy = x + column, y + row
            if 0 <= cx < SIZE and 0 <= cy < SIZE and cells[cy * SIZE + cx] != [key, tile]:
                cells[cy * SIZE + cx] = [key, tile]
                changed = True
    return changed


def erase(patio, layer, x, y):
    cells = patio["layers"][_layer(layer)]
    if not (0 <= x < SIZE and 0 <= y < SIZE) or cells[y * SIZE + x] is None:
        return False
    cells[y * SIZE + x] = None
    return True


def fill(patio, layer, key, sheet, tile):
    cells = patio["layers"][_layer(layer)]
    if all(cell == [key, tile] for cell in cells):
        return False
    patio["sheets"].setdefault(key, dict(sheet))
    patio["layers"][layer] = [[key, tile] for _ in cells]
    return True


def use_own_sheet(patio, sheet):
    """Make ``sheet`` the patio's own tilesheet, keeping every tile already painted from it."""
    used = [cell[1] for cells in patio["layers"].values() for cell in cells if cell and cell[0] == OWN_KEY]
    if used and max(used) >= sheet["columns"] * sheet["rows"]:
        raise WorldError("This sheet has fewer tiles than your patio uses. Keep the original sheet or erase those tiles first.")
    patio["sheets"][OWN_KEY] = dict(sheet)


def patio_asset_references(patio):
    for sheet in normalize_patio(patio)["sheets"].values():
        if sheet["source"] == "own":
            yield sheet["asset"]


def own_sheet_image(patio, root):
    """The creator's sheet as RGBA, checked against its recorded size."""
    sheet = patio["sheets"][OWN_KEY]
    payload = _read_asset(asset_path(sheet["asset"], root))
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.size != (sheet["columns"] * 16, sheet["rows"] * 16):
                raise WorldError("Your patio tilesheet changed size. Open it again in the patio editor.")
            return image.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, WorldError):
            raise
        raise WorldError(f"Cannot read your patio tilesheet: {exc}") from exc


def import_own_sheet(payload, root, columns, rows):
    """Copy a checked PNG into the project and return its sheet record."""
    reference = f"world_assets/patio/{hashlib.sha256(payload).hexdigest()[:24]}.png"
    _write_new_file(asset_path(reference, root), payload)
    return {"source": "own", "asset": reference, "columns": columns, "rows": rows}


def patio_issues(patio, character, root=None, *, taken_names=()):
    """Export problems as (level, field, message); fields are relative to the patio."""
    patio = normalize_patio(patio)
    issues = []
    if any(cell is None for cell in patio["layers"]["Back"]):
        issues.append(("error", "layers.Back", "Cover all 16 ground tiles of the spouse patio. Choose Ground and paint or fill it."))
    x, y = patio["stand"]
    if patio["layers"]["Buildings"][y * SIZE + x] is not None:
        issues.append(("error", "stand", "The spouse's standing spot on the patio is on an object. Move it to an open tile."))
    if OWN_KEY in used_sheets(patio) and root is not None:
        try:
            own_sheet_image(patio, root)
        except WorldError as exc:
            issues.append(("error", "sheets.own", str(exc)))
    if INTERNAL_NAME.casefold() in {name.casefold() for name in taken_names}:
        issues.append(("error", "", f"A place already uses the map ID {INTERNAL_NAME}. Rename that place first."))
    if not character.get("romanceable"):
        issues.append(("warning", "", "Only a spouse uses the patio. Turn on romance in Identity if they can marry."))
    return issues


def patio_tmx(patio):
    """A 4×4 Tiled map; game sheets are referenced by name and never included."""
    patio = normalize_patio(patio)
    root = ET.Element("map", {"version": "1.10", "orientation": "orthogonal", "renderorder": "right-down",
                              "width": str(SIZE), "height": str(SIZE), "tilewidth": "16", "tileheight": "16",
                              "infinite": "0", "nextlayerid": "5", "nextobjectid": "1"})
    first, gids = 1, {}
    for key in used_sheets(patio):
        sheet = patio["sheets"][key]
        count = sheet["columns"] * sheet["rows"]
        tileset = ET.SubElement(root, "tileset", {"firstgid": str(first), "name": key, "tilewidth": "16",
                                                  "tileheight": "16", "tilecount": str(count),
                                                  "columns": str(sheet["columns"])})
        source = sheet["image"] if sheet["source"] == "game" else "patio_tiles.png"
        ET.SubElement(tileset, "image", {"source": source, "width": str(sheet["columns"] * 16),
                                         "height": str(sheet["rows"] * 16)})
        gids[key] = first
        first += count
    # Invisible metadata sheet: Paths index 7 is where the game stands the spouse.
    paths = ET.SubElement(root, "tileset", {"firstgid": str(first), "name": "meetcute_paths", "tilewidth": "16",
                                            "tileheight": "16", "tilecount": "8", "columns": "8"})
    ET.SubElement(paths, "image", {"source": "paths.png", "width": "128", "height": "16"})
    layers = {name: [gids[cell[0]] + cell[1] if cell else 0 for cell in patio["layers"][name]] for name in LAYERS}
    layers["Paths"] = [0] * (SIZE * SIZE)
    x, y = patio["stand"]
    layers["Paths"][y * SIZE + x] = first + STAND_TILE
    for index, (name, values) in enumerate(layers.items(), 1):
        attributes = {"id": str(index), "name": name, "width": str(SIZE), "height": str(SIZE)}
        if name == "Paths":
            attributes["visible"] = "0"
        data = ET.SubElement(ET.SubElement(root, "layer", attributes), "data", {"encoding": "csv"})
        data.text = "\n" + ",\n".join(",".join(map(str, values[row * SIZE:(row + 1) * SIZE])) for row in range(SIZE)) + "\n"
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def compile_patio(patio, identity, root, prefix):
    """Files, the map Load patch, and the Data/Characters SpousePatio field."""
    patio = normalize_patio(patio)
    files = {prefix + "patio.tmx": patio_tmx(patio)}
    stream = io.BytesIO()
    Image.new("RGBA", (128, 16)).save(stream, format="PNG")
    files[prefix + "paths.png"] = stream.getvalue()
    if OWN_KEY in used_sheets(patio):
        stream = io.BytesIO()
        own_sheet_image(patio, root).save(stream, format="PNG")
        files[prefix + "patio_tiles.png"] = stream.getvalue()
    field = {"MapAsset": identity, "MapSourceRect": {"X": 0, "Y": 0, "Width": SIZE, "Height": SIZE}}
    frames = patio["animation"]["frames"]
    if frames:
        field["SpriteAnimationFrames"] = [list(frame) for frame in frames]
        x, y = patio["animation"]["offset"]
        if x or y:
            field["SpriteAnimationPixelOffset"] = {"X": x, "Y": y}
    patches = [{"Action": "Load", "Target": "Maps/" + identity, "FromFile": prefix + "patio.tmx"}]
    return {"files": files, "patches": patches, "npc_field": field}


# --- Reading the installed game (previews and starting points only) ---------

def _content(folder):
    from .game_scene_assets import content_root
    return content_root(folder)


def _game_map(root, name):
    from .game_scene_assets import asset_path as game_asset_path, parse_tbin
    from .local_templates import _read_bounded
    from .xnb_preview import MAX_PACKED, map_payload
    return parse_tbin(map_payload(_read_bounded(root, game_asset_path(root, "Maps/" + name), MAX_PACKED, None)))


def _cells_from_map(data, x0, y0):
    """The 4×4 area at (x0, y0) of a parsed game map as patio cells, sheets and stand."""
    sheets, layers, stand = {}, {name: [None] * (SIZE * SIZE) for name in LAYERS}, None
    for layer in data["layers"]:
        name = layer["name"]
        if name not in LAYERS and name != "Paths":
            continue
        for x, y, (sheet_id, index, _blend, _properties) in layer["tiles"]:
            if not (x0 <= x < x0 + SIZE and y0 <= y < y0 + SIZE):
                continue
            if name == "Paths":
                if index == STAND_TILE:
                    stand = [x - x0, y - y0]
                continue
            sheet = data["sheets"][sheet_id]
            image = game_image_name(sheet["image"])
            if image is None:
                continue
            key = sheet_key(image)
            sheets[key] = {"source": "game", "image": image, "columns": sheet["size"][0], "rows": sheet["size"][1]}
            layers[name][(y - y0) * SIZE + (x - x0)] = [key, index]
    return sheets, layers, stand


def spouse_area_corner(data):
    value = data["properties"].get("SpouseAreaLocation")
    try:
        x, y = (int(part) for part in str(value).split())
        return x, y
    except (TypeError, ValueError):
        return DEFAULT_CORNER


def farm_ground(folder):
    """A patio holding the farm's own tiles at the spouse area."""
    data = _game_map(_content(folder), "Farm")
    corner = spouse_area_corner(data)
    sheets, layers, stand = _cells_from_map(data, *corner)
    patio = new_patio()
    patio.update(sheets=sheets, layers=layers)
    if stand:
        patio["stand"] = stand
    return normalize_patio(patio)


def game_patios(folder, limit=64):
    """Each non-empty 4×4 block of the game's own patio map, as a starting patio."""
    data = _game_map(_content(folder), "spousePatios")
    width = max(layer["size"][0] for layer in data["layers"])
    height = max(layer["size"][1] for layer in data["layers"])
    found = []
    for y0 in range(0, height - SIZE + 1, SIZE):
        for x0 in range(0, width - SIZE + 1, SIZE):
            sheets, layers, stand = _cells_from_map(data, x0, y0)
            if not any(cell for cells in layers.values() for cell in cells):
                continue
            patio = new_patio()
            patio.update(sheets=sheets, layers=layers)
            if stand:
                patio["stand"] = stand
            found.append(normalize_patio(patio))
            if len(found) >= limit:
                return found
    return found


def game_sheet_choices(folder):
    """(label, sheet record) for each offered game tilesheet the installation has."""
    from .game_scene_assets import asset_path as game_asset_path, load_texture
    content_root = _content(folder)
    choices, seen = [], set()
    candidates = list(GAME_SHEETS)
    try:
        patio_map = _game_map(content_root, "spousePatios")
        for sheet in patio_map["sheets"].values():
            image = game_image_name(sheet["image"])
            if image:
                candidates.insert(0, ("Game patios", image))
    except (OSError, ValueError):
        pass
    for label, image in candidates:
        if image in seen:
            continue
        seen.add(image)
        try:
            game_asset_path(content_root, "Maps/" + image)
            texture, _path, _key = load_texture(content_root, "Maps/" + image)
        except (OSError, ValueError):
            continue
        if texture.width % 16 or texture.height % 16:
            continue
        choices.append((label, {"source": "game", "image": image,
                                "columns": texture.width // 16, "rows": texture.height // 16}))
    return choices


def game_texture(folder, image, season="spring"):
    """A game tilesheet as RGBA, in the season's version when the game has one."""
    from .game_scene_assets import asset_path as game_asset_path, load_texture
    content_root = _content(folder)
    if season not in SEASONS:
        raise WorldError("Choose a season.")
    if season != "spring" and image.startswith("spring_"):
        try:
            game_asset_path(content_root, "Maps/" + season + image[6:])
            image = season + image[6:]
        except ValueError:
            pass
    texture, _path, _key = load_texture(content_root, "Maps/" + image)
    return texture.convert("RGBA")


def farm_context(folder, season="spring", margin=3):
    """The farm around the patio as back and front images, and the patio's pixel origin in them."""
    from .game_scene_assets import load_game_map
    data = _game_map(_content(folder), "Farm")
    corner = spouse_area_corner(data)
    rendered = load_game_map(folder, "Farm", season=season, maximum=4096)
    left, top = max(0, corner[0] - margin), max(0, corner[1] - margin)
    right = min(rendered["map_size"][0], corner[0] + SIZE + margin)
    bottom = min(rendered["map_size"][1], corner[1] + SIZE + margin)
    box = (left * 16, top * 16, right * 16, bottom * 16)
    return {"back": rendered["image"].crop(box), "front": rendered["foreground"].crop(box),
            "origin": ((corner[0] - left) * 16, (corner[1] - top) * 16), "corner": corner}


def render_patio(patio, textures):
    """Back+Buildings and Front images of a patio; sheets without a texture are left blank."""
    back, front = Image.new("RGBA", (SIZE * 16, SIZE * 16)), Image.new("RGBA", (SIZE * 16, SIZE * 16))
    for name in LAYERS:
        target = front if name == "Front" else back
        for position, cell in enumerate(patio["layers"][name]):
            if not cell or cell[0] not in textures or cell[0] not in patio["sheets"]:
                continue
            columns = patio["sheets"][cell[0]]["columns"]
            sx, sy = cell[1] % columns * 16, cell[1] // columns * 16
            texture = textures[cell[0]]
            if sx + 16 > texture.width or sy + 16 > texture.height:
                continue
            target.alpha_composite(texture.crop((sx, sy, sx + 16, sy + 16)),
                                   (position % SIZE * 16, position // SIZE * 16))
    return back, front
