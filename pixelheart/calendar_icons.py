"""Calendar sprites read from an already connected local game asset source.

Only atlas coordinates are shipped. Game pixels stay in the user's library or
Content Patcher exports; this reader never downloads, copies, or exports them.
"""

from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect
from PySide6.QtGui import QImageReader, QPainter

from pixelheart_core.local_templates import LocalTemplateError, resolve_export_folder
from .game_import import game_import_settings


# LooseSprites/Cursors source rectangles, verified against a local game atlas.
SOURCE_RECTS = {
    "spring": (406, 441, 12, 8),
    "summer": (406, 449, 12, 8),
    "fall": (406, 457, 12, 8),
    "winter": (406, 465, 12, 8),
    "gift": (147, 412, 10, 11),
    "heart": (211, 428, 7, 6),
    "festival": (294, 392, 16, 16),  # Gold star, used as the festival marker.
}
MAX_ATLAS_BYTES = 8 * 1024 * 1024
MAX_ATLAS_PIXELS = 8 * 1024 * 1024


def _source_paths(settings):
    """Inspect known layouts of explicitly connected sources, without scanning."""
    roots = []
    exports = settings.value("localGame/contentPatcherExportFolder", "")
    if isinstance(exports, str) and exports:
        try:
            roots.append(resolve_export_folder(exports))
        except LocalTemplateError:
            pass
    for key in ("interiors/librarySource", "interiors/libraryFolder"):
        value = settings.value(key, "")
        if not isinstance(value, str) or not value:
            continue
        try:
            root = Path(value).expanduser()
            if key == "interiors/librarySource":
                root = root.parent
            roots.extend((root / "source", root))
        except (OSError, ValueError):
            continue
    paths = []
    for root in roots:
        try:
            root = root.resolve(strict=True)
            for name in ("LooseSprites_Cursors.png", "LooseSprites/Cursors.png"):
                path = root / name
                if path.is_file() and path.resolve(strict=True).is_relative_to(root) and path not in paths:
                    paths.append(path)
        except (OSError, ValueError, RuntimeError):
            continue
    return paths


def _read_atlas(path):
    try:
        if path.stat().st_size > MAX_ATLAS_BYTES:
            return None
        with path.open("rb") as stream:
            payload = stream.read(MAX_ATLAS_BYTES + 1)
        if len(payload) > MAX_ATLAS_BYTES or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        buffer = QBuffer()
        buffer.setData(QByteArray(payload))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"png")
        size = reader.size()
        if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_ATLAS_PIXELS:
            return None
        image = reader.read()
        return None if image.isNull() else image
    except (OSError, ValueError):
        return None


class CalendarIconStore:
    """Cache small native sprites, reloading when a source changes or vanishes."""

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else game_import_settings()
        self._signature = None
        self._images = {}
        self.reload()

    def reload(self):
        signature = []
        for path in _source_paths(self.settings):
            try:
                info = path.stat()
                signature.append((path, info.st_mtime_ns, info.st_size))
            except OSError:
                continue
        signature = tuple(signature)
        if signature == self._signature:
            return False
        self._signature = signature
        self._images.clear()
        for path, _, _ in signature:
            atlas = _read_atlas(path)
            if atlas is None:
                continue
            for kind, bounds in SOURCE_RECTS.items():
                rect = QRect(*bounds)
                if kind not in self._images and atlas.rect().contains(rect):
                    self._images[kind] = atlas.copy(rect)
            if len(self._images) == len(SOURCE_RECTS):
                break
        return True

    def image(self, kind):
        return self._images.get(kind)

    def paint(self, painter, kind, bounds):
        image = self.image(kind)
        if image is None:
            return False
        scale = min(bounds.width() // image.width(), bounds.height() // image.height())
        if scale < 1:
            return False
        width, height = image.width() * scale, image.height() * scale
        target = QRect(bounds.x() + (bounds.width() - width) // 2,
                       bounds.y() + (bounds.height() - height) // 2, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(target, image)
        painter.restore()
        return True
