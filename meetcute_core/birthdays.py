"""Offline base-game birthdays and recurring festivals for Stardew Valley 1.6.

Names and dates were checked on 2026-09-19 against the official Stardew Valley
Wiki calendar, revision 185874:
https://stardewvalleywiki.com/mediawiki/index.php?title=Calendar&oldid=185874

All 34 NPC birthdays are included, even when the NPC has not yet appeared in a
particular save (for example, Kent and Leo). This is an authoring reference, not
a save-specific calendar. Mods can add or change birthdays. Festival dates are
informational and do not make a date unavailable for a character's birthday.
No game artwork or wiki prose is bundled, and no network access is required.
"""

SEASONS = ("spring", "summer", "fall", "winter")
DAYS_PER_SEASON = 28

_BIRTHDAYS = {
    "spring": {
        4: ("Kent",),
        7: ("Lewis",),
        10: ("Vincent",),
        14: ("Haley",),
        18: ("Pam",),
        20: ("Shane",),
        26: ("Pierre",),
        27: ("Emily",),
    },
    "summer": {
        4: ("Jas",),
        8: ("Gus",),
        10: ("Maru",),
        13: ("Alex",),
        17: ("Sam",),
        19: ("Demetrius",),
        22: ("Dwarf",),
        24: ("Willy",),
        26: ("Leo",),
    },
    "fall": {
        2: ("Penny",),
        5: ("Elliott",),
        11: ("Jodi",),
        13: ("Abigail",),
        15: ("Sandy",),
        18: ("Marnie",),
        21: ("Robin",),
        24: ("George",),
    },
    "winter": {
        1: ("Krobus",),
        3: ("Linus",),
        7: ("Caroline",),
        10: ("Sebastian",),
        14: ("Harvey",),
        17: ("Wizard",),
        20: ("Evelyn",),
        23: ("Leah",),
        26: ("Clint",),
    },
}

_FESTIVALS = {
    "spring": {
        13: ("Egg Festival",),
        15: ("Desert Festival",),
        16: ("Desert Festival",),
        17: ("Desert Festival",),
        24: ("Flower Dance",),
    },
    "summer": {
        11: ("Luau",),
        20: ("Trout Derby",),
        21: ("Trout Derby",),
        28: ("Dance of the Moonlight Jellies",),
    },
    "fall": {
        16: ("Stardew Valley Fair",),
        27: ("Spirit's Eve",),
    },
    "winter": {
        8: ("Festival of Ice",),
        12: ("SquidFest",),
        13: ("SquidFest",),
        15: ("Night Market",),
        16: ("Night Market",),
        17: ("Night Market",),
        25: ("Feast of the Winter Star",),
    },
}


def _validate_season(season):
    if not isinstance(season, str) or season not in SEASONS:
        raise ValueError("Choose spring, summer, fall, or winter.")


def _validate_date(season, day):
    _validate_season(season)
    if type(day) is not int or not 1 <= day <= DAYS_PER_SEASON:
        raise ValueError("Choose a whole-number day from 1 to 28.")


def birthdays_on(season: str, day: int) -> tuple[str, ...]:
    """Return NPC names on a valid date, or an empty tuple for a free date.

    Seasons use the lowercase project values. Invalid dates raise ValueError
    instead of being reported as available.
    """
    _validate_date(season, day)
    return _BIRTHDAYS[season].get(day, ())


def birthdays_for_season(season: str) -> dict[int, tuple[str, ...]]:
    """Return an independent day-to-NPC mapping, omitting days without birthdays."""
    _validate_season(season)
    return dict(_BIRTHDAYS[season])


def festivals_on(season: str, day: int) -> tuple[str, ...]:
    """Return recurring festival names on a valid date, including passive festivals."""
    _validate_date(season, day)
    return _FESTIVALS[season].get(day, ())


def festivals_for_season(season: str) -> dict[int, tuple[str, ...]]:
    """Return an independent day-to-festival mapping, omitting non-festival days."""
    _validate_season(season)
    return dict(_FESTIVALS[season])
