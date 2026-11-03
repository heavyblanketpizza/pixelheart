"""Stardew's game clock, in minutes from midnight; the day ends at 2:00 AM."""

import re


def game_minutes(value):
    raw = str(value).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hours, minutes = map(int, raw.split(":"))
    elif re.fullmatch(r"\d{3,4}", raw):
        hours, minutes = divmod(int(raw), 100)
    else:
        return None
    total = hours * 60 + minutes
    return total if 0 <= minutes < 60 and minutes % 10 == 0 and 360 <= total <= 1560 else None


def game_time(minutes):
    hours, remainder = divmod(minutes, 60)
    return str(hours * 100 + remainder)


def clock_label(minutes):
    """The game's 12-hour clock, e.g. 6:00 AM or 1:00 AM (next day)."""
    hours, remainder = divmod(minutes, 60)
    day_hours = hours % 24
    text = f"{day_hours % 12 or 12}:{remainder:02d} {'AM' if day_hours < 12 else 'PM'}"
    return text + (" (next day)" if hours >= 24 else "")
