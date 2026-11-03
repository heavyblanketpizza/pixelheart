"""Re-read the character mid-turn."""
from ..providers import ToolSpec
from ..summary import summarize
from . import ToolOutcome


SPEC = ToolSpec(
    "read_character",
    "Re-read the whole character after the creator edited it by hand. "
    "The current character is already in your context at the start of each turn.",
    {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)


def run(document, input, context):
    return ToolOutcome(document, None, summarize(document))


TOOLS = ((SPEC, run),)
