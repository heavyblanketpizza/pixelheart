"""Bounded imports of Content Patcher's resolved ``Data/Objects`` asset.

Only factual identifiers, labels, and sprite coordinates are retained; texture
pixels and descriptions stay out of the catalog. An asset export is a snapshot,
not a running game's item registry.
The filtering below uses object data, so runtime patches and context tags from
other assets can still change whether an item can actually be gifted.

Data format: https://stardewvalleywiki.com/Modding:Objects
Categories: https://stardewvalleywiki.com/Modding:Items#Categories
Gift exclusions: https://stardewvalleywiki.com/Modding:Context_tags
"""

from __future__ import annotations

import json
import math
import re
import stat
from datetime import datetime, timezone
from pathlib import Path


CATALOG_FORMAT = "pixelheart-item-catalog"
CATALOG_VERSION = 1
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_CATALOG_ITEMS = 25_000
ITEM_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z")
CATEGORY_NAMES = {
    0: "Other", -2: "Mineral", -4: "Fish", -5: "Animal Product",
    -6: "Animal Product", -7: "Cooking", -8: "Crafting",
    -9: "Big Craftable", -12: "Mineral", -14: "Animal Product",
    -15: "Resource", -16: "Resource", -17: "Other",
    -18: "Animal Product", -19: "Fertilizer", -20: "Trash",
    -21: "Bait", -22: "Fishing Tackle", -23: "Other", -24: "Decor",
    -25: "Cooking", -26: "Artisan Goods", -27: "Artisan Goods",
    -28: "Monster Loot", -29: "Equipment", -74: "Seed",
    -75: "Vegetable", -79: "Fruit", -80: "Flower", -81: "Forage",
    -95: "Hat", -96: "Ring", -97: "Boots", -98: "Weapon",
    -99: "Tool", -100: "Clothing", -101: "Trinket",
    -102: "Book", -103: "Skill Book", -999: "Litter",
}
SNAPSHOT_WARNING = (
    "This is an item-data snapshot from the loaded game. Refresh it after "
    "changing mods or game conditions. Code mods, tags from other assets, and "
    "special item behavior can change giftability; verify selected gifts in-game."
)
# These objects are handled as courtship/movie actions, not ordinary gifts.
# Ring objects construct equipment subclasses; litter entries represent world
# debris. This is an ordinary-gift catalog filter, not CanBeGivenAsGift parity.
SPECIAL_USE_IDS = {"277", "458", "460", "809"}


class CatalogValidationError(ValueError):
    """The entire catalog import failed; no partial catalog should be used."""


def _text(value, field, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit:
        raise CatalogValidationError(f"{field} must be text of at most {limit} characters.")
    if not empty and not value.strip():
        raise CatalogValidationError(f"{field} cannot be empty.")
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise CatalogValidationError(f"{field} contains invalid text characters.")
    return value


def _item_id(value):
    if not isinstance(value, str) or not ITEM_ID.fullmatch(value):
        raise CatalogValidationError(
            "Item IDs must be unqualified object IDs of at most 100 ASCII "
            "letters, numbers, underscores, dots, or hyphens, starting with "
            "a letter, number, or underscore."
        )
    return value


def _category(value):
    if type(value) is not int or not -(2 ** 31) <= value < 2 ** 31:
        raise CatalogValidationError("An item's Category must be a 32-bit whole number.")
    return value


def validate_icon_metadata(value):
    """Validate an asset key and 16-pixel tile index, never a filesystem path."""
    if not isinstance(value, dict) or set(value) != {"texture", "index"}:
        raise CatalogValidationError("An item icon needs a texture asset name and sprite index.")
    texture = _text(value["texture"], "Icon texture", 240).replace("\\", "/")
    if (any(part in ("", ".", "..") for part in texture.split("/"))
            or any(char in '<>:"|?*' for char in texture)):
        raise CatalogValidationError("Icon textures must be relative game asset names, without traversal or special filename characters.")
    index = value["index"]
    if type(index) is not int or not 0 <= index <= 1_000_000:
        raise CatalogValidationError("An icon sprite index must be a whole number from 0 to 1,000,000.")
    return {"texture": texture, "index": index}


def _reject_constant(value):
    raise CatalogValidationError(f"The JSON contains the invalid number {value}.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogValidationError(f"The JSON repeats the key {key!r}.")
        result[key] = value
    return result


def _validate_json_tree(value):
    """Check ignored fields too, without retaining them in the snapshot."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 1_000_000 or depth > 64:
            raise CatalogValidationError("The JSON has too many values or is nested too deeply.")
        if isinstance(item, str):
            if "\x00" in item or any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise CatalogValidationError("The JSON contains null characters or invalid Unicode.")
        elif isinstance(item, dict):
            pending.extend((part, depth + 1) for pair in item.items() for part in pair)
        elif isinstance(item, list):
            pending.extend((part, depth + 1) for part in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise CatalogValidationError("The JSON contains a non-finite number.")


def validate_catalog(catalog):
    """Return a fresh, validated project catalog without mutating the input.

    Bundled vanilla catalogs may omit ``imported_at``. Local imports require a
    timezone-aware ISO timestamp. Unknown fields are rejected so project files
    cannot silently acquire unbounded extra data through this envelope.
    """
    required = {"format", "version", "label", "source", "items"}
    optional = {"warnings", "imported_at", "game_version"}
    if not isinstance(catalog, dict) or not required <= set(catalog) or set(catalog) - required - optional:
        raise CatalogValidationError("Expected a Pixelheart item catalog with the supported fields.")
    if catalog["format"] != CATALOG_FORMAT:
        raise CatalogValidationError("This is not a Pixelheart item catalog.")
    if type(catalog["version"]) is not int or catalog["version"] != CATALOG_VERSION:
        raise CatalogValidationError("Unsupported item catalog version.")
    if catalog["source"] not in ("vanilla", "content-patcher-export"):
        raise CatalogValidationError("Unsupported item catalog source.")
    result = {
        "format": CATALOG_FORMAT,
        "version": CATALOG_VERSION,
        "label": _text(catalog["label"], "Catalog label", 160),
        "source": catalog["source"],
    }
    if "game_version" in catalog:
        result["game_version"] = _text(catalog["game_version"], "Game version", 32)
    timestamp = catalog.get("imported_at")
    if timestamp is not None or catalog["source"] == "content-patcher-export":
        timestamp = _text(timestamp, "Import timestamp", 40)
        try:
            if datetime.fromisoformat(timestamp).utcoffset() is None:
                raise ValueError
        except ValueError as exc:
            raise CatalogValidationError("The import timestamp must be an ISO date and time with a timezone.") from exc
        result["imported_at"] = timestamp
    elif "imported_at" in catalog:
        raise CatalogValidationError("Omit an unavailable import timestamp instead of setting it to null.")
    warnings = catalog.get("warnings", [])
    if not isinstance(warnings, list) or len(warnings) > 20:
        raise CatalogValidationError("Use a list with up to 20 catalog warnings.")
    result["warnings"] = [_text(warning, "Catalog warning", 1000) for warning in warnings]
    items = catalog["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CATALOG_ITEMS:
        raise CatalogValidationError(f"A catalog must contain between 1 and {MAX_CATALOG_ITEMS:,} items.")
    cleaned = []
    seen = set()
    for item in items:
        fields = {"id", "name", "category", "category_name"}
        if not isinstance(item, dict) or not fields <= set(item) or set(item) - fields - {"icon"}:
            raise CatalogValidationError("Each catalog item needs an ID, name, category, and category name.")
        item_id = _item_id(item["id"])
        if item_id in seen:
            raise CatalogValidationError(f"The catalog repeats item ID {item_id!r}.")
        seen.add(item_id)
        record = {
            "id": item_id,
            "name": _text(item["name"], "Item name", 256),
            "category": _category(item["category"]),
            "category_name": _text(item["category_name"], "Category name", 80),
        }
        if "icon" in item:
            record["icon"] = validate_icon_metadata(item["icon"])
        cleaned.append(record)
    result["items"] = cleaned
    return result


def load_catalog(path):
    """Import a local ``patch export Data/Objects`` JSON file atomically.

    A malformed entry fails the complete import, including malformed entries
    which would otherwise be filtered out. No game processes are started and
    no files are written. Optional null context-tag lists mean no custom tags,
    matching the game's serialized ObjectData model.
    """
    try:
        source_path = Path(path)
        info = source_path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise CatalogValidationError("Select the exported Data_Objects.json file.")
        if info.st_size > MAX_CATALOG_BYTES:
            raise CatalogValidationError("The item export must be 32 MiB or smaller.")
        with source_path.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise CatalogValidationError("The item export must be 32 MiB or smaller.")
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, CatalogValidationError):
            raise
        raise CatalogValidationError(f"Could not read the item export: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CatalogValidationError):
            raise
        raise CatalogValidationError("The item export is not valid UTF-8 JSON. Export Data/Objects again.") from exc
    if not isinstance(data, dict) or not data or len(data) > MAX_CATALOG_ITEMS:
        raise CatalogValidationError(f"Expected Data/Objects with 1 to {MAX_CATALOG_ITEMS:,} object entries.")
    _validate_json_tree(data)
    items = []
    skipped = 0
    fallback_names = 0
    for item_id, entry in data.items():
        item_id = _item_id(item_id)
        if not isinstance(entry, dict):
            raise CatalogValidationError(f"Object {item_id!r} must have an object-data record.")
        name = _text(entry.get("Name"), f"Name for {item_id}", 256)
        category = _category(entry.get("Category"))
        display_name = entry.get("DisplayName", name)
        missing_display_name = display_name is None
        if missing_display_name:
            display_name = name
        _text(display_name, f"DisplayName for {item_id}", 1000)
        if "Type" in entry:
            _text(entry["Type"], f"Type for {item_id}", 80)
        icon = None
        if "SpriteIndex" in entry:
            texture = entry.get("Texture")
            icon = validate_icon_metadata({
                "texture": "Maps/springobjects" if texture is None else texture,
                "index": entry["SpriteIndex"],
            })
        giftable = entry.get("CanBeGivenAsGift", True)
        if type(giftable) is not bool:
            raise CatalogValidationError(f"CanBeGivenAsGift for {item_id} must be true or false.")
        tags = entry.get("ContextTags")
        if tags is None:
            tags = []
        if not isinstance(tags, list) or len(tags) > 1000:
            raise CatalogValidationError(f"ContextTags for {item_id} must be a list of tags or null.")
        for tag in tags:
            _text(tag, f"ContextTags for {item_id}", 256)
        if (not giftable or "not_giftable" in tags or entry.get("Type") == "Ring"
                or "item_type_ring" in tags or category == -999 or item_id in SPECIAL_USE_IDS):
            skipped += 1
            continue
        if missing_display_name or "[" in display_name or "{{" in display_name:
            fallback_names += 1
            display_name = name
        if len(display_name) > 256:
            raise CatalogValidationError(f"The display name for {item_id} exceeds 256 characters.")
        record = {
            "id": item_id, "name": display_name,
            "category": category,
            "category_name": CATEGORY_NAMES.get(category, f"Category {category}"),
        }
        if icon is not None:
            record["icon"] = icon
        items.append(record)
    if not items:
        raise CatalogValidationError("No gift candidates were found in this Data/Objects export.")
    warnings = [SNAPSHOT_WARNING]
    if skipped:
        warnings.append(f"Excluded {skipped:,} non-giftable, equipment, world-only, or special-use objects.")
    if fallback_names:
        warnings.append(f"Using internal names for {fallback_names:,} items whose display names are unavailable or need the game's translation system.")
    return validate_catalog({
        "format": CATALOG_FORMAT, "version": CATALOG_VERSION,
        "source": "content-patcher-export", "label": source_path.name[:160],
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": sorted(items, key=lambda item: (item["name"].casefold(), item["id"])),
        "warnings": warnings,
    })
