"""Bounded, explicit NPC schedule animations using authored sprite frames.

The supported subset of Data/animationDescriptions contains three frame lists,
an optional empty message field, and optional laying_down / offset flags.
Activity descriptions never become animation instructions.

Reference: https://stardewvalleywiki.com/Modding:Schedule_data
"""
from __future__ import annotations

import re


ANIMATION_KEY = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
MAX_ANIMATIONS = 32
MAX_DESCRIPTION_LENGTH = 4096
_FRAMES = re.compile(r"[0-9]{1,4}(?: [0-9]{1,4}){0,255}\Z")
_OFFSET = re.compile(r"offset (-?[0-9]{1,3}) (-?[0-9]{1,3})\Z")


def _description_frames(value):
    if not isinstance(value, str) or len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValueError("Use an animation description of at most 4096 characters.")
    parts = value.split("/")
    if not 3 <= len(parts) <= 6 or any(not _FRAMES.fullmatch(part) for part in parts[:3]):
        raise ValueError("Use three slash-separated, nonempty frame lists: entry/repeat/leaving.")
    frames = [int(frame) for part in parts[:3] for frame in part.split(" ")]
    if any(frame > 4095 for frame in frames):
        raise ValueError("Animation frame indices must be between 0 and 4095.")
    if len(parts) > 3 and parts[3]:
        raise ValueError("Leave the animation message field empty; message references are not supported.")
    flags = set()
    for part in parts[4:]:
        offset = _OFFSET.fullmatch(part)
        if part == "laying_down":
            flag = "laying_down"
        elif offset and all(-64 <= int(value) <= 64 for value in offset.groups()):
            flag = "offset"
        else:
            raise ValueError("Use laying_down or offset X Y with pixel offsets from -64 to 64.")
        if flag in flags:
            raise ValueError("Each animation flag may appear only once.")
        flags.add(flag)
    return frames


def animation_issues(value, sprite_size=None, *, appearance=None):
    """Validate owned definitions, optionally against a selected sprite sheet."""
    issues = []

    def add(field, message):
        if appearance:
            message = f"{appearance.title()} appearance: {message}"
        issues.append({"level": "error", "field": field, "message": message})

    if not isinstance(value, dict) or len(value) > MAX_ANIMATIONS:
        add("animations", "Animations must be an object with at most 32 named frame descriptions.")
        return issues
    frame_count = sprite_size[0] // 16 * (sprite_size[1] // 32) if sprite_size else None
    for key, description in value.items():
        field = f"animations.{key}"
        if not isinstance(key, str) or not ANIMATION_KEY.fullmatch(key):
            add(field, "Start animation names with a lowercase letter; use lowercase letters, numbers, and underscores, at most 48 characters.")
            continue
        try:
            frames = _description_frames(description)
        except ValueError as exc:
            add(field, str(exc))
            continue
        if frame_count is not None and any(frame >= frame_count for frame in frames):
            add(field, f"Animation frames must fit your {frame_count}-frame sprite sheet.")
    return issues


def animation_reference_error(stop, definitions):
    """Return an error for an explicit stop animation, or None when valid."""
    key = stop.get("animation", "")
    if key == "":
        return None
    if not isinstance(key, str) or not ANIMATION_KEY.fullmatch(key):
        return "Choose a named animation using lowercase letters, numbers, and underscores."
    if not isinstance(definitions, dict) or key not in definitions:
        return f"Define the owned animation '{key}' in character.animations first."
    if stop.get("location") == "bed":
        return "Use an explicit map and tile for an animation; the special bed destination ignores this field."
    return None


def animation_suffix(stop, npc_id):
    """Compile only an explicit owned key, never an activity note."""
    key = stop.get("animation", "")
    if key == "":
        return ""
    if not npc_id or not isinstance(key, str) or not ANIMATION_KEY.fullmatch(key):
        raise ValueError("An explicit schedule animation needs a valid owned key and NPC ID.")
    return f" {npc_id.lower()}_{key}"


def animation_entries(character, npc_id):
    return {f"{npc_id.lower()}_{key}": description
            for key, description in character.get("animations", {}).items()}
