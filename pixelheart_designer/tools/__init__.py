"""The designer's tools: pure functions from a document to a new document and a receipt.

Every tool receives a deep copy of the project document and returns a
``ToolOutcome``. Validation failures raise ``ToolError`` whose message goes
back to the model. Each tool module exports ``TOOLS``, a sequence of
``(ToolSpec, run)`` pairs where ``run(document, input, context)`` returns a
``ToolOutcome``.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from importlib import import_module

from ..providers import ToolSpec


class ToolError(ValueError):
    """The model's input could not be applied; the message is for the model."""


@dataclass(frozen=True)
class ToolContext:
    model_label: str = ""


@dataclass(frozen=True)
class ToolOutcome:
    document: dict
    receipt: dict | None
    message: str


# Registry order is the order the model sees the tools in.
_MODULES = ("read", "identity", "dialogue", "gifts", "routine", "events", "life")


@cache
def _registry():
    tools = {}
    for name in _MODULES:
        module = import_module(f"{__name__}.{name}")
        for spec, run in module.TOOLS:
            tools[spec.name] = (spec, run)
    return tools


def tool_specs() -> list[ToolSpec]:
    return [spec for spec, _ in _registry().values()]


def run_tool(name: str, document: dict, input: dict, context: ToolContext = ToolContext()) -> ToolOutcome:
    try:
        _, run = _registry()[name]
    except KeyError:
        raise ToolError(f"There is no tool called '{name}'.") from None
    if not isinstance(input, dict):
        raise ToolError("Tool input must be an object.")
    return run(deepcopy(document), input, context)
