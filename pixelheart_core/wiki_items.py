"""On-demand item icons from Stardew Valley Wiki, cached outside repositories.

Only source links are shipped with Pixelheart. Downloaded PNGs remain in the
caller-provided computer cache and are never copied into a project or export.
This module has no Qt dependency and performs no work merely by importing it.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache
import hashlib
from http.client import HTTPException
import io
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
import warnings

from PIL import Image


MAX_DOWNLOAD_BYTES = 1024 * 1024
MAX_IMAGE_SIDE = 256
NETWORK_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 20
MAX_WORKERS = 4
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_SOURCES_PATH = Path(__file__).resolve().parent / "data" / "wiki_item_images.json"
_ITEM_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z", re.ASCII)


class WikiItemError(ValueError):
    """The requested icons or their local cache cannot be used."""


class _Cancelled(WikiItemError):
    pass


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise _Cancelled("Item icon loading was cancelled.")


def _validate_url(url):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "stardewvalleywiki.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith("/mediawiki/images/")
            and parsed.path.lower().endswith(".png")
            and not any(part in {".", ".."} for part in parsed.path.split("/"))
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise WikiItemError("Item icons must use verified Stardew Valley Wiki PNG links.")


@lru_cache(maxsize=4)
def _read_sources(path, modified_ns=None, size=None):
    # Modification metadata forms the cache key; app updates and tests can
    # replace a source list without leaving stale URLs in this process.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("items"), dict):
            raise ValueError("unknown metadata format")
        result = {}
        for item_id, record in data["items"].items():
            if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id) or not isinstance(record, dict):
                raise ValueError("invalid item source")
            _validate_url(record.get("url"))
            source_url = record.get("source_url")
            if not isinstance(source_url, str) or not source_url.startswith("https://stardewvalleywiki.com/"):
                raise ValueError("missing source attribution")
            result[item_id] = {"url": record["url"], "source_url": source_url}
        return result
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        raise WikiItemError("The bundled wiki item links could not be read. Reinstall or update Pixelheart.") from exc


def _sources():
    try:
        details = _SOURCES_PATH.stat()
    except OSError as exc:
        raise WikiItemError("The bundled wiki item links could not be read. Reinstall or update Pixelheart.") from exc
    return _read_sources(_SOURCES_PATH, details.st_mtime_ns, details.st_size)


def wiki_image_sources() -> dict[str, dict[str, str]]:
    """Return shipped item IDs and attribution links; never fetch metadata."""
    return {item_id: dict(record) for item_id, record in _sources().items()}


def _cache_directory(cache_root, *, create=False) -> Path:
    try:
        supplied = Path(cache_root).expanduser().absolute()
        resolved = supplied.resolve()
        for candidate in (supplied, resolved):
            if candidate == _SOURCE_ROOT or _SOURCE_ROOT in candidate.parents:
                raise WikiItemError("Store downloaded icons in the computer cache outside the Pixelheart repository.")
            if any((ancestor / ".git").exists() or (ancestor / ".git").is_symlink()
                   for ancestor in (candidate, *candidate.parents)):
                raise WikiItemError("Store downloaded icons outside Git repositories.")
        if supplied.is_symlink() or (resolved.exists() and not resolved.is_dir()):
            raise WikiItemError("The icon cache must be a regular directory, not a linked folder.")
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    except WikiItemError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise WikiItemError("The icon cache is unavailable. Choose a writable computer cache directory.") from exc


def _icon_path(directory, source):
    return directory / (hashlib.sha256(source["url"].encode("utf-8")).hexdigest() + ".png")


def _validate_png(payload):
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise WikiItemError("The wiki icon exceeds the 1 MiB image limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise WikiItemError("The wiki did not return a PNG icon.")
                if not (1 <= image.width <= MAX_IMAGE_SIDE and 1 <= image.height <= MAX_IMAGE_SIDE):
                    raise WikiItemError("The wiki icon exceeds the 256-pixel size limit.")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise WikiItemError("The wiki icon must be a static PNG.")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
    except WikiItemError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise WikiItemError("The wiki returned an incomplete or invalid PNG icon.") from exc


def _cached_png(path):
    if path.is_symlink():
        raise WikiItemError("The icon cache must not contain linked image files.")
    try:
        details = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(details.st_mode):
        raise WikiItemError("A cached icon must be a regular file.")
    if details.st_size > MAX_DOWNLOAD_BYTES:
        return False
    try:
        with path.open("rb") as stream:
            _validate_png(stream.read(MAX_DOWNLOAD_BYTES + 1))
    except WikiItemError:
        return False
    return True


def cached_item_icon(item_id, cache_root) -> Path | None:
    """Return an already validated cached PNG, without downloading or writing.

    Unknown items and invalid/missing image data return ``None``. An unsafe
    cache path raises ``WikiItemError`` so callers can fall back to a badge.
    """
    sources = _sources()
    if not isinstance(item_id, str) or item_id not in sources:
        return None
    directory = _cache_directory(cache_root)
    path = _icon_path(directory, sources[item_id])
    try:
        return path if _cached_png(path) else None
    except OSError as exc:
        raise WikiItemError("The cached item icon could not be read.") from exc


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
            raise WikiItemError("The wiki redirected this icon too many times.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _download_png(url, *, cancelled=None):
    _check_cancelled(cancelled)
    _validate_url(url)
    request = Request(url, headers={"User-Agent": "Pixelheart/0.1 (on-demand item icons)", "Accept": "image/png"})
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    try:
        with build_opener(_WikiRedirectHandler(cancelled)).open(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            _validate_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError as exc:
                    raise WikiItemError("The wiki returned an invalid image length.") from exc
                if length < 0 or length > MAX_DOWNLOAD_BYTES:
                    raise WikiItemError("The wiki icon exceeds the 1 MiB download limit.")
            payload = bytearray()
            read = getattr(response, "read1", response.read)
            while True:
                _check_cancelled(cancelled)
                if time.monotonic() > deadline:
                    raise WikiItemError("The wiki icon download timed out. Try again later.")
                chunk = read(min(64 * 1024, MAX_DOWNLOAD_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_DOWNLOAD_BYTES:
                    raise WikiItemError("The wiki icon exceeds the 1 MiB download limit.")
            _check_cancelled(cancelled)
            return bytes(payload)
    except WikiItemError:
        raise
    except (HTTPError, URLError, HTTPException, TimeoutError, OSError, ValueError) as exc:
        raise WikiItemError("This wiki icon could not be downloaded. Check your connection and retry.") from exc


def _load_item(item_id, source, cache_root, cancelled):
    _check_cancelled(cancelled)
    directory = _cache_directory(cache_root)
    path = _icon_path(directory, source)
    try:
        if _cached_png(path):
            return "cached", path
        payload = _download_png(source["url"], cancelled=cancelled)
        _validate_png(payload)
        _check_cancelled(cancelled)
        directory = _cache_directory(cache_root, create=True)
        # Recheck after network I/O before replacing any cache entry.
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise WikiItemError("A cached icon must be a regular file, not a link.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".icon-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            _check_cancelled(cancelled)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return "downloaded", path
    except WikiItemError:
        raise
    except OSError as exc:
        raise WikiItemError("This icon could not be saved in the computer cache.") from exc


def download_item_icons(item_ids, cache_root, *, cancelled=None, progress=None):
    """Cache known icons with bounded workers and return counts and failures.

    The result contains ``downloaded``, ``cached``, ``failed`` (ID-to-message),
    ``cancelled`` and ``total``. ``progress(item_id, path_or_none)`` runs once
    for every completed item, on this calling thread. Cancellation keeps icons
    already saved, skips remaining work, and never leaves partial image files.
    ``cache_root`` is a computer cache directory, never a project directory.
    """
    sources = wiki_image_sources()
    if isinstance(item_ids, (str, bytes)):
        raise WikiItemError("Choose item IDs from the available wiki icon list.")
    try:
        identifiers = list(item_ids)
    except TypeError as exc:
        raise WikiItemError("Choose item IDs from the available wiki icon list.") from exc
    if len(identifiers) > 25_000 or any(not isinstance(item_id, str) for item_id in identifiers):
        raise WikiItemError("Choose item IDs from the available wiki icon list.")
    identifiers = list(dict.fromkeys(identifiers))
    result = {"downloaded": 0, "cached": 0, "failed": {}, "cancelled": False, "total": len(identifiers)}
    if cancelled is not None and cancelled():
        result["cancelled"] = True
        return result
    _cache_directory(cache_root)
    pending_ids = iter(identifiers)
    futures = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="wiki-icons") as executor:
        def schedule():
            while len(futures) < MAX_WORKERS:
                if cancelled is not None and cancelled():
                    result["cancelled"] = True
                    return
                item_id = next(pending_ids, None)
                if item_id is None:
                    return
                if item_id not in sources:
                    result["failed"][item_id] = "No verified wiki icon is available for this item."
                    if progress is not None:
                        progress(item_id, None)
                    continue
                future = executor.submit(_load_item, item_id, sources[item_id], cache_root, cancelled)
                futures[future] = item_id

        schedule()
        while futures:
            finished, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
            if cancelled is not None and cancelled():
                result["cancelled"] = True
            for future in finished:
                item_id = futures.pop(future)
                try:
                    status, path = future.result()
                except _Cancelled:
                    result["cancelled"] = True
                    continue
                except WikiItemError as exc:
                    result["failed"][item_id] = str(exc)
                    path = None
                else:
                    result[status] += 1
                if progress is not None:
                    progress(item_id, path)
            if not result["cancelled"]:
                schedule()
    return result
