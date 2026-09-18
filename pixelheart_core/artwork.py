"""Inspect and prepare user-supplied PNG sheets locally, without generating art.

Resizing always preserves the supplied frame arrangement and aspect ratio.
Prepared copies are separate files; correctly sized, unprocessed sheets retain
their exact original bytes, including their palette and PNG metadata.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
import warnings
from os import PathLike
from pathlib import Path

from PIL import Image, UnidentifiedImageError


MAX_ARTWORK_BYTES = 5 * 1024 * 1024
MAX_ARTWORK_PIXELS = 4_194_304
PIXELATION_FACTORS = (1, 2, 4, 8)


class ArtworkValidationError(ValueError):
    """An image or preparation option needs correction before it can be used."""


def _path(value, label):
    try:
        path = Path(value)
        if "\x00" in str(path):
            raise ValueError("Paths cannot contain null characters")
        return path
    except (TypeError, ValueError) as exc:
        raise ArtworkValidationError(f"Choose a valid local {label} file path.") from exc


def _read_png(path):
    """Bound the input, verify its container, and fully decode a static PNG."""
    try:
        details = path.stat()
        if not stat.S_ISREG(details.st_mode):
            raise ArtworkValidationError("Choose a regular local PNG file.")
        if details.st_size > MAX_ARTWORK_BYTES:
            raise ArtworkValidationError("Artwork must be 5 MB or smaller.")
        with path.open("rb") as source:
            payload = source.read(MAX_ARTWORK_BYTES + 1)
        if len(payload) > MAX_ARTWORK_BYTES:
            raise ArtworkValidationError("Artwork must be 5 MB or smaller.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != "PNG":
                    raise ArtworkValidationError("Only PNG artwork is supported. Export your sheet as a static PNG.")
                width, height = image.size
                if width * height > MAX_ARTWORK_PIXELS:
                    raise ArtworkValidationError("Artwork exceeds the 4,194,304-pixel limit. Choose a smaller PNG sheet.")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise ArtworkValidationError("Use a static PNG sheet, not an animated PNG.")
                image.verify()
            # Container verification alone does not decode every pixel.
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                decoded = image.copy()
        return payload, decoded
    except ArtworkValidationError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ArtworkValidationError("Artwork exceeds the safe image size. Choose a smaller PNG sheet.") from exc
    except (OSError, ValueError, TypeError, SyntaxError, UnidentifiedImageError) as exc:
        raise ArtworkValidationError(f"The PNG artwork cannot be read. Choose a complete, uncorrupted local PNG file: {exc}") from exc


def inspect_artwork(path: str | PathLike[str]) -> dict:
    """Return ``size`` in bytes, ``width``, ``height``, and ``format`` for a PNG.

    Inspection accepts any sheet dimensions within the safety limits. Frame
    layout and export dimensions are checked separately by preparation/export.
    Invalid, animated, unreadable, or oversized files raise ArtworkValidationError.
    """
    payload, image = _read_png(_path(path, "artwork"))
    try:
        return {"size": len(payload), "width": image.width, "height": image.height, "format": "PNG"}
    finally:
        image.close()


def _target_size(image, kind, target_height, romanceable):
    if kind == "portrait":
        width, frame_width, frame_height, minimum = 128, 64, 64, 192
    else:
        width, frame_width, frame_height, minimum = 64, 16, 32, 416 if romanceable else 128
    expected = f"{width} px wide and at least {minimum} px high, in rows of {frame_height} px"
    if target_height is not None and (type(target_height) is not int or target_height < minimum or target_height % frame_height):
        raise ArtworkValidationError(f"Choose a {kind} target {expected}.")
    if image.width < width:
        raise ArtworkValidationError(
            f"The {kind} sheet is {image.width}×{image.height}. Upscaling is not supported; supply a sheet {expected} or a larger proportional version."
        )
    scaled_height, remainder = divmod(image.height * width, image.width)
    if remainder or scaled_height < minimum or scaled_height % frame_height:
        raise ArtworkValidationError(
            f"The {kind} sheet is {image.width}×{image.height}. Its aspect ratio cannot fit a sheet {expected}. "
            "Supply all frames in the correct rows and columns; preparation cannot stretch, crop, or add frames."
        )
    if target_height is not None and target_height != scaled_height:
        raise ArtworkValidationError(
            f"The {image.width}×{image.height} source scales proportionally to {width}×{scaled_height}, "
            f"not {width}×{target_height}. Choose {scaled_height} px for the target height or supply a matching sheet."
        )
    columns = width // frame_width
    if image.width % columns:
        raise ArtworkValidationError(
            f"The {kind} source must contain {columns} complete columns with whole-pixel frame boundaries. "
            f"Choose a source width divisible by {columns}."
        )
    return (width, scaled_height), (frame_width, frame_height)


def _save_copy(destination, payload):
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pixelheart-", suffix=".png", delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
        # Replace only after encoding succeeds, so an existing prepared copy
        # survives validation, encoding, or write failures.
        os.replace(temporary, destination)
    except OSError as exc:
        raise ArtworkValidationError(f"The prepared PNG could not be saved. Choose a writable destination: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_artwork(
    source: str | PathLike[str],
    destination: str | PathLike[str],
    kind: str,
    *,
    target_height: int | None = None,
    pixelate: int = 1,
    romanceable: bool = False,
) -> Path:
    """Save a separate, export-sized portrait or sprite PNG and return its path.

    ``kind`` is ``portrait`` or ``sprite``. The source must uniformly downsize
    to 128-pixel-wide portraits (64×64 frames) or 64-pixel-wide sprites (16×32
    frames). Height defaults to the exact proportional result; an explicit
    height must match it. Romanceable sprites require at least 416 px in height.
    ``pixelate`` is 1 (off), 2, 4, or 8, measured in output pixels per grid cell.
    Pixelation averages pixels within each frame, then reconstructs with nearest
    neighbor; simple downsizing uses nearest neighbor to preserve pixel edges.
    Neither operation creates expressions or animation frames.
    """
    if kind not in ("portrait", "sprite"):
        raise ArtworkValidationError("Choose portrait or sprite artwork.")
    if type(pixelate) is not int or pixelate not in PIXELATION_FACTORS:
        raise ArtworkValidationError("Choose a pixelation factor of 1 (off), 2, 4, or 8.")
    if type(romanceable) is not bool:
        raise ArtworkValidationError("Romanceable must be true or false.")
    source_path, destination_path = _path(source, "source"), _path(destination, "destination")
    try:
        same_file = source_path.resolve() == destination_path.resolve()
        if not same_file and source_path.exists() and destination_path.exists():
            same_file = source_path.samefile(destination_path)
        if same_file:
            raise ArtworkValidationError("Save the prepared PNG to a separate file so the original artwork is preserved.")
    except OSError as exc:
        raise ArtworkValidationError(f"Artwork paths cannot be accessed. Choose readable and writable local files: {exc}") from exc
    payload, image = _read_png(source_path)
    try:
        size, frame_size = _target_size(image, kind, target_height, romanceable)
        if any(dimension % pixelate for dimension in frame_size):
            raise ArtworkValidationError("Choose a pixelation factor that divides both frame dimensions evenly.")
        if image.size != size or pixelate != 1:
            # Every frame boundary is aligned with the scaling grid. NEAREST
            # does not sample adjacent frames or introduce blended edge pixels.
            prepared = image.resize(size, Image.Resampling.NEAREST)
            try:
                if pixelate != 1:
                    rgba = prepared.convert("RGBA")
                    prepared.close()
                    prepared = rgba
                    frame_width, frame_height = frame_size
                    for top in range(0, size[1], frame_height):
                        for left in range(0, size[0], frame_width):
                            box = (left, top, left + frame_width, top + frame_height)
                            with prepared.crop(box) as frame:
                                with frame.resize((frame_width // pixelate, frame_height // pixelate), Image.Resampling.BOX) as grid:
                                    with grid.resize(frame_size, Image.Resampling.NEAREST) as pixels:
                                        prepared.paste(pixels, (left, top))
                buffer = io.BytesIO()
                prepared.save(buffer, format="PNG")
                payload = buffer.getvalue()
            finally:
                prepared.close()
            if len(payload) > MAX_ARTWORK_BYTES:
                raise ArtworkValidationError("The prepared PNG exceeds 5 MB. Choose a smaller source sheet or stronger pixelation.")
        _save_copy(destination_path, payload)
        return destination_path
    finally:
        image.close()
