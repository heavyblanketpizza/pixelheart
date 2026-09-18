"""Local object textures, externally cached wiki icons, and UI placeholders.

Content Patcher exports textures as flat PNGs. This module reads only explicit
asset names from the selected folder and caches copies on this computer. It
never downloads artwork, reads XNBs, launches the game, or modifies game files.
Verified vanilla wiki icons are a fallback when no local texture is available.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QImageReader, QPainter, QPen, QPixmap

from pixelheart_core.catalog import MAX_CATALOG_ITEMS, CatalogValidationError, validate_icon_metadata
from pixelheart_core.wiki_items import WikiItemError, cached_item_icon, wiki_image_sources

MAX_TEXTURE_BYTES = 32 * 1024 * 1024
MAX_TEXTURE_PIXELS = 16 * 1024 * 1024
MAX_IMPORT_BYTES = 128 * 1024 * 1024
MAX_TEXTURES = 256
TILE_SIZE = 16


def default_wiki_cache_dir() -> Path:
    """Use a stable per-user cache independent of the working project."""
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    # GenericCacheLocation is available on supported platforms; the fallback
    # remains outside the current directory if the platform cannot provide it.
    return (Path(root) if root else Path.home() / ".cache") / "Pixelheart" / "wiki-items"


@lru_cache(maxsize=1)
def _vanilla_icons() -> dict[str, dict]:
    path = Path(__file__).resolve().parent.parent / "pixelheart_core" / "data" / "vanilla_items.json"
    return {record["id"]: record["icon"] for record in json.loads(path.read_text(encoding="utf-8"))["items"]}


class IconImportError(ValueError):
    """The selected folder cannot be read or the local cache cannot be saved."""


@dataclass(frozen=True)
class IconImportResult:
    imported_textures: int
    ready_items: int
    missing_textures: tuple[str, ...]
    warnings: tuple[str, ...]


def _metadata(record):
    try:
        return validate_icon_metadata(record.get("icon"))
    except (CatalogValidationError, AttributeError):
        return None


def export_filename(texture: str) -> str:
    """Portable Content Patcher filename for a validated relative asset key."""
    metadata = validate_icon_metadata({"texture": texture, "index": 0})
    return metadata["texture"].replace("/", "_") + ".png"


def texture_commands(records) -> list[str]:
    textures = {icon["texture"] for record in records if (icon := _metadata(record))}
    return [f'patch export "{texture}" image' for texture in sorted(textures, key=str.casefold)]


def _read_texture(path: Path) -> QImage:
    """Bound both compressed input and decoded pixels before invoking Qt."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_TEXTURE_BYTES:
            raise ValueError("must be a PNG of at most 32 MiB")
        with path.open("rb") as stream:
            raw = stream.read(MAX_TEXTURE_BYTES + 1)
        if len(raw) > MAX_TEXTURE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("must be a PNG of at most 32 MiB")
        buffer = QBuffer()
        buffer.setData(QByteArray(raw))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"png")
        size = reader.size()
        width, height = size.width(), size.height()
        if (width < TILE_SIZE or height < TILE_SIZE or width * height > MAX_TEXTURE_PIXELS
                or width % TILE_SIZE or height % TILE_SIZE):
            raise ValueError("needs a 16-pixel tile grid of at most 16 megapixels")
        image = reader.read()
        if image.isNull():
            raise ValueError("could not decode the PNG")
        return image
    except OSError as exc:
        raise ValueError(f"could not read PNG: {exc}") from exc


class ItemIconStore:
    """Shared per-computer texture cache; project files retain metadata only.

    Pass a temporary ``cache_dir`` in tests. Construction and fallback rendering
    never create files. Importing the same asset again replaces its cached art
    for every project using that asset key; it does not change gift assignments.
    """

    def __init__(self, cache_dir: str | Path | None = None, wiki_cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        ) / "item-icons"
        self.wiki_cache_dir = Path(wiki_cache_dir) if wiki_cache_dir is not None else default_wiki_cache_dir()
        try:
            self._wiki_sources = wiki_image_sources()
        except WikiItemError:
            # Optional artwork must not prevent opening a project.
            self._wiki_sources = {}
        self._icons: OrderedDict[tuple, QIcon] = OrderedDict()
        self._textures: OrderedDict[str, QImage | None] = OrderedDict()
        # Keep small crops (or a missing marker) across the availability pass
        # and visible-list rendering. A full catalog costs at most ~25 MiB in
        # pixel data, even when its items reference many large mod atlases.
        self._sprites: OrderedDict[tuple[str, int], QImage | None] = OrderedDict()
        self._wiki_sprites: OrderedDict[str, QImage | None] = OrderedDict()

    def _cache_path(self, texture: str) -> Path:
        return self.cache_dir / (hashlib.sha256(texture.casefold().encode("utf-8")).hexdigest() + ".png")

    def _texture(self, texture: str) -> QImage | None:
        if texture in self._textures:
            self._textures.move_to_end(texture)
            return self._textures[texture]
        try:
            path = self._cache_path(texture)
            image = _read_texture(path) if path.exists() else None
        except ValueError:
            image = None
        self._textures[texture] = image
        # Two base atlases fit comfortably; avoid holding arbitrary mod packs.
        while len(self._textures) > 4:
            self._textures.popitem(last=False)
        return image

    def _local_sprite(self, record) -> QImage | None:
        metadata = _metadata(record)
        if metadata is None:
            return None
        key = (metadata["texture"].casefold(), metadata["index"])
        if key in self._sprites:
            self._sprites.move_to_end(key)
            return self._sprites[key]
        image = self._texture(metadata["texture"])
        sprite = None
        if image is not None:
            columns = image.width() // TILE_SIZE
            x = metadata["index"] % columns * TILE_SIZE
            y = metadata["index"] // columns * TILE_SIZE
            if y + TILE_SIZE <= image.height():
                sprite = image.copy(x, y, TILE_SIZE, TILE_SIZE)
        self._sprites[key] = sprite
        while len(self._sprites) > MAX_CATALOG_ITEMS:
            self._sprites.popitem(last=False)
        return sprite

    def _wiki_eligible(self, record) -> bool:
        """Do not substitute vanilla art for a mod's replacement texture."""
        item_id = record.get("id")
        baseline = _vanilla_icons().get(item_id)
        if baseline is None or item_id not in self._wiki_sources:
            return False
        return record.get("icon") is None or _metadata(record) == baseline

    def _wiki_sprite(self, record) -> QImage | None:
        if not self._wiki_eligible(record):
            return None
        item_id = record["id"]
        if item_id in self._wiki_sprites:
            self._wiki_sprites.move_to_end(item_id)
            return self._wiki_sprites[item_id]
        image = None
        try:
            path = cached_item_icon(item_id, self.wiki_cache_dir)
            if path is not None:
                # The cache reader validates a bounded PNG first. A wiki image
                # is the complete item icon, never an atlas tile to crop.
                candidate = QImage(str(path))
                if not candidate.isNull() and 0 < candidate.width() <= 256 and 0 < candidate.height() <= 256:
                    image = candidate
        except WikiItemError:
            # An unavailable cache never prevents editing or local imports.
            pass
        self._wiki_sprites[item_id] = image
        while len(self._wiki_sprites) > MAX_CATALOG_ITEMS:
            self._wiki_sprites.popitem(last=False)
        return image

    def _sprite(self, record) -> QImage | None:
        local = self._local_sprite(record)
        return local if local is not None else self._wiki_sprite(record)

    def has_local_icon(self, record) -> bool:
        return self._local_sprite(record) is not None

    def has_wiki_icon(self, record) -> bool:
        return self._wiki_sprite(record) is not None

    def missing_wiki_ids(self, records) -> list[str]:
        """Eligible vanilla items which have neither local nor cached art."""
        return list(dict.fromkeys(
            record["id"] for record in records
            if self._wiki_eligible(record) and not self.has_local_icon(record) and not self.has_wiki_icon(record)
        ))

    def invalidate_wiki_icons(self, item_ids=None) -> None:
        """Refresh rendered icons after the background downloader saves files."""
        if item_ids is None:
            self._wiki_sprites.clear()
            self._icons.clear()
            return
        item_ids = set(item_ids)
        for item_id in item_ids:
            self._wiki_sprites.pop(item_id, None)
        for key in tuple(self._icons):
            if key[0] in item_ids:
                self._icons.pop(key, None)

    def has_icon(self, record) -> bool:
        return self._sprite(record) is not None

    def prepare(self, records) -> None:
        """Warm small crops grouped by atlas before rendering a whole catalog.

        Item names often interleave many mods' atlases. Grouping here avoids
        repeatedly decoding large atlases during the initial availability pass,
        while retaining only bounded 16-pixel crops after the atlas is evicted.
        """
        ordered = []
        for record in records:
            metadata = _metadata(record)
            if metadata is not None:
                ordered.append((metadata["texture"].casefold(), metadata["index"], record))
        for _, _, record in sorted(ordered, key=lambda item: item[:2]):
            self._sprite(record)

    def icon(self, record, size: int = 48) -> QIcon:
        size = min(128, max(16, int(size)))
        metadata = _metadata(record)
        key = (record.get("id", ""), record.get("name", ""), record.get("category", 0),
               metadata["texture"] if metadata else None,
               metadata["index"] if metadata else None, size)
        if key in self._icons:
            self._icons.move_to_end(key)
            return self._icons[key]
        sprite = self._sprite(record)
        if sprite is not None:
            pixmap = QPixmap.fromImage(sprite).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation,
            )
        else:
            pixmap = self._placeholder(record, size)
        result = QIcon(pixmap)
        self._icons[key] = result
        while len(self._icons) > 2048:
            self._icons.popitem(last=False)
        return result

    @staticmethod
    def _placeholder(record, size: int) -> QPixmap:
        """A text badge identifies an unavailable sprite; never imitation art."""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        digest = hashlib.sha256(str(record.get("category", 0)).encode()).digest()
        hue = (digest[0] * 360) // 256
        background = QColor.fromHsl(hue, 65, 228)
        foreground = QColor.fromHsl(hue, 60, 80)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(background.darker(110), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 7, 7)
        words = str(record.get("name") or record.get("id") or "?").split()
        initials = "".join(word[0] for word in words[:2]).upper()
        font = QFont()
        font.setPixelSize(max(11, size // 3))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(foreground)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()
        return pixmap

    def import_folder(self, folder: str | Path, records) -> IconImportResult:
        """Import matching CP-exported PNGs; missing/bad textures are reported.

        Only the selected directory is read, without recursive discovery.
        Symlinks pointing outside it and flattened filename collisions are
        skipped. Existing icons remain available when an export is missing.
        """
        try:
            root = Path(folder).resolve(strict=True)
            if not root.is_dir():
                raise OSError("Select the folder containing exported PNG textures.")
        except (OSError, TypeError, ValueError) as exc:
            raise IconImportError(f"Could not open the texture folder: {exc}") from exc
        records = list(records)
        textures = sorted({icon["texture"] for record in records if (icon := _metadata(record))}, key=str.casefold)
        filenames: dict[str, list[str]] = {}
        for texture in textures:
            filenames.setdefault(export_filename(texture).casefold(), []).append(texture)
        warnings = []
        missing = []
        imported = 0
        total_bytes = 0
        for texture in textures:
            filename = export_filename(texture)
            path = root / filename
            if len(filenames[filename.casefold()]) > 1:
                missing.append(texture)
                warnings.append(f"{texture}: the exported filename matches more than one asset; no image imported.")
                continue
            try:
                if path.resolve().parent != root:
                    raise ValueError("the texture points outside the selected folder")
                if not path.exists():
                    missing.append(texture)
                    continue
                file_bytes = path.stat().st_size
                if imported >= MAX_TEXTURES or total_bytes + file_bytes > MAX_IMPORT_BYTES:
                    raise ValueError("the import exceeds 256 textures or 128 MiB; import a smaller set")
                image = _read_texture(path)
                cache_path = self._cache_path(texture)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                # Save decoded pixels, discarding ancillary PNG metadata.
                descriptor, temporary = tempfile.mkstemp(suffix=".png", dir=self.cache_dir)
                os.close(descriptor)
                try:
                    if not image.save(temporary, "PNG"):
                        raise OSError("could not save decoded texture")
                    os.replace(temporary, cache_path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                total_bytes += file_bytes
                imported += 1
            except ValueError as exc:
                missing.append(texture)
                warnings.append(f"{filename}: {exc}.")
            except OSError as exc:
                self._textures.clear()
                self._icons.clear()
                self._sprites.clear()
                raise IconImportError(f"Could not cache item icons: {exc}") from exc
        self._textures.clear()
        self._icons.clear()
        self._sprites.clear()
        self.prepare(records)
        ready = sum(self.has_icon(record) for record in records)
        out_of_bounds = sum(
            bool((meta := _metadata(record)) and meta["texture"] not in missing and not self.has_icon(record))
            for record in records
        )
        if out_of_bounds:
            warnings.append(f"{out_of_bounds:,} item sprite indexes were outside their texture; placeholders remain.")
        return IconImportResult(imported, ready, tuple(missing), tuple(warnings))
