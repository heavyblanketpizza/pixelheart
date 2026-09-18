"""Portable JSON projects and local artwork, independent of the desktop UI.

Artwork references are relative to the JSON file. A reference is either a string
or ``{"original": path, "prepared": path | None, "selected": "original"}``.
The selected value may be ``prepared`` when a prepared file is available. Optional
``artwork.variants`` contains seasonal and beach sets with their own portrait and
sprite references. Unknown JSON metadata is retained throughout; storage does
not require an export-ready character. None of the public functions mutate the
supplied document.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
from datetime import datetime, timezone
import uuid

from .validation import EDITABLE_FIELDS, NESTED_FIELDS, DraftValidationError, infer_legacy_gender, validate_draft
from .catalog import validate_catalog, CatalogValidationError
from .provenance import source_metadata
from .world import WorldError, normalize_world, world_asset_references, asset_path as world_asset_path, copy_world_assets


PROJECT_FORMAT = "pixelheart-project"
PROJECT_VERSION = 1
PROJECT_FILENAME = "character.json"
MAX_PROJECT_BYTES = 64 * 1024 * 1024
ARTWORK_KINDS = {"portrait", "sprite"}
APPEARANCE_VARIANTS = {
    "spring": "Spring",
    "summer": "Summer",
    "fall": "Fall",
    "winter": "Winter",
    "beach": "Beach",
}
_NESTED_KEYS = {
    "dialogues": {"id", "trigger", "text", "source", "source_history"},
    "schedule": {"id", "time", "location", "x", "y", "facing", "activity"},
    "events": {"id", "name", "hearts", "location", "description", "story"},
    "relationships": {"id", "name", "relation", "description", "story"},
}


class ProjectError(ValueError):
    """A project or artwork file could not be safely read or saved."""


def new_project() -> dict:
    """Return a fresh adult character with stable IDs and editable starter text."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "character": {
            "id": str(uuid.uuid4()),
            "name": "New character",
            "internal_name": "NewCharacter",
            "tagline": "",
            "pronouns": "they/them",
            "gender": "Undefined",
            "occupation": "",
            "bio": "",
            "age": "adult",
            "romanceable": True,
            "season": "spring",
            "day": 1,
            "manners": "neutral",
            "social_anxiety": "neutral",
            "optimism": "neutral",
            "home_map": "Town",
            "home_x": 32,
            "home_y": 62,
            "dialogues": [{"id": str(uuid.uuid4()), "trigger": "Introduction", "text": "Hello! It's lovely to meet you.$h"}],
            "schedule": [{"id": str(uuid.uuid4()), "time": "600", "location": "Town", "x": 32, "y": 62, "facing": "down", "activity": ""}],
            "gifts": {"love": [], "like": [], "dislike": [], "hate": []},
            "events": [],
            "relationships": [],
            "created_at": now,
            "updated_at": now,
        },
        "artwork": {"portrait": None, "sprite": None},
    }


def project_path(path: str | os.PathLike) -> Path:
    """Resolve a JSON filename or a project folder (including a new folder)."""
    candidate = Path(path).expanduser()
    if candidate.is_dir() or not candidate.suffix:
        candidate = candidate / PROJECT_FILENAME
    return candidate.absolute()


def _check_json(value, ancestors=None):
    """Reject lossy JSON conversion, nonfinite numbers and circular structures."""
    if type(value) is str:
        value.encode("utf-8")
        return
    if value is None or type(value) in (int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise ProjectError("Project values must be JSON objects, lists, text, or finite numbers.")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ProjectError("Project data contains a circular reference.")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict) and any(not isinstance(key, str) for key in value):
            raise ProjectError("Project object keys must be text.")
        if isinstance(value, dict):
            for key in value:
                key.encode("utf-8")
        for item in value.values() if isinstance(value, dict) else value:
            _check_json(item, ancestors)
    finally:
        ancestors.remove(id(value))


def _json_copy(value):
    try:
        _check_json(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError(f"Project data cannot be serialized: {exc}") from exc


def _relative_path(reference: str) -> Path:
    if not isinstance(reference, str) or not reference or "\x00" in reference:
        raise ProjectError("Artwork paths must be nonempty relative text paths.")
    # Interpret separators consistently even when a Windows project opens on Unix.
    normalized = reference.replace("\\", "/")
    path = Path(normalized)
    if (path.is_absolute() or PureWindowsPath(reference).drive or
            ".." in path.parts or ":" in normalized or not path.parts):
        raise ProjectError("Artwork paths must stay inside the project folder.")
    return path


def _asset_path(reference: str, root: Path) -> Path:
    path = root / _relative_path(reference)
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ProjectError("Artwork paths must stay inside the project folder; external symlinks are not allowed.")
    except (OSError, RuntimeError) as exc:
        raise ProjectError(f"Cannot resolve artwork path: {exc}") from exc
    return path


def _validate_artwork(record):
    if record is None:
        return
    if isinstance(record, str):
        _relative_path(record)
        return
    if not isinstance(record, dict):
        raise ProjectError("Artwork must be a relative path, a preparation record, or null.")
    for key in ("original", "prepared"):
        if record.get(key) is not None:
            _relative_path(record[key])
    selected = record.get("selected", "original")
    if not isinstance(selected, str) or selected not in {"original", "prepared"}:
        raise ProjectError("Artwork selection must be 'original' or 'prepared'.")
    if record.get(selected) is None:
        raise ProjectError(f"Artwork selection '{selected}' needs a relative file path.")
    if record.get("original") is None:
        raise ProjectError("An artwork preparation record must preserve its original file path.")
    try:
        record.update(source_metadata(record))
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc


def _validate_variants(artwork):
    variants = artwork.get("variants", {})
    if not isinstance(variants, dict):
        raise ProjectError("Artwork appearance variants must be a JSON object.")
    for variant, appearance in variants.items():
        if variant not in APPEARANCE_VARIANTS:
            raise ProjectError(f"Unknown artwork appearance variant: {variant!r}.")
        if not isinstance(appearance, dict):
            raise ProjectError(f"The {APPEARANCE_VARIANTS[variant]} artwork set must be a JSON object.")
        for kind in ARTWORK_KINDS:
            _validate_artwork(appearance.get(kind))
    return variants


def _artwork_sets(artwork):
    """Iterate the default and optional sets in an already validated document."""
    yield artwork
    yield from artwork.get("variants", {}).values()


def _validate_character(character):
    if not isinstance(character, dict):
        raise ProjectError("The project's character must be a JSON object.")
    for entry in character.get("dialogues", []) if isinstance(character.get("dialogues", []), list) else []:
        if isinstance(entry, dict):
            try:
                entry.update(source_metadata(entry))
            except ValueError as exc:
                raise ProjectError(str(exc)) from exc
    if character.get("age", "adult") != "adult":
        raise ProjectError("Pixelheart creates adult love-interest NPCs only. Choose adult.")
    for key in ("id", "created_at", "updated_at"):
        if key in character and (not isinstance(character[key], str) or not character[key] or "\x00" in character[key]):
            raise ProjectError(f"Character {key} must be nonempty text.")
    # Validate known fields without dropping extension metadata. Empty identity
    # fields are useful while authoring; export validation explains their errors.
    authored = {key: value for key, value in character.items() if key in EDITABLE_FIELDS}
    for key in ("name", "internal_name", "home_map"):
        if isinstance(authored.get(key), str) and not authored[key].strip():
            authored.pop(key)
    for key, known in _NESTED_KEYS.items():
        if isinstance(authored.get(key), list) and key not in {"events", "relationships"}:
            authored[key] = [{field: item for field, item in entry.items() if field in known}
                             if isinstance(entry, dict) else entry for entry in authored[key]]
    if isinstance(authored.get("gifts"), dict):
        authored["gifts"] = {key: value for key, value in authored["gifts"].items()
                             if key in {"love", "like", "dislike", "hate"}}
    try:
        return validate_draft(authored)
    except DraftValidationError as exc:
        details = "; ".join(f"{key}: {message}" for key, message in exc.errors.items())
        raise ProjectError(f"Invalid character data: {details}") from exc


def _document(value) -> dict:
    document = _json_copy(value)
    if not isinstance(document, dict):
        raise ProjectError("A project must contain a JSON object.")
    if "character" not in document:
        if "format" in document or "version" in document or not {"id", "name", "internal_name"}.intersection(document):
            raise ProjectError("This file does not contain a Pixelheart character project.")
        # Early standalone character dictionaries had no document envelope.
        artwork = document.pop("artwork", {"portrait": None, "sprite": None})
        document = {"character": document, "artwork": artwork}
    if document.get("format", PROJECT_FORMAT) != PROJECT_FORMAT:
        raise ProjectError("This file is not a Pixelheart project.")
    version = document.get("version", 0)
    if type(version) is not int or version not in (0, PROJECT_VERSION):
        raise ProjectError(f"Unsupported Pixelheart project version: {version!r}.")
    validated = _validate_character(document["character"])
    defaults = new_project()["character"]
    # Migrate legacy pronoun-only projects once. Future edits to authoring
    # pronouns must not silently change the explicit game gender.
    if "gender" not in document["character"]:
        defaults["gender"] = infer_legacy_gender(document["character"].get("pronouns"))
    # Missing legacy collections should stay empty, not gain new authored content.
    defaults.update({key: [] for key in NESTED_FIELDS - {"gifts"}})
    if "internal_name" not in document["character"]:
        name = document["character"].get("name", "NewCharacter")
        internal = re.sub(r"[^A-Za-z0-9_]", "", name)
        defaults["internal_name"] = (internal if internal and internal[0].isalpha() else "NPC" + internal)[:64]
    character = {**defaults, **document["character"]}
    if "life" in validated:
        character["life"] = validated["life"]
    for key in _NESTED_KEYS:
        if key in {"events", "relationships"}:
            # These normalizers already retain every metadata field, including
            # unknown fields inside actors and scene beats.
            character[key] = validated.get(key, [])
        else:
            character[key] = [{**normalized, **entry}
                              for entry, normalized in zip(character[key], validated.get(key, []))]
    artwork = document.get("artwork", {})
    if not isinstance(artwork, dict):
        raise ProjectError("The project's artwork must be a JSON object.")
    for kind in ARTWORK_KINDS:
        artwork.setdefault(kind, None)
        _validate_artwork(artwork[kind])
    _validate_variants(artwork)
    document.update(format=PROJECT_FORMAT, version=PROJECT_VERSION, character=character, artwork=artwork)
    if "world" in document:
        try:
            world = normalize_world(document["world"])
            for companion in world["characters"]:
                authored = companion["character"]
                age = authored.get("age", "adult")
                if age not in ("adult", "teen", "child") or (age != "adult" and authored.get("romanceable", False)):
                    raise WorldError("Supporting cast may be Adult, Teen, or Child; romance is adult only.")
                prepared = {**authored, "age": "adult", "id": authored.get("id", companion["id"])}
                companion["character"] = _document({"character": prepared, "artwork": {}})["character"]
                companion["character"]["age"] = age
            document["world"] = world
        except WorldError as exc:
            raise ProjectError(str(exc)) from exc
    if "item_catalog" in document:
        try:
            document["item_catalog"] = validate_catalog(document["item_catalog"])
        except CatalogValidationError as exc:
            raise ProjectError(f"Invalid saved item catalog: {exc}") from exc
    return document


def _check_asset_locations(document: dict, file: Path):
    for appearance in _artwork_sets(document["artwork"]):
        for kind in ARTWORK_KINDS:
            record = appearance.get(kind)
            references = [record] if isinstance(record, str) else [record.get(key) for key in ("original", "prepared")] if record else []
            for reference in references:
                if reference is not None:
                    _asset_path(reference, file.parent)
    if "world" in document:
        try:
            for reference in world_asset_references(document["world"]):
                world_asset_path(reference, file.parent)
        except WorldError as exc:
            raise ProjectError(str(exc)) from exc


def load_project(path: str | os.PathLike) -> dict:
    """Load a current or legacy JSON project, retaining identity and metadata."""
    file = project_path(path)
    try:
        with file.open("rb") as stream:
            data = stream.read(MAX_PROJECT_BYTES + 1)
        if len(data) > MAX_PROJECT_BYTES:
            raise ProjectError("Project JSON exceeds the 64 MiB size limit.")
        document = _document(json.loads(data.decode("utf-8-sig")))
        _check_asset_locations(document, file)
        return document
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProjectError(f"Could not open project '{file.name}': {exc}") from exc


def save_project(document: dict, path: str | os.PathLike) -> Path:
    """Atomically save JSON. Existing files survive validation and write failures."""
    file = project_path(path)
    normalized = _document(document)
    _check_asset_locations(normalized, file)
    temporary = None
    try:
        payload = (json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > MAX_PROJECT_BYTES:
            raise ProjectError("Project JSON exceeds the 64 MiB size limit.")
        if file.is_symlink():
            raise ProjectError("Refusing to overwrite a project file through a symlink.")
        file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=file.parent, prefix=".pixelheart-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
        return file
    except (OSError, UnicodeError) as exc:
        raise ProjectError(f"Could not save project '{file.name}': {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_artwork(document: dict, project_file: str | os.PathLike, kind: str, *, variant: str | None = None) -> Path | None:
    """Resolve a set's selected image; an unset variant has no default fallback.

    Missing files remain editable references. Omit ``variant`` for default art.
    """
    if kind not in ARTWORK_KINDS:
        raise ProjectError("Artwork kind must be 'portrait' or 'sprite'.")
    if not isinstance(document, dict) or not isinstance(document.get("artwork", {}), dict):
        raise ProjectError("The project's artwork must be a JSON object.")
    artwork = document.get("artwork", {})
    if variant is not None:
        if not isinstance(variant, str) or variant not in APPEARANCE_VARIANTS:
            raise ProjectError(f"Unknown artwork appearance variant: {variant!r}.")
        artwork = _validate_variants(artwork).get(variant, {})
    record = artwork.get(kind)
    _validate_artwork(record)
    if record is None:
        return None
    reference = record if isinstance(record, str) else record[record.get("selected", "original")]
    return _asset_path(reference, project_path(project_file).parent)


def _copy_artwork(source, project_file, kind, category):
    if kind not in ARTWORK_KINDS:
        raise ProjectError("Artwork kind must be 'portrait' or 'sprite'.")
    root = project_path(project_file).parent
    source = Path(source).expanduser()
    temporary = None
    try:
        directory = _asset_path(f"artwork/{category}", root)
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open("rb") as incoming, tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=".import-", suffix=".tmp", delete=False) as outgoing:
            temporary = Path(outgoing.name)
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        extension = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".bin"
        reference = f"artwork/{category}/{kind}-{digest.hexdigest()}{extension}"
        destination = _asset_path(reference, root)
        if destination.is_symlink():
            raise ProjectError("Refusing to import artwork through a symlink.")
        if destination.exists():
            with destination.open("rb") as existing:
                if hashlib.file_digest(existing, "sha256").hexdigest() != digest.hexdigest():
                    raise ProjectError("An existing artwork file has conflicting contents; it was not overwritten.")
        else:
            os.replace(temporary, destination)
        return reference
    except (OSError, ValueError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError(f"Could not import artwork '{source.name}': {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_artwork(source: str | os.PathLike, project_file: str | os.PathLike, kind: str) -> str:
    """Copy original bytes into a content-addressed file and return its relative path."""
    return _copy_artwork(source, project_file, kind, "originals")


def copy_project(document: dict, source_project_file: str | os.PathLike, destination_project_file: str | os.PathLike) -> Path:
    """Save As with all originals and prepared images, leaving the source untouched.

    Load the returned path to obtain the copied document's new artwork references.
    The destination JSON is replaced only after all required images are copied.
    """
    copied = _document(document)
    source_file = project_path(source_project_file)
    destination_file = project_path(destination_project_file)
    _check_asset_locations(copied, source_file)
    if destination_file.is_symlink():
        raise ProjectError("Refusing to overwrite a project file through a symlink.")
    if source_file.resolve() == destination_file.resolve():
        if source_file != destination_file:
            raise ProjectError("Refusing to overwrite the source project through a symlink or alternate path.")
        return save_project(copied, destination_file)
    for appearance in _artwork_sets(copied["artwork"]):
        for kind in ARTWORK_KINDS:
            record = appearance.get(kind)
            if isinstance(record, str):
                appearance[kind] = import_artwork(_asset_path(record, source_file.parent), destination_file, kind)
            elif record:
                for key, category in (("original", "originals"), ("prepared", "prepared")):
                    if record.get(key) is not None:
                        record[key] = _copy_artwork(_asset_path(record[key], source_file.parent), destination_file, kind, category)
    if "world" in copied:
        try:
            copy_world_assets(copied["world"], source_file.parent, destination_file.parent)
        except WorldError as exc:
            raise ProjectError(str(exc)) from exc
    return save_project(copied, destination_file)
