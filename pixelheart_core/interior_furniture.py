"""Local furniture references and atlas previews for interior authoring.

This module independently implements the documented Stardew Valley 1.6
``Data/Furniture`` format. It does not supply game artwork or furniture code.
Furniture stays an installed item ID; preview rectangles never become map tiles.
Unspecified game defaults and rotation/animation layouts remain unresolved until
the author supplies explicit preview metadata.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys
import tempfile
from threading import RLock
import warnings

from PIL import Image, UnidentifiedImageError


MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_CATALOG_ITEMS = 20_000
MAX_TEXTURE_BYTES = 16 * 1024 * 1024
MAX_TEXTURE_PIXELS = 16_777_216
MAX_FRAMES = 256
MAX_DIMENSION_TILES = 128
MAX_FRAME_PIXELS = 2048
MAX_IMPORT_TEXTURES = 512
MAX_IMPORT_BYTES = 128 * 1024 * 1024
MAX_PREVIEW_CACHE_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_CACHE_ENTRIES = 64
MAX_DISCOVERY_ENTRIES = 512
MAX_DISCOVERY_EXPORTS = 16

_preview_cache = OrderedDict()
_preview_cache_bytes = 0
_preview_cache_lock = RLock()


class FurnitureValidationError(ValueError):
    """A furniture reference, local texture, or preview needs correction."""


def _text(value, label, maximum=256, *, empty=False):
    if not isinstance(value, str) or (not value and not empty) or len(value) > maximum:
        raise FurnitureValidationError(f"{label} must be text with {'0' if empty else '1'}–{maximum} characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise FurnitureValidationError(f"{label} cannot contain control characters.")
    return value


def qualified_furniture_id(value):
    """Normalize an installed furniture ID, rejecting other item types."""
    value = _text(value, "Furniture ID", 259)
    if value.startswith("(F)"):
        value = value[3:]
    if not value or len(value) > 256 or value.startswith("(") or value != value.strip():
        raise FurnitureValidationError("Use a furniture item ID, optionally beginning with (F).")
    return "(F)" + value


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise FurnitureValidationError(f"{label} must be an integer from {minimum} to {maximum}.")
    return value


def _dimensions(value, label):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise FurnitureValidationError(f"{label} must contain width and height in tiles, or be unresolved.")
    return [_integer(part, label, 1, MAX_DIMENSION_TILES) for part in value]


def _relative(value, label):
    value = _text(value, label, 1024).replace("\\", "/")
    path = PurePosixPath(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise FurnitureValidationError(f"{label} must stay inside its selected folder.")
    return path.as_posix()


def _contained(root, reference):
    reference = _relative(reference, "Asset path")
    try:
        root = Path(root).resolve()
        path = root.joinpath(*PurePosixPath(reference).parts)
        if not path.resolve().is_relative_to(root):
            raise FurnitureValidationError("Asset paths cannot escape the selected folder through symlinks.")
        return path
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if isinstance(exc, FurnitureValidationError):
            raise
        raise FurnitureValidationError(f"Cannot resolve the local asset path: {exc}") from exc


def validate_definition(value):
    """Return a detached JSON-compatible definition with explicit preview data.

    ``footprint`` and ``sprite_size`` are tile dimensions or ``None``. Explicit
    ``rotation_footprints`` map rotation indexes (as text) to tile dimensions;
    missing overrides retain the base footprint, with no inferred swapping.
    Each
    optional frame has a rotation index, pixel ``rect`` [x, y, width, height],
    and ``duration_ms``. Frames for each rotation play in supplied list order.
    ``preview_asset`` is local only; ``texture`` is the installed game's asset
    name. A missing preview does not imply a missing game furniture item.
    """
    if not isinstance(value, dict):
        raise FurnitureValidationError("A furniture definition must be an object.")
    item_id = qualified_furniture_id(value.get("id"))
    rotations = value.get("rotations", 1)
    if type(rotations) is not int or rotations not in (1, 2, 4):
        raise FurnitureValidationError("Furniture must have 1, 2, or 4 rotations.")
    rotation_footprints = value.get("rotation_footprints", {})
    if not isinstance(rotation_footprints, dict) or len(rotation_footprints) > rotations:
        raise FurnitureValidationError("Rotation footprints must map available rotation indexes to tile dimensions.")
    normalized_footprints = {}
    for key, dimensions in rotation_footprints.items():
        if not isinstance(key, str) or key not in {str(index) for index in range(rotations)}:
            raise FurnitureValidationError("Each rotation footprint needs an available rotation index as text.")
        dimensions = _dimensions(dimensions, "Rotation footprint")
        if dimensions is None:
            raise FurnitureValidationError("An explicit rotation footprint must contain width and height.")
        normalized_footprints[key] = dimensions
    placement = value.get("placement", "default")
    if placement not in ("default", "indoors", "outdoors", "both"):
        raise FurnitureValidationError("Placement must be default, indoors, outdoors, or both.")
    sprite_index = value.get("sprite_index")
    if sprite_index is not None:
        _integer(sprite_index, "Sprite index", 0, 2_147_483_647)
    texture = _relative(value.get("texture", "TileSheets/furniture"), "Game texture")
    preview_asset = value.get("preview_asset", "")
    if preview_asset:
        preview_asset = _relative(preview_asset, "Preview asset")
        if PurePosixPath(preview_asset).suffix.lower() != ".png":
            raise FurnitureValidationError("Preview assets must be PNG files.")
    elif preview_asset != "":
        raise FurnitureValidationError("Preview asset must be a relative PNG path or empty text.")
    frames = value.get("frames", [])
    if not isinstance(frames, list) or len(frames) > MAX_FRAMES:
        raise FurnitureValidationError(f"Use a list with at most {MAX_FRAMES} preview frames.")
    result_frames = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise FurnitureValidationError("Each preview frame must be an object.")
        rotation = _integer(frame.get("rotation", 0), "Frame rotation", 0, rotations - 1)
        rect = frame.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise FurnitureValidationError("A frame rectangle must contain x, y, width, and height in pixels.")
        rect = [_integer(rect[0], "Frame x", 0, 65535),
                _integer(rect[1], "Frame y", 0, 65535),
                _integer(rect[2], "Frame width", 1, MAX_FRAME_PIXELS),
                _integer(rect[3], "Frame height", 1, MAX_FRAME_PIXELS)]
        duration = _integer(frame.get("duration_ms", 100), "Frame duration", 1, 600_000)
        result_frames.append({"rotation": rotation, "rect": rect, "duration_ms": duration})
    mod_data = value.get("mod_data", {})
    if not isinstance(mod_data, dict) or len(mod_data) > 128:
        raise FurnitureValidationError("Furniture mod data must be an object with at most 128 entries.")
    mod_data = {_text(key, "Mod data key", 256): _text(item, "Mod data value", 4096, empty=True)
                for key, item in mod_data.items()}
    return {
        "id": item_id,
        "name": _text(value.get("name", item_id[3:]), "Furniture name"),
        "kind": _text(value.get("kind", "other"), "Furniture kind", 64),
        "footprint": _dimensions(value.get("footprint"), "Furniture footprint"),
        "rotation_footprints": normalized_footprints,
        "sprite_size": _dimensions(value.get("sprite_size"), "Sprite size"),
        "rotations": rotations,
        "sprite_index": sprite_index,
        "texture": texture,
        "preview_asset": preview_asset,
        "frames": result_frames,
        "placement": placement,
        "dependency": _text(value.get("dependency", ""), "Mod dependency", empty=True),
        "mod_data": mod_data,
    }


def validate_surface(value):
    """Normalize one resolved wallpaper strip or complete repeating floor tile.

    Rectangles are observed from the installed game's item registry, rather
    than guessed from numeric item IDs. PNGs retain the same containment and
    byte limits as furniture previews.
    """
    if not isinstance(value, dict):
        raise FurnitureValidationError("A wall or floor pattern must be an object.")
    kind = value.get("kind")
    if kind not in ("wall", "floor"):
        raise FurnitureValidationError("Choose a wall or floor pattern.")
    identity = _text(value.get("id"), "Pattern ID", 260)
    prefix = "(WP)" if kind == "wall" else "(FL)"
    if not identity.startswith(prefix) or not identity[len(prefix):].strip():
        raise FurnitureValidationError("The pattern ID must identify its wallpaper or flooring item.")
    rect = value.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise FurnitureValidationError("A pattern needs its complete preview rectangle.")
    rect = [_integer(part, "Pattern rectangle", 0, 65535) for part in rect]
    if rect[2:] != ([16, 48] if kind == "wall" else [32, 32]):
        raise FurnitureValidationError("Wallpaper previews are 16 × 48 pixels; flooring previews are 32 × 32 pixels.")
    preview = _relative(value.get("preview_asset"), "Pattern preview")
    if PurePosixPath(preview).suffix.lower() != ".png":
        raise FurnitureValidationError("Pattern previews must be PNG files.")
    return {
        "id": identity,
        "name": _text(value.get("name", identity), "Pattern name"),
        "kind": kind,
        "texture": _relative(value.get("texture"), "Pattern game texture"),
        "preview_asset": preview,
        "rect": rect,
        "dependency": _text(value.get("dependency", ""), "Mod dependency", empty=True),
    }


def _read_regular(path, maximum, label):
    try:
        path = Path(path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise FurnitureValidationError(f"Select a regular local {label} file.")
        if path.stat().st_size > maximum:
            raise FurnitureValidationError(f"The {label} exceeds the {maximum // 1024 // 1024} MB limit.")
        with path.open("rb") as stream:
            payload = stream.read(maximum + 1)
        if len(payload) > maximum:
            raise FurnitureValidationError(f"The {label} exceeds its size limit.")
        return payload
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, FurnitureValidationError):
            raise
        raise FurnitureValidationError(f"The local {label} cannot be read: {exc}") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FurnitureValidationError(f"The furniture JSON contains a duplicate key: {key!r}.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise FurnitureValidationError(f"Unsupported JSON constant: {value}.")


def _native_dimensions(text, label):
    if text.strip() == "-1":
        return None
    try:
        return _dimensions([int(part) for part in text.split()], label)
    except ValueError as exc:
        raise FurnitureValidationError(f"{label} must be two positive tile dimensions or -1.") from exc


def _catalog_json(path):
    payload = _read_regular(path, MAX_CATALOG_BYTES, "furniture catalogue")
    try:
        return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise FurnitureValidationError("Choose a valid UTF-8 furniture JSON file.") from exc


def read_native_catalog(path):
    """Read a user-selected, unpacked 1.6 Data/Furniture JSON dictionary.

    Returns ``{"definitions": [...], "warnings": [...]}``. Invalid records are
    skipped with a warning; invalid JSON/top-level structure raises an error.
    No textures are read here. Rotation rectangles, animation, type-default
    dimensions and mod dependencies are deliberately not inferred.
    """
    data = _catalog_json(path)
    if not isinstance(data, dict) or len(data) > MAX_CATALOG_ITEMS:
        raise FurnitureValidationError(f"The furniture catalogue must be an object with at most {MAX_CATALOG_ITEMS} items.")
    definitions, messages, seen = [], [], set()
    for key, encoded in sorted(data.items()):
        try:
            if not isinstance(encoded, str) or len(encoded) > 16_384:
                raise FurnitureValidationError("Native furniture records must be slash-delimited strings.")
            fields = encoded.split("/")
            if len(fields) < 7:
                raise FurnitureValidationError("The native furniture record is missing required fields.")
            restriction = int(fields[6])
            if restriction not in (-1, 0, 1, 2):
                raise FurnitureValidationError("Unknown native placement restriction.")
            definition = validate_definition({
                "id": key,
                "name": fields[7] if len(fields) > 7 and fields[7] else fields[0],
                "kind": fields[1],
                "sprite_size": _native_dimensions(fields[2], "Sprite size"),
                "footprint": _native_dimensions(fields[3], "Furniture footprint"),
                "rotations": int(fields[4]),
                "placement": {-1: "default", 0: "indoors", 1: "outdoors", 2: "both"}[restriction],
                "sprite_index": int(fields[8]) if len(fields) > 8 and fields[8] else None,
                "texture": fields[9] if len(fields) > 9 and fields[9] else "TileSheets/furniture",
            })
            if definition["id"] in seen:
                raise FurnitureValidationError("This qualified furniture ID is already present.")
            seen.add(definition["id"])
            definitions.append(definition)
            if definition["footprint"] is None or definition["sprite_size"] is None:
                messages.append(f"{definition['id']}: type-default dimensions are unresolved; supply explicit dimensions for the designer.")
        except (FurnitureValidationError, ValueError) as exc:
            messages.append(f"{str(key)[:256]}: skipped invalid furniture record ({exc}).")
    if definitions:
        messages.append("Preview rectangles and animation frames must be supplied explicitly; catalogue metadata alone does not establish them.")
    return {"definitions": definitions, "warnings": messages}


def _read_texture(path):
    payload = _read_regular(path, MAX_TEXTURE_BYTES, "PNG texture")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise FurnitureValidationError("Furniture previews require a PNG atlas.")
                if image.width * image.height > MAX_TEXTURE_PIXELS:
                    raise FurnitureValidationError("The PNG atlas exceeds the safe pixel limit.")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise FurnitureValidationError("Use a static PNG atlas and explicit animation frames, not an animated PNG.")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                return payload, image.convert("RGBA")
    except FurnitureValidationError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise FurnitureValidationError(f"The PNG atlas cannot be decoded safely: {exc}") from exc


def import_texture(source, project_dir):
    """Copy a verified PNG byte-for-byte into portable private project assets."""
    payload, image = _read_texture(source)
    image.close()
    return _store_texture(payload, project_dir)


def _texture_destination(payload, project_dir):
    reference = f"world_assets/interiors/textures/{hashlib.sha256(payload).hexdigest()}.png"
    destination = _contained(project_dir, reference)
    if destination.is_symlink():
        raise FurnitureValidationError("A texture destination cannot be a symlink.")
    if destination.exists():
        if _read_regular(destination, MAX_TEXTURE_BYTES, "stored PNG texture") != payload:
            raise FurnitureValidationError("An existing content-addressed texture has unexpected contents.")
    return reference, destination


def _store_texture(payload, project_dir):
    reference, destination = _texture_destination(payload, project_dir)
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after creating directories, including a pre-existing symlink.
        destination = _contained(project_dir, reference)
        if destination.is_symlink():
            raise FurnitureValidationError("A texture destination cannot be a symlink.")
        if destination.exists():
            if _read_regular(destination, MAX_TEXTURE_BYTES, "stored PNG texture") != payload:
                raise FurnitureValidationError("An existing content-addressed texture has unexpected contents.")
            return reference
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".texture-", suffix=".png", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, destination)
        return reference
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, FurnitureValidationError):
            raise
        raise FurnitureValidationError(f"The project texture could not be saved: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _check_frames(definition, image):
    _check_frame_dimensions(definition, image.size)


def _check_frame_dimensions(definition, dimensions):
    atlas_width, atlas_height = dimensions
    for frame in definition["frames"]:
        x, y, width, height = frame["rect"]
        if x + width > atlas_width or y + height > atlas_height:
            raise FurnitureValidationError("A preview frame extends beyond its PNG atlas.")


def import_furniture_library(path, project_dir):
    """Import resolved game furniture or a native Data/Furniture dictionary.

    Resolved libraries use ``format: pixelheart-interior-library``, ``version:
    1``, and a ``definitions`` list in this module's schema. Referenced preview
    PNG paths are relative to the JSON's directory. Every definition and PNG is
    preflighted before any project texture is copied. Invalid bundle entries
    fail the import instead of silently losing catalogue items.

    Native dictionaries retain ``read_native_catalog`` semantics and do not
    infer missing preview data. Neither format downloads or generates artwork.
    """
    data = _catalog_json(path)
    if not isinstance(data, dict) or data.get("format") != "pixelheart-interior-library":
        return read_native_catalog(path)
    if type(data.get("version")) is not int or data["version"] != 1:
        raise FurnitureValidationError("This furniture library version is not supported.")
    entries = data.get("definitions")
    if not isinstance(entries, list) or len(entries) > MAX_CATALOG_ITEMS:
        raise FurnitureValidationError(f"A furniture library needs a list of at most {MAX_CATALOG_ITEMS} definitions.")
    surface_entries = data.get("surfaces", [])
    if not isinstance(surface_entries, list) or len(surface_entries) > MAX_CATALOG_ITEMS:
        raise FurnitureValidationError(f"A pattern library needs a list of at most {MAX_CATALOG_ITEMS} surfaces.")
    definitions, surfaces, seen, textures, messages = [], [], set(), {}, []
    supplied_warnings = data.get("warnings", [])
    if not isinstance(supplied_warnings, list) or len(supplied_warnings) > 1000:
        raise FurnitureValidationError("Library notes must be a list with at most 1000 entries.")
    for note in supplied_warnings:
        messages.append(_text(note, "Library note", 1024))
    total_bytes = 0
    source_root = Path(path).parent

    def prepare_texture(reference):
        nonlocal total_bytes
        if reference not in textures:
            if len(textures) >= MAX_IMPORT_TEXTURES:
                raise FurnitureValidationError(f"Import at most {MAX_IMPORT_TEXTURES} unique PNG textures at a time.")
            payload, image = _read_texture(_contained(source_root, reference))
            try:
                size = image.size
            finally:
                image.close()
            total_bytes += len(payload)
            if total_bytes > MAX_IMPORT_BYTES:
                raise FurnitureValidationError("The combined PNG textures exceed the 128 MB import limit.")
            target, destination = _texture_destination(payload, project_dir)
            for directory in (destination.parent, *destination.parents):
                if directory.exists() and not directory.is_dir():
                    raise FurnitureValidationError("A file occupies a required project texture directory.")
            textures[reference] = (payload, size, target)
        return textures[reference]

    for entry in entries:
        definition = validate_definition(entry)
        if definition["id"] in seen:
            raise FurnitureValidationError("Furniture library IDs must be unique.")
        seen.add(definition["id"])
        reference = definition["preview_asset"]
        if reference:
            _payload, size, target = prepare_texture(reference)
            _check_frame_dimensions(definition, size)
            definition["preview_asset"] = target
        else:
            messages.append(f"{definition['id']}: no preview PNG was supplied; the installed furniture reference was preserved.")
        definitions.append(definition)
    for entry in surface_entries:
        surface = validate_surface(entry)
        if surface["id"] in seen:
            raise FurnitureValidationError("Pattern library IDs must be unique.")
        seen.add(surface["id"])
        _payload, size, target = prepare_texture(surface["preview_asset"])
        x, y, width, height = surface["rect"]
        if x + width > size[0] or y + height > size[1]:
            raise FurnitureValidationError("A pattern extends beyond its PNG atlas.")
        surface["preview_asset"] = target
        surfaces.append(surface)
    # Copy only the immutable bytes that were validated. A source file changed
    # after preflight cannot replace those bytes or their recorded dimensions.
    for payload, _size, _target in textures.values():
        _store_texture(payload, project_dir)
    return {"definitions": definitions, "surfaces": surfaces, "warnings": messages}


def attach_texture(definition, source, project_dir):
    """Validate all authored frame bounds before attaching a portable texture."""
    result = validate_definition(definition)
    _, image = _read_texture(source)
    try:
        _check_frames(result, image)
    finally:
        image.close()
    result["preview_asset"] = import_texture(source, project_dir)
    return result


def import_catalog_textures(definitions, texture_dir, project_dir):
    """Attach PNGs from a chosen folder using game asset names as relative paths.

    Example: ``TileSheets/furniture`` resolves to
    ``<selected folder>/TileSheets/furniture.png``. The lookup never searches
    elsewhere, follows no escaping symlinks, and never guesses frame geometry.
    """
    if not isinstance(definitions, list) or len(definitions) > MAX_CATALOG_ITEMS:
        raise FurnitureValidationError("Choose a bounded list of furniture definitions.")
    result, messages, imported, failed = [], [], {}, {}
    total_bytes = 0
    for entry in definitions:
        definition = validate_definition(entry)
        texture = definition["texture"]
        try:
            if texture in failed:
                raise FurnitureValidationError(failed[texture])
            if texture not in imported:
                source = _contained(texture_dir, texture + ".png")
                if len(imported) >= MAX_IMPORT_TEXTURES:
                    raise FurnitureValidationError(f"Import at most {MAX_IMPORT_TEXTURES} unique PNG textures at a time.")
                # Bound the complete import, not just each individual image.
                payload = _read_regular(source, MAX_TEXTURE_BYTES, "PNG texture")
                if total_bytes + len(payload) > MAX_IMPORT_BYTES:
                    raise FurnitureValidationError("The combined PNG textures exceed the 128 MB import limit.")
                imported[texture] = import_texture(source, project_dir)
                total_bytes += len(payload)
            reference = imported[texture]
            _, atlas = _read_texture(_contained(project_dir, reference)) if definition["frames"] else (None, None)
            if atlas is not None:
                try:
                    _check_frames(definition, atlas)
                finally:
                    atlas.close()
            definition["preview_asset"] = reference
        except FurnitureValidationError as exc:
            if texture not in imported:
                failed[texture] = str(exc)
            messages.append(f"{definition['id']}: preview unavailable ({exc}).")
        result.append(definition)
    return {"definitions": result, "warnings": messages}


def definition_assets(definition):
    """Return the optional portable PNG reference used by this definition."""
    definition = validate_definition(definition)
    return [definition["preview_asset"]] if definition["preview_asset"] else []


def frame_at(definition, rotation=0, elapsed_ms=0):
    """Select an explicit frame deterministically, using half-open time spans."""
    definition = validate_definition(definition)
    _integer(rotation, "Preview rotation", 0, definition["rotations"] - 1)
    _integer(elapsed_ms, "Animation time", 0, 9_223_372_036_854_775_807)
    frames = [frame for frame in definition["frames"] if frame["rotation"] == rotation]
    if not frames:
        raise FurnitureValidationError("No explicit preview frames exist for this rotation.")
    position = elapsed_ms % sum(frame["duration_ms"] for frame in frames)
    for frame in frames:
        if position < frame["duration_ms"]:
            return frame
        position -= frame["duration_ms"]
    raise AssertionError("A bounded animation time must select a frame.")


def clear_preview_cache():
    """Release verified decoded atlases, for example when closing a project."""
    global _preview_cache_bytes
    with _preview_cache_lock:
        for _signature, image, _size in _preview_cache.values():
            image.close()
        _preview_cache.clear()
        _preview_cache_bytes = 0


@contextmanager
def _preview_atlas(path):
    """Hold a bounded verified RGBA atlas only while a caller makes its crop."""
    global _preview_cache_bytes
    with _preview_cache_lock:
        try:
            details = path.stat()
        except OSError as exc:
            raise FurnitureValidationError(f"The preview PNG cannot be read: {exc}") from exc
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_TEXTURE_BYTES:
            raise FurnitureValidationError("Preview PNGs must be regular files within the texture size limit.")
        signature = (details.st_dev, details.st_ino, details.st_size,
                     details.st_mtime_ns, details.st_ctime_ns)
        key = str(path)
        previous = _preview_cache.pop(key, None)
        if previous is not None:
            old_signature, image, decoded_bytes = previous
            if old_signature == signature:
                _preview_cache[key] = previous
                yield image
                return
            image.close()
            _preview_cache_bytes -= decoded_bytes
        _, image = _read_texture(path)
        decoded_bytes = image.width * image.height * 4
        # Images exceeding the cache budget still have the normal PNG bounds,
        # but are released after this crop instead of displacing the cache.
        if decoded_bytes > MAX_PREVIEW_CACHE_BYTES or MAX_PREVIEW_CACHE_ENTRIES < 1:
            try:
                yield image
            finally:
                image.close()
            return
        while (_preview_cache and (_preview_cache_bytes + decoded_bytes > MAX_PREVIEW_CACHE_BYTES
                                   or len(_preview_cache) >= MAX_PREVIEW_CACHE_ENTRIES)):
            _, (_, old_image, old_size) = _preview_cache.popitem(last=False)
            old_image.close()
            _preview_cache_bytes -= old_size
        _preview_cache[key] = (signature, image, decoded_bytes)
        _preview_cache_bytes += decoded_bytes
        yield image


def preview_frame(definition, project_dir, rotation=0, elapsed_ms=0):
    """Return the selected RGBA crop; callers own and should close the image."""
    definition = validate_definition(definition)
    if not definition["preview_asset"]:
        raise FurnitureValidationError("Attach a local PNG atlas to preview this furniture.")
    frame = frame_at(definition, rotation, elapsed_ms)
    # Containment and current file metadata are checked even on cache hits.
    with _preview_atlas(_contained(project_dir, definition["preview_asset"])) as image:
        _check_frames(definition, image)
        x, y, width, height = frame["rect"]
        return image.crop((x, y, x + width, y + height))


def preview_surface(surface, project_dir):
    """Return a complete wallpaper strip or repeating 2 × 2 flooring pattern."""
    surface = validate_surface(surface)
    with _preview_atlas(_contained(project_dir, surface["preview_asset"])) as image:
        x, y, width, height = surface["rect"]
        if x + width > image.width or y + height > image.height:
            raise FurnitureValidationError("A pattern extends beyond its PNG atlas.")
        return image.crop((x, y, x + width, y + height))


def _discovery_children(directory):
    """Bounded immediate children; never recurse into arbitrary user folders."""
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DISCOVERY_ENTRIES:
                    break
                if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                    yield Path(entry.path)
    except (OSError, ValueError):
        return


def _discovery_file(root, relative):
    try:
        path = _contained(root, relative)
        # A regular file inside the known folder is enough for discovery.
        # Full schema and PNG validation occurs transactionally on import.
        if path.is_symlink() or not path.is_file():
            return None
        details = path.stat()
        if details.st_size > MAX_CATALOG_BYTES:
            return None
        data = _catalog_json(path)
        if (isinstance(data, dict) and data.get("format") == "pixelheart-interior-library"
                and type(data.get("version")) is int and data["version"] == 1
                and isinstance(data.get("definitions"), list)):
            return path.resolve()
    except (FurnitureValidationError, OSError, ValueError, RuntimeError):
        pass
    return None


def _libraries_in_companion(root):
    for relative in ("cache/library/library.json", "library.json"):
        found = _discovery_file(root, relative)
        if found is not None:
            yield found
    try:
        exports = _contained(root, "exports")
        folders = sorted(_discovery_children(exports), key=lambda path: path.name, reverse=True)
        for folder in folders[:MAX_DISCOVERY_EXPORTS]:
            found = _discovery_file(folder, "library.json")
            if found is not None:
                yield found
    except (FurnitureValidationError, OSError, ValueError, RuntimeError):
        pass


def _libraries_at_selected_path(selected):
    try:
        root = Path(selected).expanduser().resolve(strict=True)
        if root.is_file():
            found = _discovery_file(root.parent, root.name)
            if found is not None:
                yield found
            return
        if not root.is_dir():
            return
        # A remembered Content Patcher export folder is a known sibling of Mods.
        if root.name == "patch export":
            root = root.parent
        yield from _libraries_in_companion(root)
        if root.name == "exports":
            for folder in sorted(_discovery_children(root), key=lambda path: path.name, reverse=True)[:MAX_DISCOVERY_EXPORTS]:
                found = _discovery_file(folder, "library.json")
                if found is not None:
                    yield found
        mods_roots = [root] if root.name.casefold() == "mods" else []
        for relative in ("Mods", "Contents/MacOS/Mods", "Stardew Valley.app/Contents/MacOS/Mods"):
            try:
                mods_roots.append(_contained(root, relative))
            except FurnitureValidationError:
                continue
        for mods_root in mods_roots:
            # Identify a renamed companion by its own manifest. Only immediate
            # mod folders are inspected; no game saves, projects or user files.
            for folder in _discovery_children(mods_root):
                try:
                    manifest_path = _contained(folder, "manifest.json")
                    if manifest_path.is_symlink():
                        continue
                    payload = _read_regular(manifest_path, 64 * 1024, "mod manifest")
                    manifest = json.loads(payload.decode("utf-8-sig"))
                    if isinstance(manifest, dict) and manifest.get("UniqueID") == "Pixelheart.Interiors":
                        yield from _libraries_in_companion(folder)
                except (FurnitureValidationError, OSError, UnicodeError, ValueError, RecursionError):
                    continue
    except (OSError, ValueError, RuntimeError, TypeError):
        return


def discover_furniture_libraries(configured_paths=(), *, include_standard_paths=True, home=None, platform=None):
    """Find local companion libraries in selected or standard game locations.

    This reads only exact known game paths, immediate Mods manifests and the
    companion's bounded export/cache directories. It neither walks a home
    directory nor imports anything into a project. Results are newest first;
    callers can offer connection in one action and use the normal importer.

    ``home`` and ``platform`` are injectable for deterministic offline tests.
    Installation paths belong in per-machine settings, never project JSON.
    """
    if isinstance(configured_paths, (str, Path)):
        configured_paths = [configured_paths]
    roots = [path for path in list(configured_paths or ())[:16] if path]
    if include_standard_paths:
        user = Path(home) if home is not None else Path.home()
        system = platform or sys.platform
        if system == "darwin":
            roots.extend((user / "Library/Application Support/Steam/steamapps/common/Stardew Valley", Path("/Applications/Stardew Valley.app")))
        elif system.startswith("linux"):
            roots.extend((user / ".local/share/Steam/steamapps/common/Stardew Valley", user / ".steam/steam/steamapps/common/Stardew Valley", user / "GOG Games/Stardew Valley"))
        elif system == "win32":
            roots.extend((Path("C:/Program Files (x86)/Steam/steamapps/common/Stardew Valley"), Path("C:/Program Files/Steam/steamapps/common/Stardew Valley"), Path("C:/GOG Games/Stardew Valley")))
    found = {}
    for root in roots:
        for path in _libraries_at_selected_path(root):
            try:
                found[path] = path.stat().st_mtime_ns
            except OSError:
                continue
    return sorted(found, key=lambda path: (found[path], str(path)), reverse=True)


def resolve_furniture_library(path):
    """Resolve a chosen game, Mods, companion or library folder in one step."""
    libraries = discover_furniture_libraries([path], include_standard_paths=False)
    if libraries:
        return libraries[0]
    raise FurnitureValidationError(
        "No ready furniture library was found here. Install Pixelheart Interiors in your game's Mods folder, "
        "launch Stardew through SMAPI, and load a save once. Then choose the game folder again."
    )
