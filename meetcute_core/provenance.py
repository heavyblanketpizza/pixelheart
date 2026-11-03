"""Portable source notices for imported references, without local file paths."""

from __future__ import annotations

from collections.abc import Mapping
import re
from urllib.parse import urlsplit


_TEXT_FIELDS = {"provider": 80, "source_name": 240, "attribution": 500,
                "creator": 160, "template": 80}
_FIELDS = set(_TEXT_FIELDS) | {"asset", "sha256", "source_url", "url", "modified_game_possible"}


def validate_source(value) -> dict:
    """Return detached, bounded attribution metadata; never accept source paths."""
    if not isinstance(value, Mapping) or not value or set(value) - _FIELDS:
        raise ValueError("Source metadata must contain only portable attribution fields, not local paths or filenames.")
    result = {}
    for field, item in value.items():
        if field == "modified_game_possible":
            if type(item) is not bool:
                raise ValueError("Source modified-game status must be true or false.")
        elif field == "sha256":
            if not isinstance(item, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", item):
                raise ValueError("Source SHA-256 must contain 64 hexadecimal characters.")
            item = item.lower()
        elif field == "asset":
            if (not isinstance(item, str) or len(item) > 240
                    or not re.fullmatch(r"(?:Characters|Portraits|Data|Maps|TileSheets)/[A-Za-z0-9_./-]+", item)
                    or any(part in ("", ".", "..") for part in item.split("/"))
                    or item.lower().endswith((".json", ".png", ".xnb"))):
                raise ValueError("Source asset must be a logical game asset key without a local path or file extension.")
        elif field in {"source_url", "url"}:
            if not isinstance(item, str) or len(item) > 1000 or any(ord(c) < 33 for c in item):
                raise ValueError("Source URL must be a public HTTPS reference.")
            try:
                url = urlsplit(item)
                valid = (url.scheme == "https" and url.hostname and "." in url.hostname
                         and not url.username and not url.password and not url.query
                         and url.port in (None, 443) and "\\" not in item)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Source URL must be a public HTTPS reference without credentials or query parameters.")
        else:
            if (not isinstance(item, str) or not item.strip() or len(item) > _TEXT_FIELDS[field]
                    or any(ord(c) < 32 for c in item) or "/" in item or "\\" in item
                    or re.search(r"(?:^|\s)[A-Za-z]:", item)):
                raise ValueError("Source labels must be plain attribution text without file paths.")
        result[field] = item
    return result


def source_metadata(record) -> dict:
    """Validate current and historical notices on a dialogue/artwork record."""
    result = {}
    if "source" in record:
        result["source"] = validate_source(record["source"])
    if "source_history" in record:
        history = record["source_history"]
        if not isinstance(history, list) or len(history) > 100:
            raise ValueError("Source history must be a list of at most 100 portable source records.")
        result["source_history"] = [validate_source(source) for source in history]
    return result
