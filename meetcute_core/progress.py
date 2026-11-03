"""What a character still needs before they feel alive in the valley."""
from __future__ import annotations

from .playtesting import _current_record

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STEP_KEYS = ("identity", "dialogue", "schedule", "gifts", "story", "artwork", "home", "export")


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def _step(key, title, done, status, next_action, optional=False):
    return {"key": key, "title": title, "done": bool(done), "status": status,
            "next_action": next_action, "optional": optional}


def project_progress(document, *, artwork_valid=None):
    """Eight steps in sidebar order. ``artwork_valid`` is False when checks found sheet errors."""
    document = document if isinstance(document, dict) else {}
    character = document.get("character", {}) if isinstance(document.get("character"), dict) else {}
    steps = []

    name = str(character.get("name", "")).strip()
    named = bool(name) and name != "New character"
    steps.append(_step("identity", "About them", named, f"Meet {name}" if named else "Give them a name",
                       "Give them a name and a birthday"))

    lines = {str(row.get("trigger", "")): str(row.get("text", "")).strip()
             for row in character.get("dialogues", []) if isinstance(row, dict)}
    needed = ("Introduction", *WEEKDAYS)
    written = sum(1 for key in needed if lines.get(key))
    steps.append(_step("dialogue", "Conversations", written == len(needed), f"{written} of {len(needed)} everyday lines",
                       "Write how they say hello, and a line for each day of the week"))

    stops = [row for row in character.get("schedule", []) if isinstance(row, dict)]
    steps.append(_step("schedule", "Daily routine", len(stops) >= 2, f"{_plural(len(stops), 'stop')} so far",
                       "Plan where they go during the day"))

    gifts = character.get("gifts", {}) if isinstance(character.get("gifts"), dict) else {}
    loved = len(gifts.get("love", []) or [])
    steps.append(_step("gifts", "Gifts", loved >= 1, _plural(loved, "loved gift") if loved else "No favorites yet",
                       "Choose the gifts they love"))

    events = [row for row in character.get("events", []) if isinstance(row, dict)]
    ready = sum(1 for row in events if isinstance(row.get("story"), dict) and row["story"].get("stage") == "ready")
    steps.append(_step("story", "Heart events", ready >= 1, f"{_plural(len(events) - ready, 'draft')}, {ready} ready",
                       "Finish a heart event and mark it ready"))

    artwork = document.get("artwork", {}) if isinstance(document.get("artwork"), dict) else {}
    portrait, sprite = bool(artwork.get("portrait")), bool(artwork.get("sprite"))
    if artwork_valid is False and (portrait or sprite):
        status = "Artwork needs a fix"
    elif portrait and sprite:
        status = "Portrait and sprite added"
    elif portrait or sprite:
        status = "Portrait added, sprite missing" if portrait else "Sprite added, portrait missing"
    else:
        status = "No artwork yet"
    steps.append(_step("artwork", "Portraits & sprites", portrait and sprite and artwork_valid is not False, status,
                       "Add their portrait and walking sprite"))

    world = document.get("world", {}) if isinstance(document.get("world"), dict) else {}
    homes = [row for row in world.get("locations", []) if isinstance(row, dict) and "interior" in row]
    steps.append(_step("home", "Home", bool(homes), "Decorated" if homes else "Optional · not started",
                       "Decorate their home", optional=True))

    installed = bool(_current_record(document, "last_install"))
    exported = bool(_current_record(document, "last_export"))
    status = "In your game" if installed else "Exported, not installed yet" if exported else "Not in your game yet"
    steps.append(_step("export", "Play in Stardew", installed, status, "Put them in your game and say hello"))
    return steps


def hearts_earned(steps):
    return sum(1 for step in steps if step["done"])


def next_step(steps):
    return next((step for step in steps if not step["done"] and not step["optional"]), None)
