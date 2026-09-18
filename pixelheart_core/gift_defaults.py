"""Offline vanilla gift baselines for the bundled Stardew Valley 1.6.15 catalog.

These are inherited tastes for a new character with no personal preferences,
not assignments to save or export. Runtime mods, item instances, and special
gift interactions can change the result. This does not predict gift acceptance.

The five universal lists are factual IDs from the 1.6.15 data documented at:
https://stardewvalleywiki.com/mediawiki/index.php?title=Modding:Gift_taste_data&oldid=189757

Artifact IDs and the four effective neutral-fallback records were checked
against the same pinned Data/Objects extraction used by vanilla_items.json:
https://raw.githubusercontent.com/juliaramosguedes/stardew-data/4e0d98119afefd766f15ee77a529db4eb71fa240/data/en-US/objects.json
SHA-256: 9b4b1d3f574feb68207a9c55fdce10cbcbe3518f706db2946905f65369c37635

The wiki's pseudocode omits the category-zero and artifact handling. Those
were checked in NPC.getGiftTasteForThisItem, with current wiki item pages
corroborating category-zero examples (Sugar, Sea Jelly, Golden Coconut):
https://github.com/Dannode36/StardewValleyDecompiled/blob/5225ef409e42a6159a82cf81200bf6eb315c9961/Stardew%20Valley/StardewValley/NPC.cs#L1405
That implementation reference predates 1.6.15; the metadata and universal
lists are pinned to 1.6.15. This is a bounded catalog baseline, not a general
resolver for arbitrary game data or a substitute for in-game verification.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path


VANILLA_GIFT_GAME_VERSION = "1.6.15"
SPECIAL_GIFT_IDS = frozenset({"StardropTea"})

# Keep the original universal ID/category references together for auditing.
# The only context tags in these lists are book_item (neutral) and
# category_trinket (dislike). No bundled object has category_trinket. All
# bundled skill/power books stay neutral naturally except Price Catalogue's
# explicit dislike. Lost Book is category zero and is not an ordinary book
# gift: universal neutral context tags do not override categories here.
_UNIVERSAL = {
    "love": frozenset("74 446 797 373 279".split()),
    "like": frozenset(
        "-2 -7 -26 -75 -80 72 395 613 634 635 636 637 638 724 459 873 394 166".split()
    ),
    "neutral": frozenset("194 216 262 304 815 book_item".split()),
    "dislike": frozenset(
        "-4 -8 -12 -15 -16 -19 -22 -24 -25 -28 -74 "
        "78 169 246 247 305 309 310 311 403 419 423 535 536 537 725 726 "
        "749 271 Book_PriceCatalogue category_trinket".split()
    ),
    "hate": frozenset(
        "0 -20 -21 92 110 111 112 142 152 153 157 178 105 168 170 171 172 "
        "374 376 378 380 397 420 684 721 766 767 772 203 308 265 909 910".split()
    ),
}
_PRECEDENCE = ("love", "hate", "like", "dislike")

# Data/Objects Type == Arch; the catalog intentionally omits the raw Type.
# Artifacts default to dislike, including explicit universal-hate IDs such
# as Chewing Stick and Rusty Spoon. Penny/Dwarf exceptions are personal NPC
# behavior and do not apply to a newly authored character.
_ARTIFACT_IDS = frozenset(
    "96 97 98 99 100 101 103 104 105 106 107 108 109 110 111 112 113 114 "
    "115 116 117 118 119 120 121 122 123 124 125 126 127 "
    "579 580 581 582 583 584 585 586 587 588 589".split()
)

# Only four otherwise-neutral catalog objects trigger a price/edibility
# fallback. Keep their factual (base sell price, edibility) values, rather
# than duplicating all 807 source object definitions or creative text.
_NEUTRAL_FALLBACK_FACTS = {
    "283": (80, -15),  # Holly
    "296": (5, 10),    # Salmonberry
    "399": (8, 5),     # Spring Onion
    "889": (1, 1),     # Qi Fruit
}


def _catalog_taste(item_id: str, category: int) -> str:
    taste = "neutral"
    # The game's category lookup also compares "0". Omitting that check
    # would incorrectly make Sugar, jellies, totems, etc. neutral.
    for candidate in _PRECEDENCE:
        if str(category) in _UNIVERSAL[candidate]:
            taste = candidate
            break

    explicit_neutral = False
    for candidate in (*_PRECEDENCE, "neutral"):
        if item_id in _UNIVERSAL[candidate]:
            taste = candidate
            explicit_neutral = candidate == "neutral"
            break

    if item_id in _ARTIFACT_IDS:
        taste = "dislike"

    if taste == "neutral" and not explicit_neutral:
        facts = _NEUTRAL_FALLBACK_FACTS.get(item_id)
        if facts is not None:
            price, edibility = facts
            if edibility != -300 and edibility < 0:
                taste = "hate"
            elif price < 20:
                taste = "dislike"
    return taste


@cache
def _bundled_tastes() -> dict[str, str]:
    path = Path(__file__).with_name("data") / "vanilla_items.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    return {
        item["id"]: _catalog_taste(item["id"], item["category"])
        for item in catalog["items"]
        if item["id"] not in SPECIAL_GIFT_IDS
    }


def vanilla_gift_tastes() -> dict[str, str]:
    """Return a fresh ID → ordinary taste mapping for bundled vanilla items.

    Values are love, like, dislike, hate, or neutral. StardropTea is omitted:
    its hardcoded special reward bypasses the five ordinary taste rules and
    personal gift preferences. Unknown/modded IDs have no inferred baseline.
    """
    return _bundled_tastes().copy()
