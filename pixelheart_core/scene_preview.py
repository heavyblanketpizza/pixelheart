"""Read-only scene artwork from a project or an explicitly chosen game export.

No discovery scan, network access, asset copying, or project mutation occurs.
Missing/unsupported assets return empty images and a reason so a UI can choose
an honestly labelled fallback. These previews do not simulate the game engine.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import io
from pathlib import Path
import re
import warnings
import xml.etree.ElementTree as ET

from PIL import Image

from .artwork import MAX_ARTWORK_BYTES, MAX_ARTWORK_PIXELS
from .local_templates import LocalTemplateError, _asset_path, _read_bounded, _safe_path, resolve_export_folder
from .locations import location_name
from .game_scene_assets import clear_game_cache, content_root, load_texture, load_game_map, load_farmer
from .projects import project_path, resolve_artwork
from .story import exported_npc_id
from .world import (MAX_ASSET_BYTES, MAX_BUNDLE_BYTES, asset_path, cast_actor_id,
                    exported_location_id, map_bundle, relative_path, render_map_preview)


_ASSET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,191}\Z")
_FACING_ROWS = {0: 2, 1: 1, 2: 0, 3: 3}
_FACING_NAMES = {"up": 0, "right": 1, "down": 2, "left": 3}
_IMAGE_CACHE = OrderedDict()
_MAP_CACHE = OrderedDict()
_MAX_CACHE_ENTRIES = 16
_MAX_CACHE_PIXELS = 16_777_216


def clear_preview_cache():
    """Release decoded local assets; useful when a user explicitly reconnects."""
    _IMAGE_CACHE.clear()
    _MAP_CACHE.clear()
    clear_game_cache()


def _keep(cache, key, record):
    cache[key] = record
    cache.move_to_end(key)
    while len(cache) > _MAX_CACHE_ENTRIES or sum(row["image"].width * row["image"].height
                                              for row in cache.values() if row.get("image") is not None) > _MAX_CACHE_PIXELS:
        cache.popitem(last=False)


def _stat_key(root, path):
    path = Path(path)
    checked = _safe_path(root, path.relative_to(root))
    if checked is None:
        raise LocalTemplateError("The local preview asset is missing.")
    details = checked.stat()
    return (str(checked), details.st_size, details.st_mtime_ns, details.st_ctime_ns, details.st_ino)


def _read_image(root, path, *, map_image=False):
    maximum = MAX_ASSET_BYTES if map_image else MAX_ARTWORK_BYTES
    max_pixels = 16_777_216 if map_image else MAX_ARTWORK_PIXELS
    key = (_stat_key(root, path), map_image)
    cached = _IMAGE_CACHE.get(key)
    if cached is not None:
        _IMAGE_CACHE.move_to_end(key)
        return cached["image"].copy(), key
    payload = _read_bounded(root, path, maximum, None)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(payload)) as image:
            if (image.format != "PNG" or image.width * image.height > max_pixels
                    or getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False)):
                raise ValueError("Use a bounded, static PNG for this preview.")
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            result = image.convert("RGBA")
    if key[0] != _stat_key(root, path):
        raise ValueError("The preview image changed while loading. Retry after the export finishes.")
    _keep(_IMAGE_CACHE, key, {"image": result})
    return result.copy(), key


def _sprite_size(sheet):
    if (not isinstance(sheet, Image.Image) or sheet.width < 64 or sheet.width % 4
            or sheet.width * sheet.height > MAX_ARTWORK_PIXELS):
        return None
    width = sheet.width // 4
    height = width * 2
    if sheet.height < 4 * height or sheet.height % height:
        return None
    return width, height


def crop_sprite_frame(sheet, facing=2, frame=0):
    """Crop an NPC walk frame. Game facings differ from the sheet's row order.

    Standard and proportionally enlarged 4-column sheets are accepted. Frames
    0–3 advance within a direction; no source pixels are modified.
    """
    if isinstance(facing, str):
        facing = _FACING_NAMES.get(facing.lower())
    dimensions = _sprite_size(sheet)
    if dimensions is None or type(facing) is not int or facing not in _FACING_ROWS or type(frame) is not int or not 0 <= frame < 4:
        return None
    width, height = dimensions
    x, y = frame * width, _FACING_ROWS[facing] * height
    return sheet.crop((x, y, x + width, y + height)).convert("RGBA")


def crop_portrait_frame(sheet, expression=0):
    """Crop a portrait by its zero-based expression index, or return None."""
    if (not isinstance(sheet, Image.Image) or type(expression) is not int or expression < 0
            or sheet.width < 128 or sheet.width % 2 or sheet.width * sheet.height > MAX_ARTWORK_PIXELS):
        return None
    size = sheet.width // 2
    if sheet.height % size or expression >= 2 * (sheet.height // size):
        return None
    x, y = (expression % 2) * size, (expression // 2) * size
    return sheet.crop((x, y, x + size, y + size)).convert("RGBA")


def _dict(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _location_label(document, location):
    if not isinstance(location, str):
        return "Scene location"
    document = _dict(document)
    for place in _rows(_dict(document.get("world")).get("locations", [])):
        if not isinstance(place.get("internal_name"), str):
            continue
        if location in (place["internal_name"], exported_location_id(place, _dict(document.get("character")))):
            return place.get("name") if isinstance(place.get("name"), str) and place["name"].strip() else place["internal_name"]
    return location_name(location)


def _project_asset(document, project_file, kind, variant):
    if not project_file:
        return None, None
    root = project_path(project_file).parent.resolve()
    # resolve_artwork validates artwork metadata in place. Isolate that small
    # subtree rather than copying every scene and character for each image.
    local = {"artwork": deepcopy(document.get("artwork", {}))}
    canonical_project = root / project_path(project_file).name
    path = resolve_artwork(local, canonical_project, kind, variant=variant) if variant else None
    if path is None:
        path = resolve_artwork(local, canonical_project, kind)
    return root, path


def _actor_source(document, actor):
    character = _dict(document.get("character"))
    if actor in ("$npc", character.get("internal_name"), exported_npc_id(character)):
        return document, "Your selected artwork"
    world = _dict(document.get("world"))
    for companion in _rows(world.get("characters", [])):
        npc = _dict(companion.get("character"))
        aliases = {npc.get("internal_name"), exported_npc_id(npc)} if isinstance(npc.get("internal_name"), (str, type(None))) else set()
        try:
            aliases.add(cast_actor_id(companion))
        except ValueError:
            pass
        if actor in aliases:
            return {"artwork": _dict(companion.get("artwork"))}, "Supporting character artwork"
    return None, "From my game"


def _check_actor_sheet(image, kind):
    frame = crop_sprite_frame(image) if kind == "sprite" else crop_portrait_frame(image)
    if frame is None:
        image.close()
        raise ValueError(f"The {kind} sheet has no supported standard frames.")
    frame.close()


def _game_actor_sheet(game_root, asset, kind, variant):
    root = content_root(game_root)
    if variant in ("winter", "beach"):
        try:
            image, path, key = load_texture(root, asset + "_" + variant.title())
        except ValueError:
            image, path, key = load_texture(root, asset)
    else:
        image, path, key = load_texture(root, asset)
    _check_actor_sheet(image, kind)
    return image, path, key


def load_actor_artwork(document, project_file, actor, *, export_root=None, game_root=None, variant=None, farmer_gender="female"):
    """Return independent full sheets plus local source metadata for one actor.

    Farmer bodies are displayed only after composing their real local clothing,
    skin, shoe, hair and arm layers into a standard preview sprite sheet.
    """
    result = {"sprite": None, "portrait": None, "sprite_path": None, "portrait_path": None,
              "source": "", "cache_key": (), "warning": "", "is_farmer": actor == "farmer", "base_path": None}
    problems, keys = [], []
    if not isinstance(document, dict) or not isinstance(actor, str) or not actor:
        result["warning"] = "Choose a character for the scene preview."
        return result
    root_choice = export_root or game_root
    if actor == "farmer":
        result["source"] = "Farmer artwork unavailable"
        gender = str(farmer_gender).lower()
        if gender not in ("female", "male"):
            result["warning"] = "Choose a female or male farmer preview."
            return result
        if game_root:
            try:
                image, path, key = load_farmer(game_root, gender)
                result.update(sprite=image, sprite_path=path, base_path=path,
                              cache_key=(key,), source="Farmer composed from Stardew Valley game artwork")
                return result
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                problems.append(str(exc))
        if root_choice:
            try:
                root = resolve_export_folder(root_choice)
                asset = "Characters/Farmer/" + ("farmer_girl_base" if gender == "female" else "farmer_base")
                path = _asset_path(root, asset, ".png")
                image, key = _read_image(root, path)
                image.close()
                result.update(base_path=path, cache_key=(key,), warning="The local farmer body needs clothing and appearance layers. Locate the game installation to show a clothed farmer.")
                return result
            except (OSError, ValueError, TypeError, RuntimeError, Image.DecompressionBombWarning, Image.DecompressionBombError):
                pass
        result["warning"] = "A composed farmer appearance is unavailable. " + " ".join(problems)
        return result
    local, source = _actor_source(document, actor)
    result["source"] = source
    for kind, prefix in (("sprite", "Characters"), ("portrait", "Portraits")):
        try:
            if local is not None:
                root, path = _project_asset(local, project_file, kind, variant)
                if path is None:
                    problems.append(f"No selected {kind} for this character.")
                    continue
            else:
                if not root_choice:
                    problems.append(f"Connect From my game to preview this character's {kind}.")
                    continue
                if not _ASSET_NAME.fullmatch(actor):
                    raise ValueError("The actor ID is not a supported local asset name.")
                root = resolve_export_folder(root_choice)
                path = _asset_path(root, f"{prefix}/{actor}", ".png")
            image, key = _read_image(root, path)
            _check_actor_sheet(image, kind)
        except (OSError, ValueError, TypeError, RuntimeError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            if local is not None or not game_root:
                problems.append(f"{kind.title()} unavailable: {exc}")
                continue
            try:
                image, path, key = _game_actor_sheet(game_root, f"{prefix}/{actor}", kind, variant)
                result["source"] = "Stardew Valley game artwork"
            except (OSError, ValueError, TypeError, RuntimeError) as game_exc:
                problems.append(f"{kind.title()} unavailable: {game_exc}")
                continue
        result[kind], result[kind + "_path"] = image, path
        keys.append((kind, key))
    result["cache_key"] = tuple(keys)
    result["warning"] = " ".join(problems)
    return result


def _dependency_key(root, path):
    try:
        return _stat_key(root, path)
    except (OSError, ValueError, RuntimeError) as exc:
        return (str(path), "unavailable", str(exc))


def _bounded_map_dependencies(root, path, observed=None):
    """Preflight only referenced files, using bounded reads and no linked paths."""
    observed = {} if observed is None else observed
    pending, visited, total = [path], {}, 0
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        if len(visited) >= 128:
            raise ValueError("A scene map can reference up to 128 local files.")
        observed[current] = _dependency_key(root, current)
        key = _stat_key(root, current)
        payload = _read_bounded(root, current, MAX_ASSET_BYTES, None)
        total += len(payload)
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("The scene map bundle exceeds 64 MiB.")
        visited[current] = key
        if current.suffix.lower() in (".tmx", ".tsx"):
            if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
                raise ValueError("Map XML cannot contain document types or external entities.")
            xml = ET.fromstring(payload)
            for element in xml.iter():
                if "source" not in element.attrib:
                    continue
                dependency = current.parent / relative_path(element.attrib["source"])
                observed[dependency] = _dependency_key(root, dependency)
                if dependency.suffix.lower() not in (".tsx", ".png"):
                    raise ValueError("Map dependencies must be local TSX or PNG files.")
                pending.append(dependency)
    return tuple(sorted(visited.items(), key=lambda entry: str(entry[0])))


def _read_map(root, path, maximum):
    cache_id = (str(path), maximum)
    cached = _MAP_CACHE.get(cache_id)
    if cached is not None:
        current = tuple((dependency, _dependency_key(root, dependency)) for dependency, _ in cached["dependencies"])
        if current == cached["dependencies"]:
            _MAP_CACHE.move_to_end(cache_id)
            if cached.get("error"):
                raise ValueError(cached["error"])
            return cached["image"].copy(), cached["map_size"], (cache_id, current)
    # Match the renderer's map-folder containment rule, even for a project map.
    map_root = path.parent.resolve()
    observed = {}
    try:
        dependencies = _bounded_map_dependencies(map_root, path, observed)
        bundle = map_bundle(path)
        image = render_map_preview(path, max_size=maximum).convert("RGBA")
    except (OSError, ValueError, TypeError, RuntimeError, ET.ParseError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        if observed:
            _keep(_MAP_CACHE, cache_id, {"image": None, "error": str(exc),
                                        "dependencies": tuple(sorted(observed.items(), key=lambda entry: str(entry[0])))})
        raise
    current = tuple((dependency, _stat_key(map_root, dependency)) for dependency, _ in dependencies)
    if current != dependencies:
        image.close()
        raise ValueError("The map changed while loading. Retry after its export finishes.")
    size = (bundle["width"], bundle["height"])
    _keep(_MAP_CACHE, cache_id, {"image": image, "map_size": size, "dependencies": dependencies})
    return image.copy(), size, (cache_id, dependencies)


def load_scene_map(document, project_file, location, *, export_root=None, game_root=None, max_map_size=4096, season="spring"):
    """Resolve the requested map only; an unavailable map is never substituted."""
    result = {"image": None, "foreground": None, "path": None, "map_size": None, "source": "", "cache_key": (), "warning": "",
              "location": location, "location_label": _location_label(document, location)}
    if type(max_map_size) is not int or not 1 <= max_map_size <= 4096:
        result["warning"] = "Map preview size must be from 1 to 4096 pixels."
        return result
    if not isinstance(document, dict) or not isinstance(location, str) or not _ASSET_NAME.fullmatch(location):
        result["warning"] = "Choose a supported map ID for the scene preview."
        return result
    selected = None
    try:
        character = _dict(document.get("character"))
        for place in _rows(_dict(document.get("world")).get("locations", [])):
            if not isinstance(place.get("internal_name"), str):
                continue
            if location in (place["internal_name"], exported_location_id(place, character)):
                selected = place
                break
        if selected is not None:
            if not project_file or not selected.get("map"):
                raise ValueError("This project location has no supplied map to preview.")
            root = project_path(project_file).parent.resolve()
            path = asset_path(selected["map"], root)
            if _safe_path(root, path.relative_to(root)) is None:
                raise ValueError("The project map is missing.")
            result["source"] = "Your supplied map"
        else:
            root_choice = export_root or game_root
            if not root_choice:
                raise ValueError("Connect From my game to preview this map.")
            root = resolve_export_folder(root_choice)
            result["source"] = "From my game"
            path = None
            for suffix in (".tmx", ".png"):
                try:
                    path = _asset_path(root, "Maps/" + location, suffix)
                    break
                except LocalTemplateError:
                    continue
            if path is None:
                raise ValueError(f"No local Maps_{location}.tmx or Maps_{location}.png export is available.")
        if path.suffix.lower() == ".tmx":
            image, size, key = _read_map(root, path, max_map_size)
        elif path.suffix.lower() == ".png":
            image, key = _read_image(root, path, map_image=True)
            if image.width % 16 or image.height % 16 or image.width > 4096 or image.height > 4096:
                image.close()
                raise ValueError("A map image must use 16-pixel tiles and be at most 256 tiles on each side.")
            size = (image.width // 16, image.height // 16)
            image.thumbnail((max_map_size, max_map_size), Image.Resampling.NEAREST)
            key = (key, max_map_size)
        else:
            raise ValueError("Supply a TMX map or a PNG map image for the scene preview.")
        result.update(image=image, path=path, map_size=size, cache_key=key)
    except (OSError, ValueError, TypeError, RuntimeError, ET.ParseError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        if selected is None and game_root:
            try:
                result.update(load_game_map(game_root, location, season=season, maximum=max_map_size))
            except (OSError, ValueError, TypeError, RuntimeError) as game_exc:
                result["warning"] = str(game_exc)
        else:
            result["warning"] = str(exc)
    if result["image"] is None:
        result["source"] = "Map preview unavailable"
    return result


def load_scene_assets(document, project_file, event, *, export_root=None, game_root=None,
                      preview_location="Town", farmer_gender="female", variant=None, max_map_size=4096):
    """Load current event scenery and cast. Defaults affect preview only.

    ``preview_location`` is used only when the event's location is blank. The
    caller owns any deliberately drawn fallback for unavailable actual maps.
    """
    event = _dict(event)
    requested = event.get("location", "")
    location = requested if isinstance(requested, str) and requested.strip() else preview_location
    if variant is None:
        season = _dict(event.get("story")).get("season")
        variant = season if season in ("spring", "summer", "fall", "winter") else None
    scenery = load_scene_map(document, project_file, location, export_root=export_root, game_root=game_root,
                             max_map_size=max_map_size, season=variant or "spring")
    actors, issues = {}, [scenery["warning"]] if scenery["warning"] else []
    for actor in _rows(_dict(event.get("story")).get("actors", [])):
        name = actor.get("name")
        if not isinstance(name, str) or not name or name in actors:
            continue
        record = load_actor_artwork(document, project_file, name, export_root=export_root, game_root=game_root,
                                    variant=variant, farmer_gender=farmer_gender)
        actors[name] = record
        if record["warning"]:
            issues.append(f"{name}: {record['warning']}")
    return {"map": scenery, "actors": actors, "issues": issues, "requested_location": requested,
            "preview_location": location, "key": (location, str(farmer_gender).lower(), scenery["cache_key"], scenery["warning"],
                                                     tuple((name, row["cache_key"], row["warning"]) for name, row in actors.items()))}


def resolve_scene_preview(document, project_file, event, *, export_root=None, game_root=None,
                          installation_root=None, preview_location="Town", farmer_gender="female"):
    """Compact facade for consumers that display all scene assets together."""
    assets = load_scene_assets(document, project_file, event, export_root=export_root,
                               game_root=game_root if game_root is not None else installation_root,
                               preview_location=preview_location, farmer_gender=farmer_gender)
    scenery = assets["map"]
    note = scenery["source"] if scenery["image"] is not None else f"{scenery['location_label']} map not available"
    missing = [name for name, row in assets["actors"].items() if row["sprite"] is None]
    if missing:
        note += "; artwork unavailable for " + ", ".join("the farmer" if name == "farmer" else name for name in missing)
    return {"background": scenery["image"], "foreground": scenery["foreground"], "map_size": scenery["map_size"],
            "sprites": {name: row["sprite"] for name, row in assets["actors"].items() if row["sprite"] is not None},
            "portraits": {name: row["portrait"] for name, row in assets["actors"].items() if row["portrait"] is not None},
            "location_label": scenery["location_label"], "source_note": note,
            "warnings": assets["issues"], "key": assets["key"]}
