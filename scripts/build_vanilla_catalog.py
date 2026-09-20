#!/usr/bin/env python3
"""Build the offline 1.6.15 object catalog from checksum-pinned metadata.

Download the two input files listed in pixelheart_core/data/README.md first.
This script never downloads files or copies sprites, descriptions, or game code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


OBJECTS_SHA256 = "9b4b1d3f574feb68207a9c55fdce10cbcbe3518f706db2946905f65369c37635"
NAMES_SHA256 = "8b3068d79e60afa5e452b7f2c59e2b57d4e01b7bd9c35dae75d876d8ac0eaf05"
GAME_VERSION = "1.6.15"
SOURCE_OBJECT_COUNT = 807

# These use dedicated NPC interactions before ordinary gift-taste processing.
SPECIAL_INTERACTION_IDS = frozenset({"71", "277", "458", "460", "809"})
# Curated vanilla quest hand-ins; not a blanket Type: Quest exclusion. Mods may
# repurpose these IDs, so this filter does not apply to local game imports.
VANILLA_QUEST_HANDIN_IDS = frozenset({
    "191", "788", "789", "790", "864", "865", "866", "867", "868", "869", "870",
    "875", "876",
})
# CanBeGivenAsGift defaults to true even for internal object definitions that
# never become normal inventory gifts. Keep this reviewed, ID-based list in the
# vanilla builder only: imported mods can repurpose the same IDs. See the
# catalog provenance for the evidence behind each exclusion.
VANILLA_NON_GIFT_REASONS = {
    "30": "Unobtainable Lumber object",
    "73": "Golden Walnut currency, rather than an inventory gift",
    "94": "Unused Spirit Torch object",
    "102": "Lost Book collected directly into the museum library",
    "326": "Dwarvish Translation Guide unlocked as a special power",
    "434": "Stardrop consumed immediately on receipt",
    "449": "Unused Stone Base object",
    "461": "Unused Decorative Pot object",
    "590": "World artifact spot, rather than an inventory gift",
    "742": "Haley's bracelet event prop, rather than an inventory gift",
    "803": "Iridium Milk event prop, rather than an inventory gift",
    "858": "Qi Gem currency, rather than an inventory gift",
    "892": "Unused Warp Totem: Qi's Arena object",
    "922": "World supply crate, rather than an inventory gift",
    "923": "World supply crate, rather than an inventory gift",
    "924": "World supply crate, rather than an inventory gift",
    "925": "Unused Slime Crate object",
    "927": "Unused Camping Stove object",
    "929": "Unused Hedge object",
    "930": "Heart pickup consumed immediately to restore health",
    "GoldCoin": "Gold pickup converted directly into money",
    "PetLicense": "Pet adoption shop action, rather than an inventory gift",
    "SeedSpot": "World seed spot, rather than an inventory gift",
}

# Friendly filter labels. Numbers retain the exact game's category values.
CATEGORY_NAMES = {
    -2: "Gems",
    -4: "Fish",
    -5: "Eggs",
    -6: "Milk",
    -7: "Cooking",
    -8: "Crafting",
    -12: "Minerals",
    -15: "Resources",
    -16: "Resources",
    -17: "Other",
    -18: "Animal products",
    -19: "Fertilizer",
    -20: "Trash",
    -21: "Bait",
    -22: "Fishing tackle",
    -23: "Other",
    -24: "Decor",
    -26: "Artisan goods",
    -27: "Artisan goods",
    -28: "Monster loot",
    -74: "Seeds",
    -75: "Vegetables",
    -79: "Fruit",
    -80: "Flowers",
    -81: "Forage",
    -102: "Books",
    -103: "Skill books",
    0: "Other",
}


def read_verified(path: Path, checksum: str) -> Any:
    """Reject changed input rather than silently changing the shipped baseline."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError(f"{path}: checksum does not match the pinned 1.6.15 source")
    return json.loads(raw)


def exclusion_reason(item: dict[str, Any]) -> str | None:
    """Exclude non-gifts and reviewed objects unavailable for normal gifting."""
    if not item["canBeGivenAsGift"]:
        return "CanBeGivenAsGift is false"
    if "not_giftable" in item["contextTags"]:
        return "not_giftable context tag"
    if item["type"] == "Ring" or "item_type_ring" in item["contextTags"]:
        return "Ring item, rather than an ordinary object"
    if item["category"] == -999:
        return "World litter, rather than an inventory gift"
    if item["id"] in SPECIAL_INTERACTION_IDS:
        return "Dedicated NPC interaction instead of an ordinary gift"
    if item["id"] in VANILLA_QUEST_HANDIN_IDS:
        return "Dedicated vanilla quest hand-in"
    return VANILLA_NON_GIFT_REASONS.get(item["id"])


def build_catalog(objects: dict[str, Any], names: list[dict[str, Any]]) -> dict[str, Any]:
    if objects["_meta"]["gameVersion"] != GAME_VERSION:
        raise ValueError("Unexpected game version")
    rows = objects["objects"]
    names_by_id = {item["id"]: item["names"]["data-en-US"] for item in names}
    object_ids = {item["id"] for item in rows}
    if (
        len(rows) != SOURCE_OBJECT_COUNT
        or len(names) != SOURCE_OBJECT_COUNT
        or len(object_ids) != SOURCE_OBJECT_COUNT
        or object_ids != names_by_id.keys()
    ):
        raise ValueError("The two sources must contain the same 807 distinct object IDs")

    items = []
    for row in rows:
        if exclusion_reason(row) is not None:
            continue
        category = row["category"]
        category_name = "Artifacts" if row["type"] == "Arch" else CATEGORY_NAMES[category]
        items.append(
            {
                "id": row["id"],
                "name": names_by_id[row["id"]],
                "category": category,
                "category_name": category_name,
                "icon": {
                    "texture": (row["spriteSheet"] or "Maps/springobjects").replace("\\", "/"),
                    "index": row["spriteIndex"],
                },
            }
        )

    items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return {
        "format": "pixelheart-item-catalog",
        "version": 1,
        "label": "Vanilla 1.6.15 · object gifts",
        "source": "vanilla",
        "items": items,
        "warnings": [
            "This is an object-data catalog. Trinkets and other item types are not included.",
            "Known internal, unobtainable, automatically consumed, and quest-only objects "
            "are excluded from this vanilla catalog. Instance-specific quest flags and "
            "special NPC interactions can still affect gift acceptance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", required=True, type=Path, help="Pinned stardew-data objects.json")
    parser.add_argument("--names", required=True, type=Path, help="Pinned stardewids objects.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "pixelheart_core/data/vanilla_items.json",
    )
    args = parser.parse_args()
    try:
        objects = read_verified(args.objects, OBJECTS_SHA256)
        names = read_verified(args.names, NAMES_SHA256)
        catalog = build_catalog(objects, names)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog['items'])} object gift candidates to {args.output}")


if __name__ == "__main__":
    main()
