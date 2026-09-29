"""Dialogue split into the boxes the game shows, each with its portrait.

``#$b#`` starts the next box and ``#$e#`` ends the conversation (the rest is
said the next time you talk). A portrait command at the end of a box sets its
expression; boxes without one show the neutral portrait. Long text continues
in more boxes. Line breaks are approximate: the game measures its own font.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

LINE_CHARS = 38
BOX_LINES = 4


@dataclass(frozen=True)
class Emotion:
    key: str
    command: str
    index: int
    label: str


EMOTIONS = (
    Emotion("neutral", "$0", 0, "Neutral"),
    Emotion("happy", "$h", 1, "Happy"),
    Emotion("sad", "$s", 2, "Sad"),
    Emotion("unique", "$u", 3, "Unique"),
    Emotion("love", "$l", 4, "Love"),
    Emotion("angry", "$a", 5, "Angry"),
)
_LETTERS = {emotion.command[1]: emotion.index for emotion in EMOTIONS if emotion.command[1].isalpha()}


@dataclass(frozen=True)
class Page:
    lines: tuple
    portrait: int
    continued: bool = False
    new_conversation: bool = False
    raw: bool = False


_BREAK = re.compile(r"#\$[be]#")
_TRAILING = re.compile(r"\s*\$([hsula]|\d+)\s*\Z")
_GENDER = re.compile(r"\$\{([^{}]*)\}\$")
_COMMAND_MARKERS = re.compile(r"[$#^%|{}\[\]]")


def _wrap(text):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            while len(word) > LINE_CHARS:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:LINE_CHARS])
                word = word[LINE_CHARS:]
            if not word:
                continue
            candidate = f"{line} {word}" if line else word
            if len(candidate) > LINE_CHARS:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
    return lines


def _portrait(segment):
    match = _TRAILING.search(segment)
    if match is None:
        return segment, 0
    value = match[1]
    return segment[:match.start()], _LETTERS[value] if value in _LETTERS else int(value)


def dialogue_pages(text, farmer="Farmer"):
    """The boxes a line of dialogue fills, in order."""
    if not text:
        return []
    pages = []
    parts = _BREAK.split(text)
    markers = _BREAK.findall(text)
    for index, segment in enumerate(parts):
        segment, portrait = _portrait(segment)
        segment = _GENDER.sub(lambda match: "/".join(re.split(r"[\^¦]", match[1])), segment)
        segment = segment.replace("@", farmer)
        raw = bool(_COMMAND_MARKERS.search(segment))
        lines = [segment.strip()] if raw else _wrap(segment)
        starts_conversation = index > 0 and markers[index - 1] == "#$e#"
        chunks = [lines[start:start + BOX_LINES] for start in range(0, len(lines), BOX_LINES)] or [[]]
        for number, chunk in enumerate(chunks):
            pages.append(Page(tuple(chunk), portrait, continued=number > 0,
                              new_conversation=starts_conversation and number == 0, raw=raw))
    return pages


def set_emotion(text, cursor, command):
    """Set the expression of the box holding the cursor. ``$0`` removes the command.

    Returns the new text and a cursor position in the same box.
    """
    cursor = max(0, min(cursor, len(text)))
    start, end = 0, len(text)
    for match in _BREAK.finditer(text):
        if match.end() <= cursor:
            start = match.end()
        elif match.start() < cursor:
            # Inside a break: the box before it.
            end = match.start()
            break
        else:
            end = match.start()
            break
    segment = text[start:end]
    kept = _TRAILING.sub("", segment)
    if kept == segment:
        kept = segment.rstrip()
    replacement = kept + ("" if command == "$0" else command)
    return text[:start] + replacement + text[end:], start + len(replacement)
