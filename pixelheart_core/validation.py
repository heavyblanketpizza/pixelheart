"""Validate and normalize partial character drafts without a storage dependency."""
import re
import uuid

from .dialogue_templates import MAX_DIALOGUES
from .provenance import source_metadata


class DraftValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Some character fields need attention.")


TEXT_FIELDS = {
    "name": 64, "internal_name": 64, "tagline": 160, "pronouns": 32,
    "occupation": 80, "bio": 12000, "home_map": 80,
}
GENDERS = ("Male", "Female", "Undefined")
ENUM_FIELDS = {
    "gender": set(GENDERS),
    "season": {"spring", "summer", "fall", "winter"},
    "age": {"adult"},
    "manners": {"polite", "neutral", "rude"},
    "social_anxiety": {"shy", "neutral", "outgoing"},
    "optimism": {"positive", "neutral", "negative"},
    "palette": {"peach", "lavender", "sage"},
    "portrait_key": {"mira", "elliott", "juniper"},
}
INT_FIELDS = {"day": (1, 28), "home_x": (0, 1000), "home_y": (0, 1000)}
NESTED_FIELDS = {"dialogues", "schedule", "gifts", "events", "relationships"}
EDITABLE_FIELDS = set(TEXT_FIELDS) | set(ENUM_FIELDS) | set(INT_FIELDS) | NESTED_FIELDS | {"romanceable", "life"}
READ_ONLY_FIELDS = {"id", "status", "created_at", "updated_at", "portrait_url", "sprite_url"}
MAX_GIFTS_PER_TASTE = 5000


def infer_legacy_gender(pronouns):
    """Migrate exact legacy pronouns without guessing a new game identity."""
    if not isinstance(pronouns, str):
        return "Undefined"
    return {"he/him": "Male", "she/her": "Female"}.get(pronouns, "Undefined")


def validate_draft(data):
    if not isinstance(data, dict):
        raise DraftValidationError({"body": "Expected a JSON object."})
    errors = {}
    cleaned = {}
    for key in data:
        if key not in EDITABLE_FIELDS | READ_ONLY_FIELDS:
            errors[key] = "This field is not editable."
    for key, limit in TEXT_FIELDS.items():
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str):
            errors[key] = "Enter text."
        elif len(value) > limit:
            errors[key] = f"Use {limit} characters or fewer."
        elif "\x00" in value:
            errors[key] = "Null characters are not allowed."
        elif key in {"name", "internal_name", "home_map"} and not value.strip():
            errors[key] = "This field is required."
        elif key == "internal_name" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            errors[key] = "Start with a letter; use only letters, numbers, and underscores."
        elif key == "home_map" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value):
            errors[key] = "Start the map ID with a letter; use letters, numbers, underscores, dots, or hyphens."
        else:
            cleaned[key] = value.strip() if key != "bio" else value
    for key, choices in ENUM_FIELDS.items():
        if key in data:
            value = data[key]
            if not isinstance(value, str) or value not in choices:
                if key == "age":
                    errors[key] = "Choose adult. Pixelheart creates adult love-interest NPCs only."
                else:
                    errors[key] = "Choose one of: " + ", ".join(sorted(choices)) + "."
            else:
                cleaned[key] = value
    for key, (low, high) in INT_FIELDS.items():
        if key in data:
            value = data[key]
            if type(value) is not int or not low <= value <= high:
                errors[key] = f"Enter a whole number from {low} to {high}."
            else:
                cleaned[key] = value
    if "romanceable" in data:
        if type(data["romanceable"]) is not bool:
            errors["romanceable"] = "Choose true or false."
        else:
            cleaned["romanceable"] = data["romanceable"]
    for key in NESTED_FIELDS:
        if key in data:
            try:
                cleaned[key] = validate_nested(key, data[key])
            except DraftValidationError as exc:
                errors.update(exc.errors)
    if "life" in data:
        from .life import life_structure_issues, normalize_life
        life_errors = life_structure_issues(data["life"])
        errors.update({issue["field"]: issue["message"] for issue in life_errors})
        if not life_errors:
            cleaned["life"] = normalize_life(data["life"])
    if errors:
        raise DraftValidationError(errors)
    return cleaned


def validate_nested(key, value):
    def fail(message, suffix=""):
        raise DraftValidationError({key + suffix: message})

    if key == "gifts":
        categories = {"love", "like", "dislike", "hate"}
        if not isinstance(value, dict) or set(value) - categories:
            fail("Use love, like, dislike, and hate gift lists.")
        result = {}
        for category in categories:
            items = value.get(category, [])
            if not isinstance(items, list) or len(items) > MAX_GIFTS_PER_TASTE:
                fail(f"Use a list with up to {MAX_GIFTS_PER_TASTE} gifts.", "." + category)
            if any(not isinstance(item, str) or not item.strip() or len(item) > 120 or "\x00" in item for item in items):
                fail("Each gift must be nonempty text of at most 120 characters.", "." + category)
            result[category] = list(dict.fromkeys(item.strip() for item in items))
        return result

    limits = {"dialogues": MAX_DIALOGUES, "schedule": 100, "events": 100, "relationships": 100}
    if not isinstance(value, list) or len(value) > limits[key]:
        fail(f"Use a list with up to {limits[key]} entries.")
    if key in {"events", "relationships"}:
        # Authoring records retain extension metadata. Playability is checked
        # separately, so an unfinished idea can always be saved.
        from .story import normalize_event, normalize_relationship, structure_issues
        normalize = normalize_event if key == "events" else normalize_relationship
        results = []
        seen = set()
        for index, entry in enumerate(value):
            issues = structure_issues(entry, key)
            if issues:
                raise DraftValidationError({f"{key}.{index}" + ("." + issue["field"] if issue["field"] else ""): issue["message"] for issue in issues})
            result = normalize(entry)
            if result["id"] in seen:
                fail("Each entry needs a unique text ID of at most 100 characters.", f".{index}.id")
            seen.add(result["id"])
            results.append(result)
        return results
    schemas = {
        "dialogues": {"trigger": ("text", 120, "Introduction"), "text": ("text", 8000, "")},
        "schedule": {"time": ("time", 0, "600"), "location": ("text", 80, "Town"), "x": ("int", 1000, 32), "y": ("int", 1000, 62), "facing": ("facing", 0, "down"), "activity": ("text", 300, "")},
        "events": {"name": ("text", 100, "New event"), "hearts": ("int", 14, 2), "location": ("text", 80, "Town"), "description": ("text", 8000, "")},
        "relationships": {"name": ("text", 80, ""), "relation": ("text", 80, "Friend"), "description": ("text", 2000, "")},
    }
    schema = schemas[key]
    results = []
    seen_ids = set()
    for index, entry in enumerate(value):
        suffix = f".{index}"
        metadata_fields = {"source", "source_history"} if key == "dialogues" else set()
        if not isinstance(entry, dict) or set(entry) - (set(schema) | {"id"} | metadata_fields):
            fail("Each entry must be an object with the expected fields.", suffix)
        entry_id = entry.get("id", str(uuid.uuid4()))
        if not isinstance(entry_id, str) or len(entry_id) > 100 or not entry_id or entry_id in seen_ids:
            fail("Each entry needs a unique text ID of at most 100 characters.", suffix + ".id")
        seen_ids.add(entry_id)
        result = {"id": entry_id}
        if key == "dialogues":
            try:
                result.update(source_metadata(entry))
            except ValueError as exc:
                fail(str(exc), suffix + ".source")
        for field, (kind, maximum, default) in schema.items():
            item = entry.get(field, default)
            if kind == "text":
                if not isinstance(item, str) or len(item) > maximum or "\x00" in item:
                    fail(f"Enter text of at most {maximum} characters.", suffix + "." + field)
            elif kind == "int":
                if type(item) is not int or not 0 <= item <= maximum:
                    fail(f"Enter a whole number from 0 to {maximum}.", suffix + "." + field)
            elif kind == "time":
                if type(item) is int:
                    item = str(item)
                if not isinstance(item, str) or not re.fullmatch(r"(?:[0-2]?\d:[0-5]\d|[0-2]?\d[0-5]\d)", item):
                    fail("Enter a time such as 06:00 or 600.", suffix + "." + field)
            elif kind == "facing":
                if type(item) is int:
                    item = str(item)
                if not isinstance(item, str) or item not in {"up", "right", "down", "left", "0", "1", "2", "3"}:
                    fail("Choose up, right, down, or left.", suffix + "." + field)
            result[field] = item
        results.append(result)
    return results
