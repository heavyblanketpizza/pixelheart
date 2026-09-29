"""OpenRaster working files for painted sheets, and the project's layer store.

The painter keeps layers in an OpenRaster (``.ora``) archive: a ZIP holding one
PNG per layer and a ``stack.xml`` listing them top first. Krita, GIMP, MyPaint
and Pinta can open these files too. Reference layers carry a Pixelheart flag.

Layer files live at ``artwork/layers/<sha256 of the flattened PNG>.ora``. A
sheet reopens with its layers only while its PNG is byte-for-byte the one the
painter saved; a sheet changed elsewhere opens as a single layer. Layer files
are working copies and never go into an exported pack.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, UnidentifiedImageError

from .pixel_document import MAX_LAYERS, Layer, PixelDocument, PixelError
from .pixel_sheets import KINDS, MAX_CANVAS_PIXELS, SheetError, open_png, sheet_spec


LAYER_FOLDER = "artwork/layers"
NAMESPACE = "urn:pixelheart:layers:1"
MIMETYPE = b"image/openraster"
MAX_ORA_BYTES = 128 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_STACK_BYTES = 1024 * 1024
MAX_ENTRIES = 256
MAX_COPY_SCAN = 20_000
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)

ET.register_namespace("pixelheart", NAMESPACE)


class LayerFileError(ValueError):
    """A layer file is unreadable, unsupported, or would leave the project."""


def _attribute(name):
    return f"{{{NAMESPACE}}}{name}"


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _entry(archive, name, payload, *, stored=False):
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, payload)


def write_ora(document):
    """Encode every layer, top first, with a merged image of the game sheet."""
    root = ET.Element("image", {"version": "0.0.5", "w": str(document.width), "h": str(document.height),
                                _attribute("frame-width"): str(document.frame_size[0]),
                                _attribute("frame-height"): str(document.frame_size[1])})
    stack = ET.SubElement(root, "stack")
    sources = []
    for index, layer in enumerate(document.layers):
        source = f"data/layer-{index}.png"
        sources.append((source, layer.image))
        attributes = {
            "name": layer.name, "src": source, "x": "0", "y": "0",
            "opacity": f"{layer.opacity / 100:.2f}",
            "visibility": "visible" if layer.visible else "hidden",
            "composite-op": "svg:src-over",
            "edit-locked": "true" if layer.locked else "false",
        }
        if layer.reference:
            attributes[_attribute("reference")] = "true"
        # OpenRaster lists the top layer first.
        stack.insert(0, ET.Element("layer", attributes))
    merged = document.flatten()
    thumbnail = merged.copy()
    thumbnail.thumbnail((256, 256), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _entry(archive, "mimetype", MIMETYPE, stored=True)
        _entry(archive, "stack.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
        for source, image in sources:
            _entry(archive, source, _png_bytes(image))
        _entry(archive, "mergedimage.png", _png_bytes(merged))
        _entry(archive, "Thumbnails/thumbnail.png", _png_bytes(thumbnail))
    return buffer.getvalue()


def _read_entry(archive, name, limit):
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise LayerFileError(f"The layer file is missing {name}.") from exc
    if info.file_size > limit:
        raise LayerFileError(f"{name} in the layer file is too large.")
    with archive.open(info) as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise LayerFileError(f"{name} in the layer file is too large.")
    return payload


def _integer(value, label, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise LayerFileError(f"The layer file has an invalid {label}.") from exc
    if not low <= number <= high:
        raise LayerFileError(f"The layer file has an invalid {label}.")
    return number


def _layer_image(payload, width, height, x, y):
    try:
        with Image.open(io.BytesIO(payload)) as source:
            if source.format != "PNG" or source.width * source.height > MAX_CANVAS_PIXELS:
                raise LayerFileError("A layer in the file is not a supported PNG.")
            pixels = source.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, LayerFileError):
            raise
        raise LayerFileError(f"A layer in the file could not be read: {exc}") from exc
    image = Image.new("RGBA", (width, height))
    image.paste(pixels, (x, y))
    return image


def read_ora(payload, *, frame=None):
    """Decode a bounded OpenRaster file into a document with no history."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise LayerFileError("The layer file is not an OpenRaster archive.") from exc
    with archive:
        if len(archive.infolist()) > MAX_ENTRIES:
            raise LayerFileError("The layer file has too many entries.")
        if _read_entry(archive, "mimetype", 64).strip() != MIMETYPE:
            raise LayerFileError("The layer file is not an OpenRaster archive.")
        text = _read_entry(archive, "stack.xml", MAX_STACK_BYTES)
        if b"<!DOCTYPE" in text or b"<!ENTITY" in text:
            raise LayerFileError("The layer file's stack uses XML features Pixelheart does not read.")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise LayerFileError("The layer file's stack could not be read.") from exc
        width = _integer(root.get("w"), "width", 1, MAX_CANVAS_PIXELS)
        height = _integer(root.get("h"), "height", 1, MAX_CANVAS_PIXELS)
        if width * height > MAX_CANVAS_PIXELS:
            raise LayerFileError("The layered sheet is larger than the painter supports.")
        if frame is None and root.get(_attribute("frame-width")):
            frame = (_integer(root.get(_attribute("frame-width")), "frame width", 1, width),
                     _integer(root.get(_attribute("frame-height")), "frame height", 1, height))
        stacks = root.findall("stack")
        if len(stacks) != 1:
            raise LayerFileError("The layer file needs exactly one layer stack.")
        entries = list(stacks[0])
        if any(entry.tag != "layer" for entry in entries):
            raise LayerFileError("Layer groups are not supported. Flatten the groups in your other editor first.")
        if not entries:
            raise LayerFileError("The layer file has no layers.")
        if len(entries) > MAX_LAYERS:
            raise LayerFileError(f"The layer file has more than {MAX_LAYERS} layers.")
        layers = []
        for index, entry in enumerate(reversed(entries), 1):
            try:
                opacity = float(entry.get("opacity", "1"))
            except ValueError as exc:
                raise LayerFileError("A layer has an invalid opacity.") from exc
            if not math.isfinite(opacity) or not 0 <= opacity <= 1:
                raise LayerFileError("A layer has an invalid opacity.")
            x = _integer(entry.get("x", "0"), "layer position", -MAX_CANVAS_PIXELS, MAX_CANVAS_PIXELS)
            y = _integer(entry.get("y", "0"), "layer position", -MAX_CANVAS_PIXELS, MAX_CANVAS_PIXELS)
            source = entry.get("src") or ""
            image = _layer_image(_read_entry(archive, source, MAX_ENTRY_BYTES), width, height, x, y)
            name = (entry.get("name") or "").strip()[:64] or f"Layer {index}"
            layers.append(Layer(f"layer-{index}", name, image,
                                visible=entry.get("visibility", "visible") != "hidden",
                                locked=entry.get("edit-locked") == "true",
                                opacity=round(opacity * 100),
                                reference=entry.get(_attribute("reference")) == "true"))
    try:
        return PixelDocument(width, height, layers, frame=frame)
    except PixelError as exc:
        raise LayerFileError(str(exc)) from exc


def layer_reference(png_bytes):
    """Project-relative path of the layer file for a flattened PNG."""
    return f"{LAYER_FOLDER}/{hashlib.sha256(png_bytes).hexdigest()}.ora"


def _contained(project_root, reference, *, create=False):
    """Resolve a layer path, refusing any folder that leads outside the project."""
    root = Path(project_root)
    resolved_root = root.resolve()
    path = root / reference
    try:
        ancestor = path.parent
        while not ancestor.exists() and ancestor != root:
            ancestor = ancestor.parent
        if not ancestor.resolve().is_relative_to(resolved_root):
            raise LayerFileError("Layer files must stay inside the project folder.")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.exists() and not path.parent.resolve().is_relative_to(resolved_root):
            raise LayerFileError("Layer files must stay inside the project folder.")
        if path.is_symlink():
            raise LayerFileError("Refusing to use a layer file through a symlink.")
    except OSError as exc:
        raise LayerFileError(f"The layer folder cannot be used: {exc}") from exc
    return path


def _write_atomic(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pixelheart-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise LayerFileError(f"The layer file could not be saved: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_layers(project_root, document, png_bytes):
    """Store the document's layers beside the flattened PNG they produce."""
    reference = layer_reference(png_bytes)
    _write_atomic(_contained(project_root, reference, create=True), write_ora(document))
    return reference


def load_layers(project_root, png_bytes, *, frame=None):
    """The saved layers for exactly this PNG, or None to paint it as one layer."""
    try:
        path = _contained(project_root, layer_reference(png_bytes))
        if not path.is_file() or path.stat().st_size > MAX_ORA_BYTES:
            return None
        document = read_ora(path.read_bytes(), frame=frame)
        with open_png(png_bytes) as expected:
            if document.flatten().tobytes() != expected.tobytes() or document.width != expected.width:
                return None
        return document
    except (LayerFileError, SheetError, OSError):
        return None


def copy_layer_files(source_root, destination_root):
    """Copy layer files for PNGs present in a copied project; returns how many."""
    source_folder = Path(source_root) / LAYER_FOLDER
    if not source_folder.is_dir():
        return 0
    copied = 0
    destination_root = Path(destination_root)
    for scanned, candidate in enumerate(destination_root.rglob("*.png")):
        if scanned >= MAX_COPY_SCAN:
            break
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            payload = candidate.read_bytes()
            reference = layer_reference(payload)
            source = _contained(source_root, reference)
            if not source.is_file() or source.stat().st_size > MAX_ORA_BYTES:
                continue
            destination = _contained(destination_root, reference, create=True)
            if not destination.exists():
                _write_atomic(destination, source.read_bytes())
                copied += 1
        except OSError as exc:
            raise LayerFileError(f"Layer files could not be copied: {exc}") from exc
    return copied


def open_painting(png_bytes, kind, *, project_root=None):
    """A document for a sheet and whether its saved layers came back.

    Frames always follow the sheet kind, so a layer file saved for another
    grid still lines up with this sheet.
    """
    with open_png(png_bytes) as image:
        spec = sheet_spec(kind, image.width, image.height)
        frame = spec.frame_width, spec.frame_height
        if project_root is not None:
            document = load_layers(project_root, png_bytes, frame=frame)
            if document is not None:
                return document, True
        return PixelDocument.from_image(image, frame=frame, name=KINDS[kind]), False


def blank_painting(kind, width, height):
    spec = sheet_spec(kind, width, height)
    return PixelDocument.blank(width, height, frame=(spec.frame_width, spec.frame_height))
