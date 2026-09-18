"""Fetch verified complete NPC sheets only when the user requests a template.

The repository contains source metadata, never game artwork. Callers provide an
OS cache directory outside any Git checkout; projects import their own copies.
"""

from __future__ import annotations

import hashlib
from http.client import HTTPException
import io
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import warnings

from PIL import Image


MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_194_304
NETWORK_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 30
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_ALLOWED_HOSTS = frozenset({"stardewvalleywiki.com", "media.githubusercontent.com"})

# Only source metadata belongs here, never the downloaded game artwork.
# Ordinary NPC page icons cannot substitute for complete sheet templates.
WIKI_TEMPLATES = {
    "abigail": {
        "name": "Abigail",
        "provider": "stardew-wiki",
        "source_name": "Stardew Valley Wiki",
        "source_url": "https://stardewvalleywiki.com/Modding:NPC_data",
        "portrait_url": "https://stardewvalleywiki.com/mediawiki/images/a/a9/Modding_-_creating_an_XNB_mod_-_example_portraits.png",
        "sprite_url": "https://stardewvalleywiki.com/mediawiki/images/7/71/Abigail-sprite-sheet.png",
        "portrait_size": (128, 320),
        "sprite_size": (64, 448),
        "portrait_sha256": "84f3a58828f835858696c48ca4862b041b108157dea54f254cf62e66fc432efe",
        "sprite_sha256": "48de36711555f6532938f50d0fab66fe3bdebbc9d7ef612adb3bbd8a26a31cd0",
    },
    "elliott": {
        "name": "Elliott",
        "provider": "stardew-data",
        "source_name": "stardew-data (GitHub)",
        "source_url": "https://github.com/juliaramosguedes/stardew-data/tree/4e0d98119afefd766f15ee77a529db4eb71fa240",
        "portrait_url": "https://media.githubusercontent.com/media/juliaramosguedes/stardew-data/4e0d98119afefd766f15ee77a529db4eb71fa240/sprites/portraits/elliott.png",
        "sprite_url": "https://media.githubusercontent.com/media/juliaramosguedes/stardew-data/4e0d98119afefd766f15ee77a529db4eb71fa240/sprites/characters/elliott.png",
        "portrait_size": (128, 320),
        "sprite_size": (64, 416),
        "portrait_sha256": "d48a2857535c78ff8388c2a80b52a5245a6c175b25979bee5f36756637ccc391",
        "sprite_sha256": "149dfa7a6e1a6aa5f12657eb52ca306d9280781f69d40b084f0262bc2b1eded0",
    },
}


class WikiArtworkError(ValueError):
    """A reference template could not be loaded safely or completely."""


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise WikiArtworkError("Template loading was cancelled.")


def _validate_url(url):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in _ALLOWED_HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not parsed.query
            and url in {
                template[f"{kind}_url"]
                for template in WIKI_TEMPLATES.values()
                for kind in ("portrait", "sprite")
            }
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise WikiArtworkError("The template download must use a verified image URL from the reference catalog.")


class _WikiRedirectHandler(HTTPRedirectHandler):
    def __init__(self, cancelled=None):
        super().__init__()
        self.cancelled = cancelled
        self.redirects = 0

    def redirect_request(self, request, response, code, message, headers, new_url):
        _check_cancelled(self.cancelled)
        _validate_url(new_url)
        self.redirects += 1
        if self.redirects > 3:
            raise WikiArtworkError("The source redirected this template too many times. Try again later.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _download_png(url, *, cancelled=None):
    _check_cancelled(cancelled)
    _validate_url(url)
    opener = build_opener(_WikiRedirectHandler(cancelled))
    request = Request(url, headers={"User-Agent": "Pixelheart/0.1 (on-demand artwork reference)", "Accept": "image/png"})
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            _validate_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError as exc:
                    raise WikiArtworkError("The source returned an invalid image length.") from exc
                if length < 0 or length > MAX_DOWNLOAD_BYTES:
                    raise WikiArtworkError("The reference template exceeds the 5 MB download limit.")
            payload = bytearray()
            # HTTPResponse.read1 returns available bytes without waiting to fill
            # a large buffer, so cancellation/deadline checks run between reads.
            read = getattr(response, "read1", response.read)
            while True:
                _check_cancelled(cancelled)
                if time.monotonic() > deadline:
                    raise WikiArtworkError("The reference download timed out. Please try again.")
                chunk = read(min(64 * 1024, MAX_DOWNLOAD_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_DOWNLOAD_BYTES:
                    raise WikiArtworkError("The reference template exceeds the 5 MB download limit.")
            _check_cancelled(cancelled)
            return bytes(payload)
    except WikiArtworkError:
        raise
    except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError) as exc:
        raise WikiArtworkError("The artwork source could not be reached. Check your connection and try again.") from exc


def _validate_png(payload, template, kind):
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise WikiArtworkError("The reference template exceeds the 5 MB image limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise WikiArtworkError("The source did not return a PNG sheet. Please try again later.")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise WikiArtworkError("The reference template exceeds the safe image size.")
                if image.size != tuple(template[f"{kind}_size"]):
                    raise WikiArtworkError("The image is not the verified complete template sheet.")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise WikiArtworkError("The reference template must be a static PNG sheet.")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
    except WikiArtworkError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise WikiArtworkError("The source returned an incomplete or invalid PNG sheet. Please try again later.") from exc
    if hashlib.sha256(payload).hexdigest() != template[f"{kind}_sha256"]:
        raise WikiArtworkError("This reference sheet has changed since it was verified. Update Pixelheart before using this template.")


def _cache_directory(cache_root, template_id):
    try:
        supplied = Path(cache_root).expanduser().absolute()
        resolved = supplied.resolve()
        for candidate in (supplied, resolved):
            if candidate == _SOURCE_ROOT or _SOURCE_ROOT in candidate.parents:
                raise WikiArtworkError("Store reference templates in an OS cache directory outside the Pixelheart repository.")
            if any((ancestor / ".git").exists() for ancestor in (candidate, *candidate.parents)):
                raise WikiArtworkError("Store reference templates outside Git repositories.")
        destination = resolved / template_id
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise WikiArtworkError("The template cache folder must be a regular directory.")
        if (destination / ".git").exists():
            raise WikiArtworkError("Store reference templates outside Git repositories.")
        destination.mkdir(parents=True, exist_ok=True)
        return destination
    except WikiArtworkError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise WikiArtworkError("The template cache is unavailable. Choose a writable OS cache directory.") from exc


def _cached_png(path, template, kind):
    if path.is_symlink():
        raise WikiArtworkError("The template cache must not contain linked image files.")
    if not path.exists():
        return None
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise WikiArtworkError("The template cache image must be a regular file.")
    if details.st_size > MAX_DOWNLOAD_BYTES:
        return None
    with path.open("rb") as source:
        payload = source.read(MAX_DOWNLOAD_BYTES + 1)
    try:
        _validate_png(payload, template, kind)
    except WikiArtworkError:
        return None
    return payload


def download_npc_template(npc_id, cache_root, *, cancelled=None):
    """Return validated cached portrait/sprite Paths and source attribution.

    ``cancelled`` is an optional zero-argument callable. No request is made at
    import/startup, or when both validated files are already cached. Downloads
    are verified before either cache file is replaced; this function never
    writes to the current project or changes character data.
    """
    _check_cancelled(cancelled)
    if not isinstance(npc_id, str) or npc_id not in WIKI_TEMPLATES:
        raise WikiArtworkError("Choose an available vanilla NPC template.")
    template = WIKI_TEMPLATES[npc_id]
    pending = []
    try:
        directory = _cache_directory(cache_root, npc_id)
        paths = {kind: directory / f"{kind}.png" for kind in ("portrait", "sprite")}
        for kind, path in paths.items():
            _check_cancelled(cancelled)
            if _cached_png(path, template, kind) is not None:
                continue
            payload = _download_png(template[f"{kind}_url"], cancelled=cancelled)
            _validate_png(payload, template, kind)
            _check_cancelled(cancelled)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                pending.append((temporary, path))
                output.write(payload)
        _check_cancelled(cancelled)
        for temporary, path in pending:
            os.replace(temporary, path)
        return {
            "id": npc_id,
            "name": template["name"],
            **paths,
            "source_url": template["source_url"],
            "provider": template["provider"],
            "source_name": template["source_name"],
            "attribution": f"Artwork © ConcernedApe · source: {template['source_name']}",
        }
    except WikiArtworkError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise WikiArtworkError("The reference template could not be saved in the local cache. Check that it is writable.") from exc
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
