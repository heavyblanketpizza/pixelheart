"""Load a character's full archived dialogue file on request.

Only source metadata and original teaching notes are bundled. English game
dialogue is fetched from an immutable archive into an OS cache outside Git.
The archive is preserved in full, with original keys, text, and source order.
It represents one archived character dialogue file; other game files contain
additional dialogue, and newer game releases may have changed these entries.
"""

from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_DOWNLOAD_BYTES = 256 * 1024
MAX_ENTRIES = 250
MAX_TEXT_LENGTH = 8000
NETWORK_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 30
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_COMMIT = "811fa276771551ca1c18f2327555c4d09eff6436"
_REPOSITORY = "FunnyEivske/StardewValley-NO"
_SOURCE_NAME = "StardewValley-NO English reference (GitHub)"
_ATTRIBUTION = f"Dialogue © ConcernedApe · source: {_SOURCE_NAME} · full archived English dialogue file"
DOCUMENTATION_URL = "https://stardewvalleywiki.com/Modding:Dialogue"
_IMPORT_TRIGGER = re.compile(r"[A-Za-z0-9_.*()+:-]{1,120}\Z")
_WEEKDAY_TRIGGER = re.compile(r"(?:(spring|summer|fall|winter)_)?(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(2|4|6|8|10)?\Z")
_DATE_TRIGGER = re.compile(r"(?:(spring|summer|fall|winter)_)?([1-9]|1[0-9]|2[0-8])\Z")
_DAY_NAMES = dict(zip(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))


def _example(trigger, title, when, lesson):
    return {"trigger": trigger, "title": title, "when": when, "lesson": lesson}


# Source text must never be added here. These are original explanations of keys
# and commands documented at DOCUMENTATION_URL.
DIALOGUE_TEMPLATES = {
    "abigail": {
        "name": "Abigail",
        "source_name": _SOURCE_NAME,
        "source_url": f"https://github.com/{_REPOSITORY}/blob/{_COMMIT}/Dialogue/Abigail.json",
        "download_url": f"https://raw.githubusercontent.com/{_REPOSITORY}/{_COMMIT}/Dialogue/Abigail.json",
        "sha256": "598612cc9bc028ff2cf4d8235daac54f3da4b2ccecaf1c41343e01edaac50cca",
        "attribution": _ATTRIBUTION,
        "examples": [
            _example("Introduction", "Introduction", "An initial greeting while the new farmer's Introduction topic is active (the first six days).", "A named topic supplies an opening greeting. #$e# saves the next part for another interaction; $9 selects portrait 9 (counting from 0). Your portrait sheet needs at least 10 frames for $9; change it to an expression your sheet has."),
            _example("Mon", "Weekday greeting", "On Monday, when a more specific dialogue does not take priority.", "Mon is the Monday fallback. Plain dialogue works without any formatting commands."),
            _example("summer_Mon", "Seasonal conversation", "On a summer Monday, when a more specific dialogue does not take priority.", "A season prefix narrows a weekday key. summer_Mon takes priority over Mon in summer."),
            _example("Tue4", "Friendship conversation", "On Tuesday with at least 4 hearts, when a more specific dialogue does not take priority.", "The 4 adds a minimum friendship level. $h selects the happy portrait; #$e# continues on the next interaction."),
        ],
    },
    "elliott": {
        "name": "Elliott",
        "source_name": _SOURCE_NAME,
        "source_url": f"https://github.com/{_REPOSITORY}/blob/{_COMMIT}/Dialogue/Elliott.json",
        "download_url": f"https://raw.githubusercontent.com/{_REPOSITORY}/{_COMMIT}/Dialogue/Elliott.json",
        "sha256": "0fddaa1326db47cf2abc75eeeaeebdf39f3fe7a0006fadb3a5a370d19b51fff6",
        "attribution": _ATTRIBUTION,
        "examples": [
            _example("Introduction", "Introduction", "An initial greeting while the new farmer's Introduction topic is active (the first six days).", "#$b# opens the next dialogue box in the same conversation. It lets an introduction unfold over several clicks."),
            _example("Mon", "Weekday greeting", "On Monday, when a more specific dialogue does not take priority.", "Mon is the Monday fallback. $h selects the happy portrait; #$e# continues only when the player talks again."),
            _example("summer_Mon", "Seasonal conversation", "On a summer Monday, when a more specific dialogue does not take priority.", "summer_Mon overrides Mon in summer. $u selects this character's unique portrait; #$e# saves the next part for another interaction."),
            _example("Sun6", "Friendship conversation", "On Sunday with at least 6 hearts, when a more specific dialogue does not take priority.", "The 6 adds a minimum friendship level. @ becomes the player's name, $h selects the happy portrait, and #$e# continues on the next interaction."),
        ],
    },
}


class WikiDialogueError(ValueError):
    """The archived dialogue file could not be loaded safely or completely."""


def _annotated_entries(data, template):
    """Add teaching notes without filtering or changing archived entries."""
    teaching_notes = {example["trigger"]: example for example in template["examples"]}
    entries = []
    for trigger, text in data.items():
        if trigger in teaching_notes:
            metadata = teaching_notes[trigger]
        elif match := _WEEKDAY_TRIGGER.fullmatch(trigger):
            season, day, hearts = match.groups()
            when = f"On {_DAY_NAMES[day]}"
            if season:
                when += f" in {season}"
            if hearts:
                when += f" with at least {hearts} hearts"
            when += ", when a more specific dialogue does not take priority."
            title = "Friendship conversation" if hearts else "Seasonal conversation" if season else "Weekday greeting"
            metadata = _example(trigger, title, when, "The weekday sets the day. A season prefix limits it to that season, and a number after the weekday sets a minimum friendship level. Keep the original commands while exploring, then adapt the writing and portraits for your character.")
        elif match := _DATE_TRIGGER.fullmatch(trigger):
            season, day = match.groups()
            date = f"{season} {day}" if season else f"day {day} of a season"
            metadata = _example(trigger, "Calendar conversation", f"On {date} in the first year, when a more specific dialogue does not take priority.", "A day-of-month key without a year suffix applies only in the first year. The _* suffix can make a date key apply every year. Check related entries before changing the trigger.")
        else:
            metadata = _example(trigger, "Archived dialogue entry", "The original trigger is preserved. Check the source and dialogue documentation for its specific context.", "Some entries are referenced by another dialogue or event. Keep related entries together when adapting them, and review character names, portrait commands, and any branching commands.")
        entries.append({**metadata, "text": text})
    return entries


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise WikiDialogueError("Dialogue loading was cancelled.")


def _validate_url(url):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "raw.githubusercontent.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not parsed.query
            and url in {template["download_url"] for template in DIALOGUE_TEMPLATES.values()}
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise WikiDialogueError("Dialogue must use a verified, pinned reference URL.")


class _DialogueRedirectHandler(HTTPRedirectHandler):
    def __init__(self, cancelled=None):
        super().__init__()
        self.cancelled = cancelled
        self.redirects = 0

    def redirect_request(self, request, response, code, message, headers, new_url):
        _check_cancelled(self.cancelled)
        _validate_url(new_url)
        self.redirects += 1
        if self.redirects > 3:
            raise WikiDialogueError("The dialogue source redirected too many times. Try again later.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _download_json(url, *, cancelled=None):
    _check_cancelled(cancelled)
    _validate_url(url)
    opener = build_opener(_DialogueRedirectHandler(cancelled))
    request = Request(url, headers={"User-Agent": "Pixelheart/0.1 (on-demand dialogue reference)", "Accept": "application/json"})
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            _validate_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError as exc:
                    raise WikiDialogueError("The source returned an invalid dialogue length.") from exc
                if length < 0 or length > MAX_DOWNLOAD_BYTES:
                    raise WikiDialogueError("The dialogue reference exceeds the download limit.")
            payload = bytearray()
            read = getattr(response, "read1", response.read)
            while True:
                _check_cancelled(cancelled)
                if time.monotonic() > deadline:
                    raise WikiDialogueError("The dialogue download timed out. Please try again.")
                chunk = read(min(64 * 1024, MAX_DOWNLOAD_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_DOWNLOAD_BYTES:
                    raise WikiDialogueError("The dialogue reference exceeds the download limit.")
            _check_cancelled(cancelled)
            return bytes(payload)
    except WikiDialogueError:
        raise
    except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError) as exc:
        raise WikiDialogueError("The dialogue source could not be reached. Check your connection and try again.") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _validate_payload(payload, template):
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise WikiDialogueError("The dialogue reference exceeds the download limit.")
    if hashlib.sha256(payload).hexdigest() != template["sha256"]:
        raise WikiDialogueError("The dialogue reference differs from the verified version. Update Pixelheart before using it.")
    try:
        data = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise WikiDialogueError("The source did not return valid dialogue JSON.") from exc
    if not isinstance(data, dict) or not data or len(data) > MAX_ENTRIES:
        raise WikiDialogueError("The source returned an invalid dialogue collection.")
    for key, value in data.items():
        if (not isinstance(key, str) or not _IMPORT_TRIGGER.fullmatch(key)
                or not isinstance(value, str) or not value.strip()
                or len(value) > MAX_TEXT_LENGTH or "{{" in value
                or any(ord(char) < 32 and char not in "\n\r\t" for char in key + value)
                or any(0xD800 <= ord(char) <= 0xDFFF for char in key + value)):
            raise WikiDialogueError("The archived file contains a dialogue entry that does not fit the editor's import format.")
    return data


def _cache_directory(cache_root, template_id):
    try:
        supplied = Path(cache_root).expanduser().absolute()
        resolved = supplied.resolve()
        for candidate in (supplied, resolved):
            if candidate == _SOURCE_ROOT or _SOURCE_ROOT in candidate.parents:
                raise WikiDialogueError("Store dialogue references in an OS cache outside the Pixelheart repository.")
            if any((ancestor / ".git").exists() for ancestor in (candidate, *candidate.parents)):
                raise WikiDialogueError("Store dialogue references outside Git repositories.")
        destination = resolved / template_id
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise WikiDialogueError("The dialogue cache folder must be a regular directory.")
        if (destination / ".git").exists():
            raise WikiDialogueError("Store dialogue references outside Git repositories.")
        destination.mkdir(parents=True, exist_ok=True)
        return destination
    except WikiDialogueError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise WikiDialogueError("The dialogue cache is unavailable. Choose a writable OS cache directory.") from exc


def _cached_data(path, template):
    if path.is_symlink():
        raise WikiDialogueError("The dialogue cache must not contain linked files.")
    if not path.exists():
        return None
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise WikiDialogueError("The cached dialogue must be a regular file.")
    if details.st_size > MAX_DOWNLOAD_BYTES:
        return None
    with path.open("rb") as source:
        payload = source.read(MAX_DOWNLOAD_BYTES + 1)
    try:
        return _validate_payload(payload, template)
    except WikiDialogueError:
        return None


def download_dialogue_examples(template_id, cache_root, *, cancelled=None):
    """Return every archived entry in source order with teaching notes.

    The ``examples`` result key and function name remain for API compatibility.
    This function never writes to or modifies a project.
    No request occurs at import, or when a verified cache already exists.
    ``cancelled`` is an optional zero-argument callable checked during I/O.
    """
    _check_cancelled(cancelled)
    if not isinstance(template_id, str) or template_id not in DIALOGUE_TEMPLATES:
        raise WikiDialogueError("Choose an available dialogue reference character.")
    template = DIALOGUE_TEMPLATES[template_id]
    temporary = None
    try:
        directory = _cache_directory(cache_root, template_id)
        path = directory / "dialogue.json"
        data = _cached_data(path, template)
        _check_cancelled(cancelled)
        if data is None:
            payload = _download_json(template["download_url"], cancelled=cancelled)
            data = _validate_payload(payload, template)
            _check_cancelled(cancelled)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            _check_cancelled(cancelled)
            os.replace(temporary, path)
        _check_cancelled(cancelled)
        return {
            "id": template_id,
            **{key: template[key] for key in ("name", "source_url", "source_name", "attribution")},
            "examples": _annotated_entries(data, template),
        }
    except WikiDialogueError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise WikiDialogueError("The dialogue reference could not be saved in the local cache. Check that it is writable.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
