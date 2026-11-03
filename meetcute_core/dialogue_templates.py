"""Readable dialogue previews and lossless, explicit example imports."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import re
import uuid

from .provenance import validate_source


MAX_DIALOGUES = 2000
_DIALOGUE_KEY = re.compile(r"[A-Za-z0-9_.*()+:-]{1,120}\Z")
_EXPRESSION = re.compile(r"\$(?:[hslanu]\b|\d+(?=\s*(?:$|[#$])))")
_ADVANCED_MARKER = re.compile(r"[$#^%|{}\[\]]")


def dialogue_has_advanced_commands(text: str) -> bool:
    """Identify scripts the text preview cannot faithfully interpret.

    Numeric portrait tags are simple only at the end of a dialogue segment.
    In particular, ``$1 mailFlag#...`` is a conditional command, not portrait 1.
    Keep unfamiliar control syntax visible instead of guessing its meaning.
    """
    remaining = _EXPRESSION.sub("", text)
    remaining = remaining.replace("#$b#", "").replace("#$e#", "")
    return _ADVANCED_MARKER.search(remaining) is not None


def dialogue_preview(text: str) -> str:
    """Render simple lines; preserve advanced scripts exactly as authored."""
    if dialogue_has_advanced_commands(text):
        return text
    text = _EXPRESSION.sub("", text)
    return text.replace("#$b#", "\n").replace("#$e#", "\n\n").replace("@", "Farmer")


def _selected_examples(examples):
    """Validate the entire selection before making any project changes."""
    if not isinstance(examples, (list, tuple)):
        raise ValueError("Choose a list of dialogue examples.")
    selected = []
    seen = set()
    for example in examples:
        if not isinstance(example, Mapping):
            raise ValueError("Each dialogue example needs a trigger and text.")
        trigger, text = example.get("trigger"), example.get("text")
        if (not isinstance(trigger, str) or len(trigger) > 120
                or not _DIALOGUE_KEY.fullmatch(trigger.strip())):
            raise ValueError("Each example needs a valid dialogue trigger of at most 120 characters.")
        trigger = trigger.strip()
        if trigger in seen:
            raise ValueError(f'Dialogue trigger "{trigger}" is selected more than once.')
        if not isinstance(text, str) or not text.strip() or len(text) > 8000 or "\x00" in text:
            raise ValueError(f'Dialogue example "{trigger}" needs nonempty text of at most 8000 characters.')
        if "{{" in text:
            raise ValueError(f'Dialogue example "{trigger}" cannot contain Content Patcher tokens.')
        seen.add(trigger)
        selected.append({"trigger": trigger, "text": text,
                         **({"source": validate_source(example["source"])} if "source" in example else {})})
    return selected


def _existing_by_trigger(records):
    if not isinstance(records, (list, tuple)) or any(not isinstance(row, dict) for row in records):
        raise ValueError("Existing dialogue must be a list of dialogue records.")
    indexed = {}
    for index, row in enumerate(records):
        trigger = row.get("trigger")
        if isinstance(trigger, str) and trigger.strip():
            indexed.setdefault(trigger.strip(), []).append(index)
    return indexed


def dialogue_conflicts(records, examples) -> dict[str, list[dict]]:
    """Return detached copies of matching rows, keyed by trimmed trigger."""
    selected = _selected_examples(examples)
    indexed = _existing_by_trigger(records)
    return {
        example["trigger"]: [copy.deepcopy(records[index]) for index in indexed[example["trigger"]]]
        for example in selected if example["trigger"] in indexed
    }


def apply_dialogue_examples(records, examples, decisions=None) -> list[dict]:
    """Copy selected examples atomically, keeping collisions unless replaced.

    ``decisions`` maps trimmed triggers to ``"keep"`` or ``"replace"``.
    Replacements retain the existing row's identity, order, and metadata.
    Duplicate existing triggers must be resolved manually before replacement.
    Only portable source notices accompany imported text; teaching notes do not.
    """
    selected = _selected_examples(examples)
    indexed = _existing_by_trigger(records)
    decisions = {} if decisions is None else decisions
    if not isinstance(decisions, Mapping) or any(
        not isinstance(trigger, str) or decision not in ("keep", "replace")
        for trigger, decision in decisions.items()
    ):
        raise ValueError('Choose "keep" or "replace" for each conflicting dialogue trigger.')

    additions = sum(example["trigger"] not in indexed for example in selected)
    if len(records) + additions > MAX_DIALOGUES:
        raise ValueError(f"A character can have up to {MAX_DIALOGUES} dialogue entries. Remove entries or select fewer examples.")
    for example in selected:
        trigger = example["trigger"]
        if decisions.get(trigger, "keep") == "replace" and len(indexed.get(trigger, ())) > 1:
            raise ValueError(f'Dialogue trigger "{trigger}" matches multiple existing entries. Resolve the duplicate triggers before replacing it.')

    result = copy.deepcopy(list(records))
    used_ids = {row.get("id") for row in records if isinstance(row.get("id"), str)}
    for example in selected:
        trigger = example["trigger"]
        matches = indexed.get(trigger)
        if matches:
            if decisions.get(trigger, "keep") == "replace":
                result[matches[0]]["text"] = example["text"]
                result[matches[0]].pop("source", None)
                result[matches[0]].pop("source_history", None)
                if "source" in example:
                    result[matches[0]]["source"] = copy.deepcopy(example["source"])
        else:
            entry_id = str(uuid.uuid4())
            while entry_id in used_ids:
                entry_id = str(uuid.uuid4())
            used_ids.add(entry_id)
            result.append({"id": entry_id, **copy.deepcopy(example)})
    return result


def overwrite_dialogue_examples(records, examples) -> list[dict]:
    """Import a complete template, overwriting matches and keeping other lines.

    Every matching trimmed trigger receives the imported text and provenance,
    retaining its identity, spelling, order, and metadata. Missing triggers are
    appended in template order. Validation is atomic and all data is detached.
    """
    selected = _selected_examples(examples)
    indexed = _existing_by_trigger(records)
    if not selected:
        raise ValueError("Choose a dialogue template with at least one entry.")
    additions = sum(example["trigger"] not in indexed for example in selected)
    if len(records) + additions > MAX_DIALOGUES:
        raise ValueError(f"A character can have up to {MAX_DIALOGUES} dialogue entries. Remove entries or choose a smaller template.")

    result = copy.deepcopy(list(records))
    used_ids = {row.get("id") for row in records if isinstance(row.get("id"), str)}
    for example in selected:
        matches = indexed.get(example["trigger"])
        if matches:
            for index in matches:
                row = result[index]
                row["text"] = example["text"]
                row.pop("source", None)
                row.pop("source_history", None)
                if "source" in example:
                    row["source"] = copy.deepcopy(example["source"])
        else:
            entry_id = str(uuid.uuid4())
            while entry_id in used_ids:
                entry_id = str(uuid.uuid4())
            used_ids.add(entry_id)
            result.append({"id": entry_id, **copy.deepcopy(example)})
    return result
