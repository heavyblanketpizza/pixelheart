"""Read complete character references exported from the user's own game.

Content Patcher writes flattened asset names under ``patch export``. A nested
unpacked-content layout is also accepted. Imports are read-only and offline;
source records contain logical asset names and hashes, never local paths.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .artwork import ArtworkValidationError, MAX_ARTWORK_BYTES, inspect_artwork
from .dialogue_templates import MAX_DIALOGUES
from .wiki_dialogue import _annotated_entries


CONTENT_PATCHER_EXPORT_URL = (
    "https://github.com/Pathoschild/StardewMods/blob/develop/ContentPatcher/"
    "docs/author-guide/troubleshooting.md#export"
)
LOCAL_TEMPLATES = {
    "abigail": {"name": "Abigail"},
    "elliott": {"name": "Elliott"},
}
MAX_DIALOGUE_BYTES = 8 * 1024 * 1024
MAX_DIALOGUE_ENTRIES = MAX_DIALOGUES
MAX_TEXT_LENGTH = 8000
_TRIGGER = re.compile(r"[A-Za-z0-9_.*()+:-]{1,120}\Z")
_SOURCE = {
    "provider": "local-content-patcher",
    "source_name": "From my game (Content Patcher)",
    "source_url": CONTENT_PATCHER_EXPORT_URL,
    "attribution": (
        "Stardew Valley content © ConcernedApe; modded content belongs to its "
        "respective creators. Importing does not grant redistribution rights."
    ),
    "modified_game_possible": True,
}


class LocalTemplateError(ValueError):
    """A local reference could not be loaded safely and completely."""


def _template(template_id):
    if not isinstance(template_id, str) or template_id not in LOCAL_TEMPLATES:
        raise LocalTemplateError("Choose an available character reference.")
    return LOCAL_TEMPLATES[template_id]


def export_commands(template_id, kind="all") -> str:
    """Return commands for the SMAPI console; never execute a command."""
    name = _template(template_id)["name"]
    if kind not in ("dialogue", "artwork", "all"):
        raise LocalTemplateError("Choose dialogue, artwork, or all references.")
    commands = []
    if kind in ("dialogue", "all"):
        commands.append(f'patch export "Characters/Dialogue/{name}"')
    if kind in ("artwork", "all"):
        commands.extend((f'patch export "Portraits/{name}" image', f'patch export "Characters/{name}" image'))
    return "\n".join(commands)


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise LocalTemplateError("Reference loading was cancelled.")


def _safe_path(root, relative, *, directory=False):
    """Check each existing component without following symlinked children."""
    path = root
    try:
        parts = Path(relative).parts
        for index, part in enumerate(parts):
            path /= part
            try:
                details = path.lstat()
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(details.st_mode):
                raise LocalTemplateError("Reference folders must not contain linked files or folders.")
            expect_directory = directory or index < len(parts) - 1
            if expect_directory and not stat.S_ISDIR(details.st_mode):
                raise LocalTemplateError("Choose a regular reference folder.")
            if not expect_directory and not stat.S_ISREG(details.st_mode):
                raise LocalTemplateError("Reference assets must be regular files.")
        # Keep containment explicit if path handling is extended in the future.
        path.resolve(strict=True).relative_to(root)
        return path
    except LocalTemplateError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise LocalTemplateError("The reference folder cannot be read. Choose a readable local folder.") from exc


def resolve_export_folder(path) -> Path:
    """Find exports in a selected folder, game root, or macOS app bundle.

    A directly selected export/unpacked folder can have any name. Only known
    child locations are inspected; this never scans the disk or unpacks XNBs.
    """
    try:
        if not path or "\x00" in str(path):
            raise ValueError("empty path")
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("not a directory")
        for relative in (
            "patch export",
            "Contents/MacOS/patch export",
            "Stardew Valley.app/Contents/MacOS/patch export",
            "Content (unpacked)",
        ):
            candidate = _safe_path(root, relative, directory=True)
            if candidate is not None:
                return candidate
        return root
    except LocalTemplateError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise LocalTemplateError(
            "Choose the Content Patcher 'patch export' folder or its game folder."
        ) from exc


def _asset_path(root, asset, suffix):
    for relative in (asset.replace("/", "_") + suffix, asset + suffix):
        path = _safe_path(root, relative)
        if path is not None:
            return path
    type_hint = " image" if suffix == ".png" else ""
    raise LocalTemplateError(
        f"Missing {asset.replace('/', '_')}{suffix}. Run patch export \"{asset}\"{type_hint} "
        "in the SMAPI console, then choose the resulting 'patch export' folder."
    )


def _read_bounded(root, path, maximum, cancelled):
    """Bound the actual read as well as the reported file size."""
    _check_cancelled(cancelled)
    try:
        if _safe_path(root, path.relative_to(root)) is None:
            raise LocalTemplateError("A reference file is missing. Export it again and retry.")
        if path.stat().st_size > maximum:
            raise LocalTemplateError("The reference file exceeds the size limit.")
        payload = bytearray()
        with path.open("rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise LocalTemplateError("Reference assets must be regular files.")
            while True:
                _check_cancelled(cancelled)
                chunk = source.read(min(64 * 1024, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > maximum:
                    raise LocalTemplateError("The reference file exceeds the size limit.")
        _check_cancelled(cancelled)
        return bytes(payload)
    except LocalTemplateError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise LocalTemplateError("The reference file cannot be read. Export it again and retry.") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate dialogue key")
        result[key] = value
    return result


def _source(asset, payload):
    return {**_SOURCE, "asset": asset, "sha256": hashlib.sha256(payload).hexdigest()}


def load_local_dialogue(template_id, export_root, *, cancelled=None) -> dict:
    """Load every exported dialogue entry, keeping its text and source order."""
    _check_cancelled(cancelled)
    name = _template(template_id)["name"]
    root = resolve_export_folder(export_root)
    asset = f"Characters/Dialogue/{name}"
    path = _asset_path(root, asset, ".json")
    payload = _read_bounded(root, path, MAX_DIALOGUE_BYTES, cancelled)
    try:
        data = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise LocalTemplateError("Export dialogue as a JSON object with unique trigger names and text values.") from exc
    if not isinstance(data, dict) or not data or len(data) > MAX_DIALOGUE_ENTRIES:
        raise LocalTemplateError(f"Dialogue must contain 1 to {MAX_DIALOGUE_ENTRIES} entries; nothing was imported.")
    for trigger, text in data.items():
        _check_cancelled(cancelled)
        if (not isinstance(trigger, str) or not _TRIGGER.fullmatch(trigger)
                or not isinstance(text, str) or not text.strip()
                or len(text) > MAX_TEXT_LENGTH or "{{" in text
                or any(ord(char) < 32 and char not in "\n\r\t" for char in trigger + text)
                or any(0xD800 <= ord(char) <= 0xDFFF for char in trigger + text)):
            raise LocalTemplateError(
                "A dialogue entry is unsupported: use valid triggers and nonempty text up to "
                "8000 characters, without Content Patcher tokens. Nothing was imported."
            )
    source = _source(asset, payload)
    # Use general key explanations. A mod can replace any line, so the archive's
    # command-specific teaching notes need not describe this user's actual text.
    entries = _annotated_entries(data, {"examples": []})
    for entry in entries:
        if entry["title"] == "Archived dialogue entry":
            entry["title"] = "Dialogue entry"
        entry["source"] = dict(source)
    _check_cancelled(cancelled)
    return {"id": template_id, "name": name, **_SOURCE, "source": source, "examples": entries}


def load_local_artwork(template_id, export_root, *, cancelled=None) -> dict:
    """Load a complete portrait/sprite pair without modifying either file."""
    _check_cancelled(cancelled)
    name = _template(template_id)["name"]
    root = resolve_export_folder(export_root)
    result = {"id": template_id, "name": name, **_SOURCE, "source": dict(_SOURCE)}
    for kind, asset in (("portrait", f"Portraits/{name}"), ("sprite", f"Characters/{name}")):
        _check_cancelled(cancelled)
        path = _asset_path(root, asset, ".png")
        payload = _read_bounded(root, path, MAX_ARTWORK_BYTES, cancelled)
        try:
            info = inspect_artwork(path)
        except ArtworkValidationError as exc:
            raise LocalTemplateError("The exported artwork must be a complete, readable static PNG within the artwork size limits.") from exc
        width, height = info["width"], info["height"]
        columns, minimum_width, minimum_rows = (2, 128, 3) if kind == "portrait" else (4, 64, 13)
        frame_height = width // 2
        if (width < minimum_width or width % columns or height % frame_height
                or height < minimum_rows * frame_height):
            raise LocalTemplateError(
                f"Export the complete {kind} sheet: {columns} columns and at least "
                f"{minimum_rows} complete rows, at standard size or a larger proportional size."
            )
        # Detect edits during inspection so recorded provenance matches the file
        # which was validated, rather than mixing two versions of an export.
        if _read_bounded(root, path, MAX_ARTWORK_BYTES, cancelled) != payload:
            raise LocalTemplateError("The exported artwork changed while loading. Finish exporting and retry.")
        result[kind] = path
        result[f"{kind}_source"] = _source(asset, payload)
    _check_cancelled(cancelled)
    return result
