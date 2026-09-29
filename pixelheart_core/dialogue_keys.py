"""Plain-language occasions for an NPC's dialogue lines, and their game keys.

The formats follow the keys the game's own villagers use in
``Characters/Dialogue`` (for example ``Mon2``, ``summer_Mon``, ``summer_Thu4``,
``summer_1``, ``AcceptGift_(O)109`` and ``Saloon_Tue``) and the modding wiki's
dialogue page. Hearts suffixes mean "at least", and the game only checks 2, 4,
6, 8 and 10. Keys that don't fit an occasion are kept exactly as written.
"""
from __future__ import annotations

import re

from .locations import LOCATIONS_BY_ID

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = dict(zip(DAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
SEASONS = ("spring", "summer", "fall", "winter")
HEARTS = (0, 2, 4, 6, 8, 10)
OCCASIONS = {
    "introduction": "The first time they meet",
    "weekday": "On a day of the week",
    "date": "On a date",
    "place": "At a place",
    "gift": "When given a gift",
    "birthday_gift": "When given a birthday gift",
    "other": "Something else (game key)",
}

_SEASON = "|".join(SEASONS)
_DAY = "|".join(DAYS)
_WEEKDAY = re.compile(rf"(?:({_SEASON})_)?({_DAY})(10|8|6|4|2)?\Z")
_DATE = re.compile(rf"({_SEASON})_([1-9]|1[0-9]|2[0-8])\Z")
_DAY_SUFFIX = re.compile(rf"(.+)_({_DAY})\Z")


def parse_trigger(key, *, places=()):
    """The occasion a key stands for. ``places`` adds the project's own place IDs."""
    key = key if isinstance(key, str) else ""
    if key == "Introduction":
        return {"occasion": "introduction"}
    match = _WEEKDAY.fullmatch(key)
    if match:
        return {"occasion": "weekday", "day": match[2], "season": match[1] or "", "hearts": int(match[3] or 0)}
    match = _DATE.fullmatch(key)
    if match:
        return {"occasion": "date", "season": match[1], "day": int(match[2])}
    if key.startswith("AcceptGift_") and len(key) > len("AcceptGift_"):
        return {"occasion": "gift", "item": key[len("AcceptGift_"):]}
    if key in ("AcceptBirthdayGift_Positive", "AcceptBirthdayGift_Negative"):
        return {"occasion": "birthday_gift", "liked": key.endswith("Positive")}
    known = set(LOCATIONS_BY_ID) | set(places)
    if key in known:
        return {"occasion": "place", "location": key, "day": ""}
    match = _DAY_SUFFIX.fullmatch(key)
    if match and match[1] in known:
        return {"occasion": "place", "location": match[1], "day": match[2]}
    return {"occasion": "other", "key": key}


def build_trigger(spec):
    """The game key for an occasion; raises ValueError for impossible choices."""
    occasion = spec.get("occasion")
    if occasion == "introduction":
        return "Introduction"
    if occasion == "weekday":
        day, season, hearts = spec.get("day"), spec.get("season", ""), spec.get("hearts", 0)
        if day not in DAYS or season not in ("", *SEASONS) or hearts not in HEARTS:
            raise ValueError("Choose a day, a season, and 2, 4, 6, 8 or 10 hearts.")
        return (season + "_" if season else "") + day + (str(hearts) if hearts else "")
    if occasion == "date":
        season, day = spec.get("season"), spec.get("day")
        if season not in SEASONS or type(day) is not int or not 1 <= day <= 28:
            raise ValueError("Choose a season and a day from 1 to 28.")
        return f"{season}_{day}"
    if occasion == "place":
        location, day = spec.get("location", ""), spec.get("day", "")
        if not isinstance(location, str) or not location or day not in ("", *DAYS):
            raise ValueError("Choose a place.")
        return location + ("_" + day if day else "")
    if occasion == "gift":
        item = spec.get("item", "")
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Choose the gift.")
        return "AcceptGift_" + item.strip()
    if occasion == "birthday_gift":
        return "AcceptBirthdayGift_" + ("Positive" if spec.get("liked", True) else "Negative")
    if occasion == "other":
        return spec.get("key", "")
    raise ValueError("Choose when they say it.")


def _hearts(hearts):
    return "10 hearts" if hearts == 10 else f"{hearts}+ hearts"


def describe_trigger(key, *, places=None, item_names=None):
    """A short plain-language title for a dialogue key."""
    places = dict(places or {})
    spec = parse_trigger(key, places=places)
    occasion = spec["occasion"]
    if occasion == "introduction":
        return OCCASIONS["introduction"]
    if occasion == "weekday":
        text = DAY_NAMES[spec["day"]] + "s"
        if spec["season"]:
            text = spec["season"].title() + " " + text
        return text + (" · " + _hearts(spec["hearts"]) if spec["hearts"] else "")
    if occasion == "date":
        return f"{spec['season'].title()} {spec['day']}"
    if occasion == "place":
        location = spec["location"]
        name = places.get(location) or (LOCATIONS_BY_ID[location].name if location in LOCATIONS_BY_ID else location)
        return f"At {name}" + (f" on {DAY_NAMES[spec['day']]}s" if spec["day"] else "")
    if occasion == "gift":
        item = spec["item"]
        name = (item_names or {}).get(item)
        return f"When given {name}" if name else f"When given item {item}"
    if occasion == "birthday_gift":
        return "When given a birthday gift they " + ("like" if spec["liked"] else "don't like")
    return f"Game key: {key}" if key else "Choose when they say it"
