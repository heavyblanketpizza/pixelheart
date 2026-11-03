"""Item lookups: the project's own catalog snapshot when it has one, else the bundled vanilla items."""
from __future__ import annotations

from functools import cache
import json
from pathlib import Path

from pixelheart_core.catalog import CatalogValidationError, validate_catalog


_VANILLA_FILE = Path(__file__).resolve().parent.parent / "pixelheart_core" / "data" / "vanilla_items.json"


@cache
def _vanilla_items() -> tuple[dict, ...]:
    catalog = validate_catalog(json.loads(_VANILLA_FILE.read_text(encoding="utf-8")))
    return tuple(catalog["items"])


def catalog_items(document) -> list[dict]:
    """Fresh copies of the items the character's gifts may use."""
    snapshot = document.get("item_catalog") if isinstance(document, dict) else None
    if snapshot is not None:
        try:
            return [dict(item) for item in validate_catalog(snapshot)["items"]]
        except (CatalogValidationError, KeyError, TypeError):
            pass
    return [dict(item) for item in _vanilla_items()]


def item_names(document) -> dict[str, str]:
    return {item["id"]: item["name"] for item in catalog_items(document)}
