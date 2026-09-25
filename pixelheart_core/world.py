"""Portable supporting cast, supplied Tiled maps, and safe local installation.

No game assets are generated. Tiled bundles are copied with their relative
references intact; export uses documented Content Patcher map/data patches.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import struct
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib

from PIL import Image

from .story import exported_npc_id, story_repeat_events
from .locations import VANILLA_LOCATIONS


MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,191}\Z")


class WorldError(ValueError):
    """An authored world or supplied asset cannot be handled safely."""


def new_world():
    return {"version": 1, "characters": [], "locations": [], "dependencies": []}


def new_companion(name="New companion", age="adult"):
    from .projects import new_project
    character = new_project()["character"]
    internal = re.sub(r"[^A-Za-z0-9_]", "", name)[:64] or "Companion"
    if not internal[0].isalpha():
        internal = "NPC" + internal[:61]
    character.update(name=name, internal_name=internal, age=age, romanceable=False)
    return {"id": character["id"], "character": character,
            "artwork": {"portrait": None, "sprite": None}}


def new_location():
    return {"id": str(uuid.uuid4()), "name": "New place", "internal_name": "NewPlace",
            "map": None, "spouse_room": False, "room_x": 0, "room_y": 0,
            "room_width": 6, "room_height": 9,
            "entrance": {"map": "Town", "x": 32, "y": 62,
                         "arrival_x": 32, "arrival_y": 63},
            "entry_x": 2, "entry_y": 2, "exit_x": 2, "exit_y": 3}


def relative_path(reference):
    if not isinstance(reference, str) or not reference or "\x00" in reference:
        raise WorldError("Asset references must be nonempty relative paths.")
    normalized = reference.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or PureWindowsPath(reference).drive or ".." in path.parts or ":" in normalized or not path.parts:
        raise WorldError("World assets must stay inside the project folder; parent and absolute paths are not supported.")
    return Path(*path.parts)


def asset_path(reference, root):
    root = Path(root)
    path = root / relative_path(reference)
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            raise WorldError("World assets cannot point through a symlink outside the project folder.")
    except (OSError, RuntimeError) as exc:
        raise WorldError(f"Cannot resolve world asset: {exc}") from exc
    return path


def normalize_world(world=None):
    if world is None:
        return new_world()
    issues = world_structure_issues(world)
    if issues:
        raise WorldError("; ".join(item["message"] for item in issues))
    result = {**new_world(), **copy.deepcopy(world)}
    result["locations"] = [{**new_location(), **item,
                            "entrance": {**new_location()["entrance"], **item.get("entrance", {})}}
                           for item in result["locations"]]
    return result


def world_structure_issues(world):
    issues = []
    def add(field, message):
        issues.append({"level": "error", "field": "world" + ("." + field if field else ""), "message": message})
    if not isinstance(world, dict):
        add("", "World must be a JSON object.")
        return issues
    if type(world.get("version", 1)) is not int or world.get("version", 1) != 1:
        add("version", "This world version is not supported.")
    for key, limit in (("characters", 32), ("locations", 32), ("dependencies", 64)):
        values = world.get(key, [])
        if not isinstance(values, list) or len(values) > limit:
            add(key, f"Use a list with up to {limit} {key}.")
            continue
        seen = set()
        for index, record in enumerate(values):
            field = f"{key}.{index}"
            if not isinstance(record, dict):
                add(field, "Each world entry must be an object.")
                continue
            identity = record.get("id")
            if not isinstance(identity, str) or not identity or len(identity) > 192 or identity in seen:
                add(field + ".id", "World entries need unique nonempty text IDs.")
            else:
                seen.add(identity)
            if key == "characters":
                if not isinstance(record.get("character"), dict):
                    add(field + ".character", "A companion needs a character object.")
                if not isinstance(record.get("artwork", {}), dict):
                    add(field + ".artwork", "Companion artwork must contain portrait and sprite references.")
                else:
                    for kind in ("portrait", "sprite"):
                        reference = record.get("artwork", {}).get(kind)
                        if reference is not None:
                            try:
                                relative_path(reference)
                            except WorldError as exc:
                                add(field + ".artwork." + kind, str(exc))
            elif key == "locations":
                if "interior" in record:
                    try:
                        from .interiors import normalize_interior
                        interior = normalize_interior(record["interior"])
                        if (interior["kind"] == "spouse") != record.get("spouse_room", False):
                            add(field + ".interior", "The interior mode must match the place's spouse-room setting.")
                    except (ValueError, TypeError) as exc:
                        add(field + ".interior", str(exc))
                for name in ("name", "internal_name"):
                    if name in record and (not isinstance(record[name], str) or len(record[name]) > (80 if name == "name" else 40)):
                        add(field + "." + name, "Use a short text name (map IDs have at most 40 characters).")
                if "spouse_room" in record and type(record["spouse_room"]) is not bool:
                    add(field + ".spouse_room", "Choose whether this is a spouse room.")
                if record.get("map") is not None:
                    try:
                        relative_path(record["map"])
                    except WorldError as exc:
                        add(field + ".map", str(exc))
                for name in ("room_x", "room_y", "entry_x", "entry_y", "exit_x", "exit_y", "room_width", "room_height"):
                    if name in record and (type(record[name]) is not int or not 0 <= record[name] <= 1000):
                        add(field + "." + name, "Tile values must be whole numbers from 0 to 1000.")
                entrance = record.get("entrance", {})
                if not isinstance(entrance, dict):
                    add(field + ".entrance", "The entrance must describe its map and tiles.")
                else:
                    if "confirmed" in entrance and type(entrance["confirmed"]) is not bool:
                        add(field + ".entrance.confirmed", "Entrance confirmation must be true or false.")
                    for name in ("x", "y", "arrival_x", "arrival_y"):
                        if name in entrance and (type(entrance[name]) is not int or not 0 <= entrance[name] <= 1000):
                            add(field + ".entrance." + name, "Entrance tiles must be whole numbers from 0 to 1000.")
                    if "map" in entrance and not isinstance(entrance["map"], str):
                        add(field + ".entrance.map", "Use a map's internal name.")
            else:
                if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
                    add(field + ".id", "Use the dependency's SMAPI UniqueID.")
                version = record.get("minimum_version", "")
                if not isinstance(version, str) or (version and not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?", version)):
                    add(field + ".minimum_version", "Use a version like 1.0.0, or leave the minimum version empty.")
                if type(record.get("required", True)) is not bool:
                    add(field + ".required", "Dependency required must be true or false.")
    return issues


def _read_asset(path):
    try:
        if path.stat().st_size > MAX_ASSET_BYTES:
            raise WorldError(f"{path.name} exceeds the 16 MiB asset limit.")
        return path.read_bytes()
    except OSError as exc:
        raise WorldError(f"Cannot read {path.name}: {exc}") from exc


def _xml(path):
    payload = _read_asset(path)
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise WorldError("Tiled files cannot contain XML document types or external entities.")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise WorldError(f"Invalid Tiled XML in {path.name}: {exc}") from exc


def map_bundle(path):
    """Return validated TMX/TSX/PNG sources, with references inside map's folder."""
    path = Path(path).expanduser()
    if path.suffix.lower() != ".tmx":
        raise WorldError("Import a finite orthogonal .tmx map exported by Tiled.")
    root = path.parent.resolve()
    files = {}
    queue = [path]
    while queue:
        current = queue.pop()
        if not current.resolve().is_relative_to(root):
            raise WorldError("Map dependencies must stay in the selected map's folder.")
        reference = current.relative_to(path.parent).as_posix()
        if reference in files:
            continue
        if len(files) >= 128:
            raise WorldError("A map bundle supports up to 128 files.")
        payload = _read_asset(current)
        files[reference] = payload
        if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
            raise WorldError("A map bundle must be 64 MiB or smaller.")
        if current.suffix.lower() in (".tmx", ".tsx"):
            xml = _xml(current)
            if xml.tag not in ("map", "tileset"):
                raise WorldError("Expected a Tiled map or tileset XML document.")
            for item in xml.iter():
                if "source" in item.attrib:
                    source = item.attrib["source"]
                    dependency = current.parent / relative_path(source)
                    if dependency.suffix.lower() not in (".tsx", ".png"):
                        raise WorldError("Map dependencies must be local TSX tilesets or PNG tilesheets.")
                    queue.append(dependency)
        elif current.suffix.lower() == ".png":
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    if image.format != "PNG" or image.width * image.height > 16_777_216:
                        raise WorldError("Tilesheets must be PNG images of at most 16 million pixels.")
                    image.verify()
                with Image.open(io.BytesIO(payload)) as image:
                    image.load()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise WorldError(f"Invalid tilesheet {current.name}: {exc}") from exc
    xml = _xml(path)
    try:
        width, height = int(xml.attrib["width"]), int(xml.attrib["height"])
        if not (1 <= width <= 256 and 1 <= height <= 256):
            raise ValueError
        if int(xml.get("tilewidth", "0")) != 16 or int(xml.get("tileheight", "0")) != 16:
            raise ValueError
    except (KeyError, ValueError):
        raise WorldError("Use a 1–256 tile map with 16×16 pixel tiles.") from None
    if xml.get("orientation", "orthogonal") != "orthogonal" or xml.get("infinite", "0") != "0":
        raise WorldError("Use a finite orthogonal Tiled map.")
    layers = {layer.get("name") for layer in xml.findall("layer")}
    if not {"Back", "Buildings", "Front"} <= layers:
        raise WorldError("A game map needs Back, Buildings, and Front tile layers.")
    return {"files": files, "width": width, "height": height, "layers": sorted(layers), "entry": path.name}


def _layer_gids(layer, width, height):
    data = layer.find("data")
    if data is None:
        raise WorldError("A map layer is missing tile data.")
    count = width * height
    try:
        encoding = data.get("encoding")
        if encoding == "csv":
            result = [int(value.strip()) for value in (data.text or "").replace("\n", "").split(",") if value.strip()]
        elif encoding == "base64":
            binary = base64.b64decode("".join((data.text or "").split()), validate=True)
            compression = data.get("compression", "")
            if compression:
                if compression not in ("zlib", "gzip"):
                    raise WorldError("Map preview supports CSV, XML, or base64 with zlib/gzip compression.")
                decoder = zlib.decompressobj(31 if compression == "gzip" else 15)
                binary = decoder.decompress(binary, count * 4 + 1)
                if decoder.unconsumed_tail or len(binary) != count * 4:
                    raise WorldError("Map tile data exceeds its declared dimensions.")
            if len(binary) != count * 4:
                raise WorldError("Map tile data does not match its declared dimensions.")
            result = list(struct.unpack("<" + "I" * count, binary))
        elif encoding is None:
            result = [int(tile.get("gid", "0")) for tile in data.findall("tile")]
        else:
            raise WorldError("Map preview supports CSV, XML, or base64 tile data.")
        if len(result) != count or any(not 0 <= value <= 0xFFFFFFFF for value in result):
            raise WorldError("Map tile data does not match its declared dimensions.")
        return result
    except (ValueError, struct.error, zlib.error) as exc:
        raise WorldError(f"Invalid map tile data: {exc}") from exc


def _render_map_preview(path, max_size=1024):
    """Render supplied orthogonal tiles only; this is not collision simulation."""
    path = Path(path)
    bundle = map_bundle(path)
    xml = _xml(path)
    width, height = bundle["width"], bundle["height"]
    tilesets = []
    for declaration in xml.findall("tileset"):
        first_gid = int(declaration.get("firstgid", "1"))
        tileset = declaration
        directory = path.parent
        if declaration.get("source"):
            source = path.parent / relative_path(declaration.get("source"))
            tileset = _xml(source)
            directory = source.parent
        image = tileset.find("image")
        if image is None or tileset.get("tilewidth", "16") != "16" or tileset.get("tileheight", "16") != "16":
            raise WorldError("Preview needs regular 16×16 PNG tilesheets; image collections are not previewed.")
        margin, spacing = int(tileset.get("margin", "0")), int(tileset.get("spacing", "0"))
        if margin < 0 or spacing < 0:
            raise WorldError("Tilesheet margins and spacing cannot be negative.")
        with Image.open(directory / relative_path(image.get("source"))) as pixels:
            sheet = pixels.convert("RGBA")
        columns = int(tileset.get("columns") or max(1, (sheet.width - margin + spacing) // (16 + spacing)))
        if columns <= 0:
            raise WorldError("The tilesheet has no tile columns.")
        tilesets.append((first_gid, sheet, columns, margin, spacing))
    tilesets.sort(key=lambda item: item[0])
    result = Image.new("RGBA", (width * 16, height * 16), (0, 0, 0, 0))
    for layer in xml.findall("layer"):
        if layer.get("name") == "Paths" or layer.get("visible", "1") == "0":
            continue
        if any(float(layer.get(key, "0")) != 0 for key in ("offsetx", "offsety", "x", "y")):
            raise WorldError("Offset map layers are exported but not supported by the local preview.")
        gids = _layer_gids(layer, width, height)
        opacity = float(layer.get("opacity", "1"))
        if not 0 <= opacity <= 1:
            raise WorldError("Layer opacity must be from 0 to 1.")
        for index, raw_gid in enumerate(gids):
            gid = raw_gid & 0x0FFFFFFF
            if not gid:
                continue
            selected = next((item for item in reversed(tilesets) if item[0] <= gid), None)
            if selected is None:
                raise WorldError("A map tile references an unknown tilesheet.")
            first, sheet, columns, margin, spacing = selected
            tile = gid - first
            x = margin + tile % columns * (16 + spacing)
            y = margin + tile // columns * (16 + spacing)
            if x + 16 > sheet.width or y + 16 > sheet.height:
                raise WorldError("A map tile lies outside its tilesheet.")
            pixels = sheet.crop((x, y, x + 16, y + 16))
            if raw_gid & 0x20000000:
                pixels = pixels.transpose(Image.Transpose.TRANSPOSE)
            if raw_gid & 0x80000000:
                pixels = pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if raw_gid & 0x40000000:
                pixels = pixels.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if opacity != 1:
                pixels.putalpha(pixels.getchannel("A").point(lambda alpha: round(alpha * opacity)))
            result.alpha_composite(pixels, ((index % width) * 16, (index // width) * 16))
    result.thumbnail((max_size, max_size), Image.Resampling.NEAREST)
    return result


def render_map_preview(path, max_size=1024):
    """Return a bounded PIL image or a readable WorldError for unsupported maps."""
    if type(max_size) is not int or not 1 <= max_size <= 4096:
        raise WorldError("Preview size must be from 1 to 4096 pixels.")
    try:
        return _render_map_preview(path, max_size)
    except WorldError:
        raise
    except (OSError, ValueError, TypeError, Image.DecompressionBombError) as exc:
        raise WorldError(f"This map cannot be previewed: {exc}") from exc


def _write_new_file(destination, payload):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise WorldError("Refusing to copy an asset through a symlink.")
    if destination.exists():
        if destination.read_bytes() != payload:
            raise WorldError(f"The destination asset {destination.name} has different contents; it was not overwritten.")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pixelheart-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_map(source, project_file):
    from .projects import project_path
    bundle = map_bundle(source)
    digest = hashlib.sha256()
    for name, payload in sorted(bundle["files"].items()):
        digest.update(name.encode())
        digest.update(payload)
    root = project_path(project_file).parent
    prefix = f"world_assets/maps/{digest.hexdigest()[:24]}"
    for name, payload in bundle["files"].items():
        _write_new_file(asset_path(prefix + "/" + name, root), payload)
    return prefix + "/" + bundle["entry"]


def world_asset_references(world):
    world = normalize_world(world)
    for entry in world["characters"]:
        for reference in entry.get("artwork", {}).values():
            if isinstance(reference, str):
                yield reference
    for location in world["locations"]:
        if location["map"]:
            yield location["map"]
        if "interior" in location:
            from .interiors import interior_asset_references
            yield from interior_asset_references(location["interior"])


def copy_world_assets(world, source_root, destination_root):
    """Copy the complete world closure with stable project-relative references."""
    for reference in world_asset_references(world):
        source = asset_path(reference, source_root)
        if source.suffix.lower() == ".tmx":
            bundle = map_bundle(source)
            for name, payload in bundle["files"].items():
                target = (Path(reference).parent / name).as_posix()
                _write_new_file(asset_path(target, destination_root), payload)
        else:
            _write_new_file(asset_path(reference, destination_root), _read_asset(source))


def exported_location_id(location, character):
    digest = hashlib.sha256(str(character.get("id") or character.get("internal_name")).encode()).hexdigest()[:10]
    return f"Pixelheart_{digest}_{location['internal_name']}"


def exported_mod_id(character):
    internal = str(character.get("internal_name", "")).strip()
    suffix = hashlib.sha256(str(character.get("id") or internal).encode()).hexdigest()[:10]
    return f"Pixelheart.{internal}_{suffix}"


def cast_actor_id(companion_or_id):
    """An authoring alias that survives a supporting character's name changes."""
    identity = companion_or_id.get("id") if isinstance(companion_or_id, dict) else companion_or_id
    if not isinstance(identity, str) or not identity:
        raise WorldError("A supporting actor needs a stable record ID.")
    return "PixelheartCast." + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _character_references(character):
    yield "home_map", "map", character.get("home_map")
    for index, stop in enumerate(character.get("schedule", []) if isinstance(character.get("schedule", []), list) else []):
        if isinstance(stop, dict):
            yield f"schedule.{index}.location", "map", stop.get("location")
    for index, event in enumerate(character.get("events", []) if isinstance(character.get("events", []), list) else []):
        if not isinstance(event, dict):
            continue
        yield f"events.{index}.location", "map", event.get("location")
        story = event.get("story", {})
        if not isinstance(story, dict):
            continue
        for collection, key in (("actors", "name"), ("beats", "actor")):
            entries = story.get(collection, [])
            for number, entry in enumerate(entries if isinstance(entries, list) else []):
                if isinstance(entry, dict):
                    yield f"events.{index}.story.{collection}.{number}.{key}", "actor", entry.get(key)
    for index, relation in enumerate(character.get("relationships", []) if isinstance(character.get("relationships", []), list) else []):
        if isinstance(relation, dict) and isinstance(relation.get("story"), dict):
            yield f"relationships.{index}.story.target", "actor", relation["story"].get("target")
    life = character.get("life", {})
    if isinstance(life, dict) and isinstance(life.get("routines", []), list):
        for index, routine in enumerate(life.get("routines", [])):
            if isinstance(routine, dict) and isinstance(routine.get("stops", []), list):
                for number, stop in enumerate(routine.get("stops", [])):
                    if isinstance(stop, dict):
                        yield f"life.routines.{index}.stops.{number}.location", "map", stop.get("location")


def world_character(character, world, primary=None):
    """Resolve authored map/cast aliases in a copy while preserving original notes."""
    world = normalize_world(world)
    result = copy.deepcopy(character)
    primary = primary or character
    maps = {item["internal_name"]: exported_location_id(item, primary) for item in world["locations"]}
    actors = {item["character"].get("internal_name"): exported_npc_id(item["character"]) for item in world["characters"]
              if isinstance(item["character"].get("internal_name"), str)}
    actors.update({cast_actor_id(item): exported_npc_id(item["character"]) for item in world["characters"]})
    actors.pop(character.get("internal_name"), None)
    if primary is not character:
        actors[primary.get("internal_name")] = exported_npc_id(primary)
    result["home_map"] = maps.get(result.get("home_map"), result.get("home_map"))
    for stop in result.get("schedule", []):
        if isinstance(stop, dict):
            stop["location"] = maps.get(stop.get("location"), stop.get("location"))
    for event in result.get("events", []):
        if not isinstance(event, dict):
            continue
        event["location"] = maps.get(event.get("location"), event.get("location"))
        story = event.get("story", {})
        if isinstance(story, dict):
            for entry in story.get("actors", []) if isinstance(story.get("actors", []), list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    entry["name"] = actors.get(entry["name"], entry["name"])
            for entry in story.get("beats", []) if isinstance(story.get("beats", []), list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("actor"), str):
                    entry["actor"] = actors.get(entry["actor"], entry["actor"])
    for relation in result.get("relationships", []):
        if isinstance(relation, dict) and isinstance(relation.get("story"), dict):
            target = relation["story"].get("target")
            if isinstance(target, str):
                relation["story"]["target"] = actors.get(target, target)
    life = result.get("life", {})
    if isinstance(life, dict) and isinstance(life.get("routines", []), list):
        for routine in life.get("routines", []):
            if isinstance(routine, dict) and isinstance(routine.get("stops", []), list):
                for stop in routine.get("stops", []):
                    if isinstance(stop, dict) and isinstance(stop.get("location"), str):
                        stop["location"] = maps.get(stop["location"], stop["location"])
    return result


def world_issues(world, character, project_root=None):
    if world is None:
        return []
    issues = world_structure_issues(world)
    if issues:
        return issues
    world = normalize_world(world)
    def add(level, field, message):
        issues.append({"level": level, "field": "world." + field, "message": message})
    from .exporting import validate_character
    used = {str(character.get("internal_name", "")).casefold()}
    identities = {str(character.get("id", ""))}
    known_cast = {cast_actor_id(entry) for entry in world["characters"]}
    spouse_maps = {entry["internal_name"] for entry in world["locations"] if entry["spouse_room"]}
    all_characters = [("", character)] + [(f"world.characters.{index}.character.", entry["character"]) for index, entry in enumerate(world["characters"])]
    for prefix, authored in all_characters:
        for field, kind, reference in _character_references(authored):
            if not isinstance(reference, str):
                continue
            if kind == "actor" and reference.startswith("PixelheartCast.") and reference not in known_cast:
                issues.append({"level": "error", "field": prefix + field, "message": "This supporting character was removed. Choose an existing cast member before exporting."})
            if kind == "map" and reference in spouse_maps:
                issues.append({"level": "error", "field": prefix + field, "message": "A spouse-room section is a farmhouse asset, not a standalone location. Use FarmHouse or create a connected place."})
    for index, entry in enumerate(world["characters"]):
        prefix = f"characters.{index}"
        authored = entry["character"]
        identity = authored.get("id")
        if not isinstance(identity, str) or not identity or identity in identities:
            add("error", prefix + ".character.id", "Every character needs its own stable project ID.")
        else:
            identities.add(identity)
        internal = authored.get("internal_name", "")
        if isinstance(internal, str) and internal.casefold() in used:
            add("error", prefix + ".character.internal_name", "Every character needs a different internal name.")
        if isinstance(internal, str):
            used.add(internal.casefold())
        age = authored.get("age", "adult")
        if age not in ("adult", "teen", "child") or (age != "adult" and authored.get("romanceable")):
            add("error", prefix + ".character.age", "Supporting cast may be Adult, Teen, or Child; romance is adult only.")
        try:
            prepared = world_character(authored, world, character)
            prepared["age"] = "adult"
            paths = {kind: asset_path(entry.get("artwork", {}).get(kind), project_root)
                     if entry.get("artwork", {}).get(kind) and project_root else None for kind in ("portrait", "sprite")}
            for issue in validate_character(prepared, paths["portrait"], paths["sprite"]):
                issues.append({**issue, "field": "world." + prefix + "." + issue["field"]})
        except (WorldError, TypeError, ValueError) as exc:
            add("error", prefix, str(exc))
    names = set()
    spouse_rooms = 0
    vanilla_names = {location.id.casefold() for location in VANILLA_LOCATIONS}
    entrances = set()
    locations_by_name = {location["internal_name"]: location for location in world["locations"]}
    known_dimensions = {}
    for index, location in enumerate(world["locations"]):
        if "doorway" in location.get("interior", {}):
            from .interiors import doorway_exit
            exit_x, exit_y = doorway_exit(location["interior"])
            location = {**location, "exit_x": exit_x, "exit_y": exit_y}
        prefix = f"locations.{index}"
        internal = location["internal_name"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", internal) or internal.casefold() in names:
            add("error", prefix + ".internal_name", "Give each custom place a unique ID using a letter followed by up to 39 letters, digits, or underscores.")
        names.add(internal.casefold())
        if internal.casefold() in vanilla_names:
            add("error", prefix + ".internal_name", "Use a new place ID instead of an existing vanilla map name.")
        # Existing projects predate explicit entrance confirmation. Keep their
        # saved connections working; new GUI drafts opt in with confirmed=False.
        # Report this even when missing map artwork also blocks export.
        if not location["spouse_room"] and not location["entrance"].get("confirmed", True):
            add("error", prefix + ".entrance", "Choose the entrance in Home & places and select “Use this entrance” before exporting this place.")
        try:
            if "interior" in location:
                from .interiors import interior_export_issues, reachable_tiles
                if project_root is None:
                    raise WorldError("Save the interior project before exporting.")
                design = location["interior"]
                failures = interior_export_issues(design, project_root)
                if failures:
                    raise WorldError(failures[0]["message"])
                bundle = {"width": design["width"], "height": design["height"]}
                if not location["spouse_room"]:
                    if [location["entry_x"], location["entry_y"]] != design["entry"]:
                        raise WorldError("The place's arrival must match its designed interior entry.")
                    if (location["exit_x"], location["exit_y"]) not in reachable_tiles(design):
                        raise WorldError("Keep an unobstructed route from the interior entry to its exit.")
                add("warning", prefix + ".interior", "Designed interiors require the separately built Pixelheart Interiors SMAPI companion and Stardew Valley 1.6.9 or later. Game behavior has to be playtested.")
            else:
                if not location["map"] or project_root is None:
                    raise WorldError("Import this place's TMX map and its local tilesheets before exporting.")
                bundle = map_bundle(asset_path(location["map"], project_root))
            if not location["spouse_room"]:
                known_dimensions[internal] = (bundle["width"], bundle["height"])
                known_dimensions[exported_location_id(location, character)] = (bundle["width"], bundle["height"])
            if location["spouse_room"]:
                spouse_rooms += 1
                if not character.get("romanceable"):
                    add("error", prefix + ".spouse_room", "Enable adult romance before assigning a spouse room.")
                if location["room_width"] != 6 or location["room_height"] != 9:
                    add("error", prefix, "Use a 6×9 tile spouse-room section.")
                if location["room_x"] + 6 > bundle["width"] or location["room_y"] + 9 > bundle["height"]:
                    add("error", prefix, "The spouse-room section falls outside this map.")
            else:
                entrance = location["entrance"]
                if not IDENTIFIER.fullmatch(entrance["map"]):
                    add("error", prefix + ".entrance.map", "Choose the existing map where the player enters this place.")
                entrance_key = (entrance["map"], entrance["x"], entrance["y"])
                if entrance.get("confirmed", True):
                    if entrance_key in entrances:
                        add("error", prefix + ".entrance", "Another place uses this entrance tile. Choose a different entrance so both places remain reachable.")
                    entrances.add(entrance_key)
                visited = {internal}
                cursor = entrance["map"]
                while cursor in locations_by_name:
                    if cursor in visited or locations_by_name[cursor]["spouse_room"]:
                        add("error", prefix + ".entrance.map", "Connect this place to a reachable outside map; a closed loop or spouse-room asset cannot be its only entrance.")
                        break
                    visited.add(cursor)
                    cursor = locations_by_name[cursor]["entrance"]["map"]
                for x, y in (("entry_x", "entry_y"), ("exit_x", "exit_y")):
                    if location[x] >= bundle["width"] or location[y] >= bundle["height"]:
                        add("error", prefix + "." + x, "Arrival and exit tiles must lie inside the imported map.")
                if (location["entry_x"], location["entry_y"]) == (location["exit_x"], location["exit_y"]):
                    add("error", prefix, "Use different arrival and exit tiles to avoid an immediate return warp.")
                if (entrance["x"], entrance["y"]) == (entrance["arrival_x"], entrance["arrival_y"]):
                    add("error", prefix + ".entrance", "The return arrival must differ from the entrance trigger tile.")
            add("warning", prefix, "Check this map's collision layers, entrance, return warp, and NPC routes in-game; the preview cannot verify pathfinding.")
        except (WorldError, OSError, ValueError) as exc:
            add("error", prefix + ".map", str(exc))
    if spouse_rooms > 1:
        add("error", "locations", "Assign only one spouse room to the primary character.")
    for index, location in enumerate(world["locations"]):
        if not location["spouse_room"] and location["entrance"]["map"] in known_dimensions:
            width, height = known_dimensions[location["entrance"]["map"]]
            for x, y in (("x", "y"), ("arrival_x", "arrival_y")):
                if location["entrance"][x] >= width or location["entrance"][y] >= height:
                    add("error", f"locations.{index}.entrance.{x}", "This entrance or return tile lies outside the connected custom map.")
    for prefix, authored in all_characters:
        _check_known_map_tiles(authored, known_dimensions, prefix, issues)
        from .interiors import reachable_tiles
        home = next((p for p in world["locations"] if "interior" in p and not p["spouse_room"]
                     and authored.get("home_map") in (p["internal_name"], exported_location_id(p, character))), None)
        if home:
            try:
                if (int(authored["home_x"]), int(authored["home_y"])) not in reachable_tiles(home["interior"]):
                    add("error", prefix + "home_x", "The resident's home position must have an unobstructed route from the interior entry.")
            except (KeyError, ValueError, TypeError):
                pass  # The character validator reports malformed coordinates.
    return issues


def _check_known_map_tiles(character, dimensions, prefix, issues):
    def integer(value):
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d{1,6}", value):
            return int(value)
        return None

    def check(location, x, y, field):
        if not isinstance(location, str) or location not in dimensions:
            return
        width, height = dimensions[location]
        x, y = integer(x), integer(y)
        if x is not None and y is not None and not (0 <= x < width and 0 <= y < height):
            issues.append({"level": "error", "field": prefix + field,
                           "message": f"This tile is outside {location}'s {width}×{height} map. Choose X 0–{width - 1} and Y 0–{height - 1}."})

    check(character.get("home_map"), character.get("home_x"), character.get("home_y"), "home_x")
    for index, stop in enumerate(character.get("schedule", []) if isinstance(character.get("schedule", []), list) else []):
        if isinstance(stop, dict):
            check(stop.get("location"), stop.get("x"), stop.get("y"), f"schedule.{index}.x")
    life = character.get("life", {})
    if isinstance(life, dict) and isinstance(life.get("routines", []), list):
        for index, routine in enumerate(life.get("routines", [])):
            if isinstance(routine, dict) and routine.get("enabled") is True and isinstance(routine.get("stops", []), list):
                for number, stop in enumerate(routine.get("stops", [])):
                    if isinstance(stop, dict):
                        check(stop.get("location"), stop.get("x"), stop.get("y"), f"life.routines.{index}.stops.{number}.x")
    def actor_name(name):
        return "$npc" if name in ("$npc", character.get("internal_name"), exported_npc_id(character)) else name
    for index, event in enumerate(character.get("events", []) if isinstance(character.get("events", []), list) else []):
        if not isinstance(event, dict) or not isinstance(event.get("story"), dict) or event["story"].get("stage") != "ready":
            continue
        story = event["story"]
        positions = {}
        for number, actor in enumerate(story.get("actors", []) if isinstance(story.get("actors", []), list) else []):
            if not isinstance(actor, dict) or not isinstance(actor.get("name"), str):
                continue
            x, y = integer(actor.get("x")), integer(actor.get("y"))
            check(event.get("location"), x, y, f"events.{index}.story.actors.{number}.x")
            if x is not None and y is not None:
                positions[actor_name(actor["name"])] = [x, y]
        for number, beat in enumerate(story.get("beats", []) if isinstance(story.get("beats", []), list) else []):
            if not isinstance(beat, dict) or beat.get("kind") != "move" or not isinstance(beat.get("actor"), str):
                continue
            actor = actor_name(beat["actor"])
            dx, dy = integer(beat.get("x")), integer(beat.get("y"))
            if actor in positions and dx is not None and dy is not None:
                positions[actor][0] += dx
                positions[actor][1] += dy
                check(event.get("location"), *positions[actor], f"events.{index}.story.beats.{number}.x")


def compile_world(world, character, project_root):
    """Compile imported maps and companion archives into one content pack."""
    from .exporting import build_mod_archive, _story_test_guide
    world = normalize_world(world)
    for location in world["locations"]:
        if "doorway" in location.get("interior", {}):
            from .interiors import doorway_exit
            location["exit_x"], location["exit_y"] = doorway_exit(location["interior"])
    backup_world = copy.deepcopy(world)
    issues = world_issues(world, character, project_root)
    if any(issue["level"] == "error" for issue in issues):
        raise WorldError("; ".join(issue["message"] for issue in issues if issue["level"] == "error"))
    patches, files, npc_fields, repeat_events, story_guides = [], {}, {}, [], []
    for index, entry in enumerate(world["characters"]):
        companion = world_character(entry["character"], world, character)
        age = companion.get("age", "adult")
        companion["age"] = "adult"
        blob = build_mod_archive(companion, *(asset_path(entry["artwork"][kind], project_root) for kind in ("portrait", "sprite")))
        folder = "[CP] " + companion["internal_name"] + "/"
        prefix = "assets/cast/" + hashlib.sha256(entry["id"].encode()).hexdigest()[:16] + "/"
        backup_world["characters"][index]["artwork"] = {"portrait": prefix + "portraits.png", "sprite": prefix + "sprites.png"}
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            content = json.loads(archive.read(folder + "content.json"))
            scene_patches = [patch for patch in content["Changes"] if patch["Target"].startswith("Data/Events/")]
            if scene_patches:
                story_guides.append(_story_test_guide(companion, scene_patches, exported_mod_id(character), exported_npc_id(companion)))
            for patch in content["Changes"]:
                if "FromFile" in patch:
                    patch["FromFile"] = prefix + patch["FromFile"].removeprefix("assets/")
                if patch["Target"] == "Data/Characters":
                    for npc in patch["Entries"].values():
                        npc["Age"] = age.title()
                patches.append(patch)
            for name in archive.namelist():
                if name.startswith(folder + "assets/"):
                    files[prefix + name.removeprefix(folder + "assets/")] = archive.read(name)
            repeat_events.extend(story_repeat_events(companion, mod_id=exported_mod_id(character)))
    prepared_interiors, map_targets, runtime_designs, interior_dependencies = {}, {}, {}, set()
    for location in world["locations"]:
        identity = exported_location_id(location, character)
        map_targets[location["internal_name"]] = ["Maps/" + identity]
        if "interior" in location:
            from .interiors import compile_interior
            prefix = "assets/maps/" + hashlib.sha256(location["id"].encode()).hexdigest()[:16] + "/"
            compiled = compile_interior(location["interior"], identity, exported_npc_id(character), project_root, prefix)
            protected = ([location["interior"]["spouse_stand"]] if location["spouse_room"] else
                         [location["interior"]["entry"], [location["exit_x"], location["exit_y"]]])
            if "doorway" in location["interior"]:
                protected.append(location["interior"]["doorway"])
            for authored in ([] if location["spouse_room"] else [character, *(c["character"] for c in world["characters"])]):
                if authored.get("home_map") == location["internal_name"]:
                    protected.append([int(authored["home_x"]), int(authored["home_y"])])
                stops = list(authored.get("schedule", []))
                for routine in authored.get("life", {}).get("routines", []):
                    stops.extend(routine.get("stops", []))
                for stop in stops:
                    if stop.get("location") == location["internal_name"]:
                        protected.append([int(stop["x"]), int(stop["y"])])
            compiled["runtime"]["protected_tiles"] = protected
            prepared_interiors[location["id"]] = compiled
            runtime_designs[identity] = compiled["runtime"]
            interior_dependencies.update(compiled["dependencies"])
            map_targets[location["internal_name"]].extend(v["map_asset"] for v in compiled["runtime"]["variants"])
    for index, location in enumerate(world["locations"]):
        prefix = "assets/maps/" + hashlib.sha256(location["id"].encode()).hexdigest()[:16] + "/"
        identity = exported_location_id(location, character)
        if location["id"] in prepared_interiors:
            compiled = prepared_interiors[location["id"]]
            files.update(compiled["files"])
            patches.extend(compiled["patches"])
            default_file = next(p["FromFile"] for p in compiled["patches"] if p["Target"] == compiled["map_asset"] and p["Action"] == "Load")
            patches.append({"Action": "Load", "Target": "Maps/" + identity, "FromFile": default_file})
            patches.extend({**patch, "Target": "Maps/" + identity}
                           for patch in copy.deepcopy(compiled["patches"])
                           if patch["Target"] == compiled["map_asset"] and patch["Action"] == "EditMap")
            backup_world["locations"][index]["map"] = None
            # Keep authoring references portable inside the editable project backup.
            from .interiors import interior_asset_references
            for reference in interior_asset_references(location["interior"]):
                files[reference] = _read_asset(asset_path(reference, project_root))
        else:
            source = asset_path(location["map"], project_root)
            bundle = map_bundle(source)
            backup_world["locations"][index]["map"] = prefix + bundle["entry"]
            files.update({prefix + name: payload for name, payload in bundle["files"].items()})
            patches.append({"Action": "Load", "Target": "Maps/" + identity, "FromFile": prefix + bundle["entry"]})
        if location["spouse_room"]:
            npc_fields["SpouseRoom"] = {"MapAsset": identity, "MapSourceRect": {
                "X": location["room_x"], "Y": location["room_y"], "Width": 6, "Height": 9}}
        else:
            patches.append({"Action": "EditData", "Target": "Data/Locations", "Entries": {
                identity: {"DisplayName": location["name"], "DefaultArrivalTile": {"X": location["entry_x"], "Y": location["entry_y"]},
                           "CreateOnLoad": {"MapPath": "Maps/" + identity,
                                            **({"Type": "StardewValley.Locations.DecoratableLocation"} if "interior" in location else {})}}}})
            entrance = location["entrance"]
            source_name = next((exported_location_id(other, character) for other in world["locations"]
                                if other["internal_name"] == entrance["map"]), entrance["map"])
            patches.extend([
                {"Action": "EditMap", "Target": ", ".join(map_targets.get(entrance["map"], ["Maps/" + source_name])), "AddWarps": [f"{entrance['x']} {entrance['y']} {identity} {location['entry_x']} {location['entry_y']}"]},
                {"Action": "EditMap", "Target": ", ".join(map_targets[location["internal_name"]]), "AddWarps": [f"{location['exit_x']} {location['exit_y']} {source_name} {entrance['arrival_x']} {entrance['arrival_y']}"]},
            ])
    dependencies = [{"UniqueID": item["id"], "IsRequired": item.get("required", True),
                     **({"MinimumVersion": item["minimum_version"]} if item.get("minimum_version") else {})}
                    for item in world["dependencies"]]
    if runtime_designs:
        patches.append({"Action": "EditData", "Target": "Pixelheart.Interiors/Designs", "Entries": runtime_designs})
        for identity in sorted(interior_dependencies | {"Pixelheart.Interiors"}):
            existing = next((d for d in dependencies if d["UniqueID"].casefold() == identity.casefold()), None)
            if existing is None:
                dependencies.append({"UniqueID": identity, "IsRequired": True,
                                     **({"MinimumVersion": "0.1.0"} if identity == "Pixelheart.Interiors" else {})})
            else:
                existing["IsRequired"] = True
                if identity == "Pixelheart.Interiors":
                    specified = existing.get("MinimumVersion", "0.0.0")
                    version = tuple(map(int, specified.split("-")[0].split("+")[0].split(".")))
                    if version < (0, 1, 0) or (version == (0, 1, 0) and "-" in specified):
                        existing["MinimumVersion"] = "0.1.0"
        files["INTERIOR_TESTING.txt"] = ("PIXELHEART INTERIORS — PLAYTEST REQUIRED\n\n"
            "Install the separately built Pixelheart.Interiors 0.1.0+ companion, Stardew Valley 1.6.9+, SMAPI 4.1+, "
            "Content Patcher, and every furniture provider declared by this pack. This archive does not include the companion DLL.\n\n"
            "On a disposable test save:\n"
            "1. Enter each residence and check floor/wall appearance, collision, entry, and return warp.\n"
            "2. Sit, rotate, collect, and replace furniture; test lights, storage, and installed-mod animations.\n"
            "3. Save/reload and confirm moved or collected furniture does not respawn.\n"
            "4. Use F8 to add/remove optional rooms in single-player. Occupied rooms must refuse removal.\n"
            "5. Follow the resident's schedules and verify every home destination remains reachable.\n"
            "6. After marriage, check the spouse room's actual position, furniture, and standing point.\n"
            "7. Apply wallpaper/flooring, change rooms, and reload; check that player choices persist.\n\n"
            "If placement was deferred, resolve the reported obstruction or missing item and run pixelheart_interiors_retry. "
            "Structural room changes are not enabled in multiplayer. Preview animations do not add custom game item behavior.\n").encode("utf-8")
    return {"patches": patches, "files": files, "npc_fields": npc_fields,
            "dependencies": dependencies, "repeat_events": repeat_events, "story_guides": story_guides, "world": backup_world}


def install_archive(payload, mods_directory, expected_mod_id):
    """Install one generated pack; update only a previous Pixelheart installation."""
    if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE_BYTES:
        raise WorldError("Choose a Pixelheart archive no larger than 256 MiB.")
    if not isinstance(expected_mod_id, str) or not expected_mod_id.startswith("Pixelheart.") or not IDENTIFIER.fullmatch(expected_mod_id):
        raise WorldError("Expected a valid Pixelheart mod identity.")
    mods = Path(mods_directory).expanduser()
    if not mods.is_dir() or mods.is_symlink():
        raise WorldError("Select an existing Mods folder that is not a symlink.")
    stage = None
    backup = None
    destination = None
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 4096 or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES:
                raise WorldError("The archive is empty or exceeds installation limits.")
            names = [PurePosixPath(item.filename) for item in entries]
            for item, name in zip(entries, names):
                relative_path(item.filename)
                if "\\" in item.filename or len(name.parts) < 2 or stat.S_ISLNK(item.external_attr >> 16):
                    raise WorldError("The archive contains an unsafe path or symlink.")
            roots = {name.parts[0] for name in names}
            if len(roots) != 1 or len({str(name).casefold() for name in names}) != len(names):
                raise WorldError("Install one pack with unique file paths at a time.")
            folder = next(iter(roots))
            if not folder.startswith("[CP] "):
                raise WorldError("This is not a Pixelheart content pack archive.")
            manifest = json.loads(archive.read(folder + "/manifest.json"))
            if manifest.get("UniqueID") != expected_mod_id:
                raise WorldError("The archive's mod identity does not match this project.")
            destination = mods / folder
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or not destination.is_dir():
                    raise WorldError("The destination is not an owned mod folder.")
                try:
                    marker = json.loads((destination / ".pixelheart-install.json").read_text())
                    existing = json.loads((destination / "manifest.json").read_text())
                except (OSError, ValueError):
                    raise WorldError("A folder with this name already exists and was not installed by Pixelheart. Move it aside before installing.") from None
                if marker.get("mod_id") != expected_mod_id or existing.get("UniqueID") != expected_mod_id:
                    raise WorldError("Refusing to replace a folder belonging to another mod.")
            stage = Path(tempfile.mkdtemp(prefix=".pixelheart-stage-", dir=mods))
            for item, name in zip(entries, names):
                target = stage.joinpath(*name.parts[1:])
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(item))
            (stage / ".pixelheart-install.json").write_text(json.dumps({"mod_id": expected_mod_id, "version": 1}) + "\n")
            if destination.exists():
                backups = mods.parent / "Pixelheart backups"
                if backups.is_symlink():
                    raise WorldError("The backup folder cannot be a symlink.")
                backups.mkdir(exist_ok=True)
                backup = backups / (folder + "-" + uuid.uuid4().hex[:12])
                os.replace(destination, backup)
            try:
                os.replace(stage, destination)
                stage = None
            except OSError:
                if backup is not None and not destination.exists():
                    os.replace(backup, destination)
                    backup = None
                raise
        return {"installed_path": destination, "backup_path": backup, "mod_id": expected_mod_id}
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeError) as exc:
        raise WorldError(f"Could not install this pack: {exc}") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
