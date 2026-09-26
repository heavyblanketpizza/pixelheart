"""Native spouse-patio sections and optional separate actor-pose assets."""
from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path

from PIL import Image

from .world import WorldError, _layer_gids, _read_asset, _xml, asset_path, map_bundle, relative_path


SEASONS = ("spring", "summer", "fall", "winter")


def patio_structure_issues(patio):
    """Validate authoring data without requiring files to be available yet."""
    issues = []

    def add(field, message):
        issues.append({"level": "error", "field": "spouse_patio" + ("." + field if field else ""), "message": message})

    def vector(value, length, low, high):
        return (isinstance(value, list) and len(value) == length
                and all(type(number) is int and low <= number <= high for number in value))

    def frames(value, field, required=False):
        if (not isinstance(value, list) or len(value) > 64 or (required and not value)
                or any(not vector(frame, 2, 0, 60000) or frame[0] > 4095 or frame[1] < 1 for frame in value)):
            add(field, "Use up to 64 [frame index, duration in milliseconds] pairs; indices must be 0–4095 and durations 1–60000. A pose needs at least one frame.")

    if patio is None:
        return issues
    if not isinstance(patio, dict):
        add("", "The spouse patio must be an object or null.")
        return issues
    try:
        if relative_path(patio.get("map")).suffix.lower() != ".tmx":
            raise WorldError("The spouse patio needs a project-relative .tmx map.")
    except WorldError as exc:
        add("map", str(exc))
    rect = patio.get("source_rect")
    if not vector(rect, 4, 0, 256) or not (1 <= rect[2] <= 4 and 1 <= rect[3] <= 4):
        add("source_rect", "Use [X, Y, Width, Height] in tiles, with a patio section no larger than 4×4 tiles.")
    frames(patio.get("animation_frames", []), "animation_frames", required=patio.get("pose") is not None)
    if not vector(patio.get("animation_pixel_offset", [0, 0]), 2, -128, 128):
        add("animation_pixel_offset", "Use two whole native-pixel offsets from -128 to 128.")
    pose = patio.get("pose")
    if pose is not None:
        if not isinstance(pose, dict):
            add("pose", "The patio pose must be an object or null.")
            return issues
        try:
            if relative_path(pose.get("texture")).suffix.lower() != ".png":
                raise WorldError("A patio pose needs a project-relative PNG texture.")
        except WorldError as exc:
            add("pose.texture", str(exc))
        seasonal = pose.get("seasonal_textures", {})
        if not isinstance(seasonal, dict):
            add("pose.seasonal_textures", "Seasonal pose textures must map spring, summer, fall, or winter to project-relative PNG files.")
        else:
            for season, reference in seasonal.items():
                if season not in SEASONS:
                    add("pose.seasonal_textures", "Use only spring, summer, fall, or winter for seasonal pose textures.")
                try:
                    if relative_path(reference).suffix.lower() != ".png":
                        raise WorldError("Each seasonal pose needs a project-relative PNG texture.")
                except WorldError as exc:
                    add("pose.seasonal_textures", str(exc))
        for field in ("frame_width", "frame_height"):
            size = pose.get(field)
            if type(size) is not int or not 16 <= size <= 128 or size % 16:
                add("pose." + field, "Pose frame dimensions must be multiples of 16 from 16 to 128 pixels.")
        frames(pose.get("frames"), "pose.frames", required=True)
        if not vector(pose.get("draw_offset_pixels", [0, 0]), 2, -128, 128):
            add("pose.draw_offset_pixels", "Use two whole native-pixel draw offsets from -128 to 128.")
    return issues


def patio_asset_references(patio):
    if patio is not None:
        yield patio["map"]
        if patio.get("pose") is not None:
            yield patio["pose"]["texture"]
            yield from patio["pose"].get("seasonal_textures", {}).values()


def _tilesets(path, xml):
    records = []
    for declaration in xml.findall("tileset"):
        tileset = declaration
        directory = path.parent
        if declaration.get("source"):
            source = directory / relative_path(declaration.get("source"))
            tileset = _xml(source)
            directory = source.parent
        first = int(declaration.get("firstgid", "0"))
        image = tileset.find("image")
        if (first < 1 or image is None or tileset.get("tilewidth", "16") != "16"
                or tileset.get("tileheight", "16") != "16"):
            raise WorldError("Patio maps need regular 16×16 PNG tilesheets with valid first GIDs.")
        margin, spacing = int(tileset.get("margin", "0")), int(tileset.get("spacing", "0"))
        if margin < 0 or spacing < 0:
            raise WorldError("Tilesheet margins and spacing cannot be negative.")
        with Image.open(directory / relative_path(image.get("source"))) as pixels:
            columns = (pixels.width - 2 * margin + spacing) // (16 + spacing)
            rows = (pixels.height - 2 * margin + spacing) // (16 + spacing)
        count = int(tileset.get("tilecount", str(columns * rows)))
        declared_columns = int(tileset.get("columns", str(columns)))
        if columns < 1 or rows < 1 or declared_columns != columns or not 1 <= count <= columns * rows:
            raise WorldError("The patio tilesheet dimensions do not match its declared tiles.")
        properties = {int(tile.get("id", "-1")): {prop.get("name"): prop.get("value", prop.text or "")
                      for prop in tile.findall("properties/property")} for tile in tileset.findall("tile")}
        records.append((first, count, properties))
    records.sort()
    if not records or any(left[0] + left[1] > right[0] for left, right in zip(records, records[1:])):
        raise WorldError("Patio tilesheets must have nonoverlapping GID ranges.")
    return records


def validate_patio_assets(patio, project_root):
    """Check the selected section, its standing marker, and its local approach."""
    failures = patio_structure_issues(patio)
    if failures:
        raise WorldError("; ".join(issue["message"] for issue in failures))
    if project_root is None:
        raise WorldError("Save the patio map and assets inside the project before exporting.")
    try:
        path = asset_path(patio["map"], project_root)
        bundle = map_bundle(path)
        xml = _xml(path)
        width, height = bundle["width"], bundle["height"]
        x, y, section_width, section_height = patio["source_rect"]
        if x + section_width > width or y + section_height > height:
            raise WorldError("The spouse-patio source rectangle falls outside its map.")
        if xml.findall("group"):
            raise WorldError("Patio layers must be top-level tile layers, not groups.")
        layers = {}
        for layer in xml.findall("layer"):
            name = layer.get("name")
            if name in layers:
                raise WorldError("Patio tile-layer names must be unique.")
            if (int(layer.get("width", str(width))) != width or int(layer.get("height", str(height))) != height
                    or any(float(layer.get(key, "0")) != 0 for key in ("offsetx", "offsety", "x", "y"))):
                raise WorldError("Patio tile layers must match the map dimensions and have no offsets.")
            layers[name] = _layer_gids(layer, width, height)
        if "Paths" not in layers:
            raise WorldError("The spouse patio needs a Paths layer with one tile-index-7 standing marker.")
        tilesets = _tilesets(path, xml)

        def tile(raw_gid):
            gid = raw_gid & 0x0FFFFFFF
            if not gid:
                return None, {}
            selected = next((record for record in reversed(tilesets) if record[0] <= gid), None)
            if selected is None or gid >= selected[0] + selected[1]:
                raise WorldError("A patio tile references a tile outside its tilesheet.")
            index = gid - selected[0]
            return index, selected[2].get(index, {})

        # Validate all GIDs because the full supplied map is loaded by the game.
        for values in layers.values():
            for gid in values:
                tile(gid)
        section = {(cx, cy) for cy in range(y, y + section_height) for cx in range(x, x + section_width)}
        markers = [(cx, cy) for cx, cy in section if tile(layers["Paths"][cy * width + cx])[0] == 7]
        if len(markers) != 1:
            raise WorldError("The selected patio section needs exactly one Paths tile-index-7 standing marker.")
        blocked = set()
        for cx, cy in section:
            index = cy * width + cx
            building, props = tile(layers["Buildings"][index])
            _, ground = tile(layers["Back"][index])
            if ((building is not None and "Passable" not in props)
                    or "NPCBarrier" in props or "NPCBarrier" in ground or "Water" in ground):
                blocked.add((cx, cy))
        marker = markers[0]
        if marker in blocked:
            raise WorldError("The spouse-patio standing marker is blocked by a collision or water tile.")
        # A spouse and player must be able to enter the insert from farm ground
        # along its southern boundary; outside farm obstructions need playtesting.
        reached = {(cx, y + section_height - 1) for cx in range(x, x + section_width)} - blocked
        pending = list(reached)
        while pending:
            cx, cy = pending.pop()
            for neighbor in ((cx-1, cy), (cx+1, cy), (cx, cy-1), (cx, cy+1)):
                if neighbor in section and neighbor not in blocked and neighbor not in reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
        if marker not in reached:
            raise WorldError("Keep an unobstructed route from the patio's southern edge to its standing marker.")
        neighbors = {(marker[0]-1, marker[1]), (marker[0]+1, marker[1]),
                     (marker[0], marker[1]-1), (marker[0], marker[1]+1)}
        if not (neighbors & reached or marker[1] == y + section_height - 1):
            raise WorldError("Keep a reachable adjacent tile for talking to the spouse.")
        pose = patio.get("pose")
        if pose is not None:
            base_size = None
            for reference in (pose["texture"], *pose.get("seasonal_textures", {}).values()):
                payload = _read_asset(asset_path(reference, project_root))
                with Image.open(io.BytesIO(payload)) as image:
                    if image.format != "PNG" or not (0 < image.width <= 4096 and 0 < image.height <= 4096):
                        raise WorldError("Patio poses must be PNG images no wider or taller than 4096 pixels.")
                    fw, fh = pose["frame_width"], pose["frame_height"]
                    if image.width % fw or image.height % fh:
                        raise WorldError("The pose texture dimensions must be exact multiples of its frame dimensions.")
                    if base_size is not None and image.size != base_size:
                        raise WorldError("Seasonal pose textures must have the same dimensions and frame grid as the base texture.")
                    base_size = image.size
                    count = image.width // fw * (image.height // fh)
                    if count > 4096 or any(frame[0] >= count for frame in pose["frames"]):
                        raise WorldError("A patio pose frame falls outside the supplied texture.")
                    image.verify()
                with Image.open(io.BytesIO(payload)) as image:
                    image.load()
        return {"bundle": bundle, "standing_tile": [marker[0]-x, marker[1]-y]}
    except WorldError:
        raise
    except (OSError, ValueError, TypeError, Image.DecompressionBombError) as exc:
        raise WorldError(f"Invalid spouse-patio assets: {exc}") from exc


def compile_patio(patio, character, project_root):
    from .story import exported_npc_id

    checked = validate_patio_assets(patio, project_root)
    bundle = checked["bundle"]
    digest = hashlib.sha256(str(character.get("id") or character.get("internal_name")).encode()).hexdigest()[:10]
    identity = f"PixelheartPatio_{digest}"
    prefix = f"assets/patio/{digest}/"
    backup = copy.deepcopy(patio)
    backup["map"] = prefix + "map/" + bundle["entry"]
    files = {prefix + "map/" + name: payload for name, payload in bundle["files"].items()}
    patches = [{"Action": "Load", "Target": "Maps/" + identity, "FromFile": backup["map"]}]
    x, y, width, height = patio["source_rect"]
    npc_field = {"MapAsset": identity, "MapSourceRect": {"X": x, "Y": y, "Width": width, "Height": height}}
    if "animation_frames" in patio:
        npc_field["SpriteAnimationFrames"] = copy.deepcopy(patio["animation_frames"])
    if "animation_pixel_offset" in patio:
        ox, oy = patio["animation_pixel_offset"]
        npc_field["SpriteAnimationPixelOffset"] = {"X": ox, "Y": oy}
    pose = patio.get("pose")
    if pose is not None:
        npc = exported_npc_id(character)
        texture = f"Mods/{npc}/SpousePatioPose"
        backup["pose"]["texture"] = prefix + "pose.png"
        files[backup["pose"]["texture"]] = _read_asset(asset_path(pose["texture"], project_root))
        seasonal = pose.get("seasonal_textures", {})
        remaining = [season for season in SEASONS if season not in seasonal]
        # Content Patcher permits only one active Load per asset. The base
        # handles the explicit complement, so every season has exactly one.
        if remaining:
            patches.append({"Action": "Load", "Target": texture, "FromFile": backup["pose"]["texture"],
                            **({"When": {"Season": ", ".join(remaining)}} if seasonal else {})})
        for season in SEASONS:
            if season in seasonal:
                reference = prefix + f"pose-{season}.png"
                backup["pose"]["seasonal_textures"][season] = reference
                files[reference] = _read_asset(asset_path(seasonal[season], project_root))
                patches.append({"Action": "Load", "Target": texture, "FromFile": reference, "When": {"Season": season}})
        patches.extend([
            {"Action": "EditData", "Target": "Pixelheart.Interiors/PatioPoses", "Entries": {npc: {
                "Npc": npc, "Texture": texture, "FrameWidth": pose["frame_width"], "FrameHeight": pose["frame_height"],
                "Frames": [{"Frame": frame, "Duration": duration} for frame, duration in pose["frames"]],
                "DrawOffsetPixels": copy.deepcopy(pose.get("draw_offset_pixels", [0, 0])),
            }}},
        ])
    return {"files": files, "patches": patches, "npc_field": npc_field, "patio": backup, "requires_runtime": pose is not None}
