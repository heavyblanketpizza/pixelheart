"""Who they are: identity fields and the storyline brief."""
from __future__ import annotations

from pixelheart_core.story_planning import normalize_storyline, storyline_structure_issues
from pixelheart_core.validation import DraftValidationError, validate_draft

from ..providers import ToolSpec
from . import ToolError, ToolOutcome


def _text(description):
    return {"type": ["string", "null"], "description": description}


def _enum(choices, description):
    return {"anyOf": [{"type": "string", "enum": list(choices)}, {"type": "null"}], "description": description}


def _schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


IDENTITY_PROPERTIES = {
    "name": _text("Display name, up to 64 characters."),
    "tagline": _text("One line that captures them, up to 160 characters."),
    "pronouns": _text("For example she/her, he/him or they/them."),
    "gender": _enum(("Male", "Female", "Undefined"), "Used by the game's gendered dialogue."),
    "occupation": _text("What they do, up to 80 characters."),
    "bio": _text("Who they are, in a few paragraphs (up to 12000 characters)."),
    "birthday_season": _enum(("spring", "summer", "fall", "winter"), "Season of their birthday."),
    "birthday_day": {"type": ["integer", "null"], "description": "Day of their birthday, 1 to 28."},
    "manners": _enum(("polite", "neutral", "rude"), "How they treat people."),
    "social_anxiety": _enum(("shy", "neutral", "outgoing"), "How they are around others."),
    "optimism": _enum(("positive", "neutral", "negative"), "Their outlook."),
    "romanceable": {"type": ["boolean", "null"], "description": "Whether the farmer can date and marry them."},
    "home_map": _text("The map they live on, usually Town or a place the creator designed."),
}
_CAPTIONS = {"name": "name", "tagline": "tagline", "pronouns": "pronouns", "gender": "gender", "occupation": "occupation",
             "bio": "bio", "birthday_season": "birthday", "birthday_day": "birthday", "manners": "manners",
             "social_anxiety": "social anxiety", "optimism": "optimism", "romanceable": "romance", "home_map": "home map"}
_RENAMES = {"birthday_season": "season", "birthday_day": "day"}
BRIEF_FIELDS = ("desire", "question", "boundaries", "motif")

IDENTITY_SPEC = ToolSpec(
    "set_identity",
    "Set who the character is: name, tagline, pronouns, gender, occupation, bio, birthday, personality "
    "(manners, social anxiety, optimism), whether they can be romanced, and their home map. "
    "Pass null for anything you are not changing. The internal name never changes.",
    _schema(IDENTITY_PROPERTIES),
)
BRIEF_SPEC = ToolSpec(
    "set_storyline_brief",
    "Set the storyline brief that guides their heart events: what they desire, the question their story asks, "
    "their boundaries, and a recurring motif. Pass null to leave a field as it is.",
    _schema({field: _text(f"The {field} of their story, up to 8000 characters.") for field in BRIEF_FIELDS}),
)


def _given(input, allowed):
    unknown = sorted(set(input) - set(allowed))
    if unknown:
        raise ToolError("Unknown fields: " + ", ".join(unknown) + ".")
    given = {key: value for key, value in input.items() if value is not None}
    if not given:
        raise ToolError("Give at least one field to change.")
    return given


def set_identity(document, input, context):
    given = _given(input, IDENTITY_PROPERTIES)
    partial = {_RENAMES.get(key, key): value for key, value in given.items()}
    try:
        cleaned = validate_draft(partial)
    except DraftValidationError as exc:
        raise ToolError("; ".join(f"{field}: {message}" for field, message in exc.errors.items())) from None
    character = document["character"]
    renamed = "name" in cleaned and cleaned["name"] != character.get("name")
    character.update(cleaned)
    captions = list(dict.fromkeys(_CAPTIONS[key] for key in IDENTITY_PROPERTIES if key in given))
    detail = "Set " + ", ".join(captions)
    if renamed:
        detail += f" · Internal name stays {character.get('internal_name', '')}"
    receipt = {"section": "identity", "title": "Updated About them", "detail": detail}
    return ToolOutcome(document, receipt, f"Done. {detail}.")


def set_storyline_brief(document, input, context):
    given = _given(input, BRIEF_FIELDS)
    if any(not isinstance(value, str) for value in given.values()):
        raise ToolError("Each brief field must be text.")
    character = document["character"]
    storyline = normalize_storyline(character.get("storyline"))
    storyline["brief"].update(given)
    issues = [issue["message"] for issue in storyline_structure_issues(storyline) if issue["level"] == "error"]
    if issues:
        raise ToolError("; ".join(issues))
    character["storyline"] = storyline
    detail = "Set " + ", ".join(field for field in BRIEF_FIELDS if field in given)
    receipt = {"section": "story", "title": "Updated storyline brief", "detail": detail}
    return ToolOutcome(document, receipt, f"Done. {detail}.")


TOOLS = ((IDENTITY_SPEC, set_identity), (BRIEF_SPEC, set_storyline_brief))
