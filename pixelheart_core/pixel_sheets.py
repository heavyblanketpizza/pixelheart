"""Frame geometry and limits for the sheets the pixel painter edits.

Portraits are two columns of square expressions, sprites four columns of
frames twice as tall as they are wide, and tilesheets 16 × 16 cells. Painting
keeps a sheet's width, so frame columns and tile numbers never shift; rows are
added or removed at the bottom only. Furniture is painted on 16 × 16 cells and
keeps its size exactly, because the game finds each frame by position.
"""
from __future__ import annotations

from dataclasses import dataclass
import io

from PIL import Image


MAX_CANVAS_PIXELS = 4_194_304
MAX_TILESHEET_SIDE = 2048
KINDS = {"portrait": "Portrait sheet", "sprite": "Sprite sheet", "tilesheet": "Tilesheet", "furniture": "Furniture"}
_MIN_ROWS = {"portrait": 3, "sprite": 4, "tilesheet": 1, "furniture": 1}
_FIXED_SIZE = {"furniture"}


class SheetError(ValueError):
    """A sheet cannot be painted or saved within Pixelheart's limits."""


@dataclass(frozen=True)
class SheetSpec:
    kind: str
    width: int
    frame_width: int
    frame_height: int
    regular: bool

    @property
    def label(self):
        return KINDS[self.kind]

    @property
    def columns(self):
        return self.width // self.frame_width

    def rows(self, height):
        return height // self.frame_height

    def frame_count(self, height):
        return self.columns * self.rows(height)

    def frame_box(self, index, height):
        if not 0 <= index < self.frame_count(height):
            raise SheetError("Choose a frame inside the sheet.")
        left = index % self.columns * self.frame_width
        top = index // self.columns * self.frame_height
        return left, top, left + self.frame_width, top + self.frame_height

    def frame_at(self, x, y, height):
        if not (0 <= x < self.width and 0 <= y < height):
            return None
        return y // self.frame_height * self.columns + x // self.frame_width

    def can_add_row(self, height):
        new_height = height + self.frame_height
        if not self.regular or self.kind in _FIXED_SIZE or self.width * new_height > MAX_CANVAS_PIXELS:
            return False
        return self.kind != "tilesheet" or new_height <= MAX_TILESHEET_SIDE

    def can_remove_row(self, height):
        return self.regular and self.kind not in _FIXED_SIZE and self.rows(height) > _MIN_ROWS[self.kind]


def _check_canvas(width, height):
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise SheetError("A sheet needs a positive width and height.")
    if width * height > MAX_CANVAS_PIXELS:
        raise SheetError("The painter edits sheets of up to 4,194,304 pixels, such as 2048 × 2048.")


def sheet_spec(kind, width, height):
    """Frame geometry for a sheet; irregular sizes are painted as one frame."""
    if kind not in KINDS:
        raise SheetError("Choose a portrait sheet, sprite sheet, tilesheet, or furniture.")
    _check_canvas(width, height)
    if kind in ("tilesheet", "furniture"):
        frame = (16, 16)
    elif kind == "portrait":
        frame = (width // 2, width // 2)
    else:
        frame = (width // 4, width // 2)
    regular = frame[0] > 0 and width % frame[0] == 0 and height % frame[1] == 0 and height >= frame[1]
    if not regular:
        frame = (width, height)
    return SheetSpec(kind, width, frame[0], frame[1], regular)


def new_sheet_size(kind, *, romanceable=False, columns=16, rows=16):
    """A blank sheet matching the export template for ``kind``."""
    if kind == "portrait":
        return 128, 192
    if kind == "sprite":
        return 64, 416 if romanceable else 128
    if kind == "tilesheet":
        size = columns * 16, rows * 16
        _check_canvas(*size)
        if max(size) > MAX_TILESHEET_SIDE:
            raise SheetError("Tilesheets can be up to 2048 pixels on each side.")
        return size
    raise SheetError("Choose a portrait sheet, sprite sheet, or tilesheet.")


def encode_png(image, *, max_bytes=None):
    """Encode an RGBA PNG, refusing results larger than the target accepts."""
    buffer = io.BytesIO()
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    rgba.save(buffer, format="PNG", optimize=True)
    payload = buffer.getvalue()
    if max_bytes is not None and len(payload) > max_bytes:
        limit = f"{max_bytes / (1024 * 1024):.0f} MB" if max_bytes >= 1024 * 1024 else f"{max_bytes} bytes"
        raise SheetError(f"The flattened PNG is larger than {limit}. Simplify noisy areas or use fewer colors.")
    return payload


def open_png(payload):
    """Decode a still PNG into RGBA pixels within the painter's limits."""
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                raise SheetError("Choose a PNG image.")
            if getattr(image, "n_frames", 1) != 1:
                raise SheetError("Choose a still PNG, not an animated one.")
            _check_canvas(*image.size)
            return image.convert("RGBA")
    except SheetError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SheetError(f"The PNG could not be read: {exc}") from exc
