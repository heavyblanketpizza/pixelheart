"""Original, editable starting points for a new character's gift preferences.

These profiles are Pixelheart authoring examples, not vanilla NPC tastes or
universal gift rules. They refer only to ordinary items in the bundled catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


TASTES = ("love", "like", "dislike", "hate")
RECOMMENDED_GIFT_PRESET_ID = "everyday"


@dataclass(frozen=True)
class GiftPreset:
    """A read-only profile whose assignments can be copied into a project."""

    id: str
    name: str
    description: str
    gifts: Mapping[str, tuple[str, ...]]

    def __post_init__(self):
        if set(self.gifts) != set(TASTES):
            raise ValueError("A gift preset needs love, like, dislike, and hate preferences.")
        assignments = {taste: tuple(self.gifts[taste]) for taste in TASTES}
        all_ids = [item_id for values in assignments.values() for item_id in values]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Each gift can appear only once in a preset.")
        object.__setattr__(self, "gifts", MappingProxyType(assignments))

    def as_gifts(self) -> dict[str, list[str]]:
        """Return fresh, editable lists without changing the shared preset."""
        return {taste: list(self.gifts[taste]) for taste in TASTES}


GIFT_PRESETS = (
    GiftPreset(
        id="everyday",
        name="Everyday favorites",
        description="Coffee, cake, and thoughtful little gifts. An easy place to start.",
        gifts={
            "love": ("395", "221", "72"),  # Coffee, Pink Cake, Diamond
            "like": ("591", "18", "613", "340"),  # Tulip, Daffodil, Apple, Honey
            "dislike": ("330", "167"),  # Clay, Joja Cola
            "hate": ("168", "170"),  # Trash, Broken Glasses
        },
    ),
    GiftPreset(
        id="botanist",
        name="Botanist",
        description="Flowers, garden greens, and a quiet cup of tea.",
        gifts={
            "love": ("595", "421", "614"),  # Fairy Rose, Sunflower, Green Tea
            "like": ("376", "402", "18", "196"),  # Poppy, Sweet Pea, Daffodil, Salad
            "dislike": ("390", "335"),  # Stone, Iron Bar
            "hate": ("168", "420"),  # Trash, Red Mushroom
        },
    ),
    GiftPreset(
        id="baker",
        name="Baker",
        description="Sweet treats and fresh ingredients for the next batch.",
        gifts={
            "love": ("221", "220", "340"),  # Pink Cake, Chocolate Cake, Honey
            "like": ("176", "184", "246", "245"),  # Egg, Milk, Wheat Flour, Sugar
            "dislike": ("227", "152"),  # Sashimi, Seaweed
            "hate": ("92", "168"),  # Sap, Trash
        },
    ),
    GiftPreset(
        id="angler",
        name="Angler",
        description="Fresh catches and a good meal after a day by the water.",
        gifts={
            "love": ("715", "143", "138"),  # Lobster, Catfish, Rainbow Trout
            "like": ("139", "130", "131", "227"),  # Salmon, Tuna, Sardine, Sashimi
            "dislike": ("246", "420"),  # Wheat Flour, Red Mushroom
            "hate": ("168", "170"),  # Trash, Broken Glasses
        },
    ),
    GiftPreset(
        id="miner",
        name="Miner",
        description="Sparkling gems and useful finds from below the valley.",
        gifts={
            "love": ("72", "64", "60"),  # Diamond, Ruby, Emerald
            "like": ("66", "70", "68", "334"),  # Amethyst, Jade, Topaz, Copper Bar
            "dislike": ("18", "376"),  # Daffodil, Poppy
            "hate": ("167", "168"),  # Joja Cola, Trash
        },
    ),
    GiftPreset(
        id="artist",
        name="Artist",
        description="Soft fabrics, colorful treasures, and materials to create with.",
        gifts={
            "love": ("428", "444", "394"),  # Cloth, Duck Feather, Rainbow Shell
            "like": ("440", "814", "393", "66"),  # Wool, Squid Ink, Coral, Amethyst
            "dislike": ("335", "334"),  # Iron Bar, Copper Bar
            "hate": ("168", "170"),  # Trash, Broken Glasses
        },
    ),
)


def get_gift_preset(preset_id: str) -> GiftPreset:
    """Look up a preset by its stable ID; reject missing or obsolete IDs."""
    for preset in GIFT_PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(f"Unknown gift preset: {preset_id!r}")
