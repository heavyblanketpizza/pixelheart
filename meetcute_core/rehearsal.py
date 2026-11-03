"""Readable previews of supported dialogue substitutions; authored text is untouched."""
from __future__ import annotations

import re

_PORTRAIT = re.compile(r"\$(?:[hslanu]\b|\d+(?=\s*(?:$|[#$])))")


def _gender_text(text, farmer_gender):
    result = str(text or "")
    gender = 0 if str(farmer_gender).lower() == "male" else 1
    modern = "${" in result

    def choose(match):
        payload = match.group(1)
        options = payload.split("¦" if "¦" in payload else "^")
        return options[gender] if len(options) in (2, 3) else match.group(0)

    result = re.sub(r"\$\{([^{}]*)\}\$", choose, result)
    # Legacy dialogue uses two alternatives separated across the whole line.
    if result.count("^") == 1 and not modern:
        result = result.split("^")[gender]
    return result


def portrait_expression(text, *, farmer_gender="female"):
    """Return the selected branch's last ordinary expression, ignoring commands."""
    matches = _PORTRAIT.findall(_gender_text(text, farmer_gender))
    face = matches[-1][1:] if matches else "0"
    return int(face) if face.isdigit() else {"h": 1, "s": 2, "u": 3, "l": 4, "a": 5, "n": 0}[face]


def preview_dialogue(text, *, farmer_name="Farmer", farmer_gender="female"):
    """Resolve name, gender alternatives and page/portrait tags for rehearsal.

    This is a reader preview, not an implementation of the game's dialogue
    interpreter. Other commands are deliberately retained for the author.
    """
    result = _gender_text(text, farmer_gender).replace("@", farmer_name.strip() or "Farmer")
    result = result.replace("#$b#", "\n").replace("#$e#", "\n\n")
    return _PORTRAIT.sub("", result)
