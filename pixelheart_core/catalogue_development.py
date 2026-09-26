"""Read-only, portable furniture comparison packs for the Home workshop."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path, PurePosixPath

from PIL import Image


class CataloguePackError(ValueError):
    """A local development pack cannot be previewed."""


def local_asset(root, reference):
    if not isinstance(reference, str) or not reference or "\\" in reference or ":" in reference:
        raise CataloguePackError("Use a relative asset path inside the development pack.")
    relative = PurePosixPath(reference)
    if relative.is_absolute() or any(p in ("", ".", "..") for p in reference.split("/")):
        raise CataloguePackError("Asset paths must stay inside the development pack.")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise CataloguePackError(f"Missing or external pack asset: {reference}")
    return path


def _json(path, limit=4 * 1024 * 1024):
    try:
        if path.stat().st_size > limit:
            raise CataloguePackError("This development manifest is too large.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CataloguePackError("A development manifest must contain an object.")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CataloguePackError(f"Cannot read the development pack: {exc}") from exc


def _text(value, label, limit=2048):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise CataloguePackError(f"{label} needs text of at most {limit} characters.")
    return value


@dataclass
class DevelopmentPack:
    path: Path
    data: dict
    actor: dict | None

    @property
    def root(self):
        return self.path.parent


def load_development_pack(path):
    path = Path(path).resolve()
    data = _json(path)
    if data.get("format") != "pixelheart-catalogue-development" or data.get("version") != 1:
        raise CataloguePackError("Choose a version 1 furniture catalogue development pack.")
    _text(data.get("title"), "Catalogue title", 256)
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 1024:
        raise CataloguePackError("A catalogue needs between 1 and 1024 pieces.")
    seen, images = set(), {}
    total_bytes = 0

    def image(reference, root=None):
        nonlocal total_bytes
        file = local_asset(root or path.parent, reference)
        if not file.is_relative_to(path.parent):
            raise CataloguePackError("An actor image escapes the development pack.")
        if file not in images:
            total_bytes += file.stat().st_size
            if len(images) >= 1024 or total_bytes > 128 * 1024 * 1024:
                raise CataloguePackError("The preview assets exceed the pack size limit.")
            try:
                with Image.open(file) as source:
                    if source.format != "PNG" or source.width * source.height > 4_194_304:
                        raise CataloguePackError("Preview assets must be PNG images of at most 4 megapixels.")
                    source.verify()
                    images[file] = source.size
            except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
                raise CataloguePackError(f"Invalid preview image: {reference}") from exc
        return images[file]

    def animation(frames, size):
        if not isinstance(frames, list) or not 1 <= len(frames) <= 64:
            raise CataloguePackError("A furniture animation needs 1–64 frames.")
        for frame in frames:
            if not isinstance(frame, dict) or image(frame.get("image")) != size:
                raise CataloguePackError("Animation frames must match the furniture preview dimensions.")
            duration = frame.get("duration_ms")
            if type(duration) is not int or not 16 <= duration <= 60_000:
                raise CataloguePackError("Animation frame duration must be 16–60000 milliseconds.")

    def lighting(lights):
        if not isinstance(lights, list) or len(lights) > 16:
            raise CataloguePackError("Use at most 16 light effects per furniture view.")
        for light in lights:
            if not isinstance(light, dict):
                raise CataloguePackError("A light effect must contain its shape, color and position.")
            point = light.get("offset")
            if (not isinstance(point, list) or len(point) != 2
                    or any(type(n) not in (int, float) or not -512 <= n <= 512 for n in point)):
                raise CataloguePackError("Light offsets need bounded pixel positions.")
            for field, low, high in (("radius", .01, 1024), ("intensity", 0, 1)):
                value = light.get(field)
                if type(value) not in (int, float) or not low <= value <= high:
                    raise CataloguePackError(f"Light {field} must be from {low} to {high}.")
            if not isinstance(light.get("color"), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", light["color"]):
                raise CataloguePackError("Light colors must use #RRGGBB.")
            if light.get("when") not in ("day", "night", "always"):
                raise CataloguePackError("Light timing must be day, night, or always.")
            if type(light.get("requires_power")) is not bool:
                raise CataloguePackError("Every light effect must declare whether it needs power.")
            if light.get("blend", "illuminate") not in ("illuminate", "overlay"):
                raise CataloguePackError("Light blending must be illuminate or overlay.")
            if light.get("mask_channel", "alpha") not in ("alpha", "luminance"):
                raise CataloguePackError("Light masks must use alpha or luminance.")
            if light.get("mask"):
                image(light["mask"])
            elif light.get("blend") == "overlay":
                raise CataloguePackError("A literal light overlay needs its image mask.")

    for item in items:
        if not isinstance(item, dict):
            raise CataloguePackError("Each catalogue piece must be an object.")
        identity = _text(item.get("id"), "Piece identity", 256)
        if identity in seen:
            raise CataloguePackError("Piece identities must be unique.")
        seen.add(identity)
        for key in ("name", "group", "slug"):
            _text(item.get(key), key, 256)
        for side_key in ("collection", "vanilla"):
            side = item.get(side_key)
            if not isinstance(side, dict):
                raise CataloguePackError("Every piece needs collection and vanilla previews.")
            for key in ("name", "id", "kind"):
                _text(side.get(key), key, 256)
            views = side.get("views")
            if not isinstance(views, list) or not 1 <= len(views) <= 32:
                raise CataloguePackError("Each comparison side needs 1–32 views.")
            for view in views:
                if not isinstance(view, dict):
                    raise CataloguePackError("A preview view must be an object.")
                _text(view.get("label"), "View label", 128)
                if type(view.get("rotation")) is not int or not 0 <= view["rotation"] <= 3:
                    raise CataloguePackError("A preview rotation must be from 0 to 3.")
                size = image(view.get("image"))
                if list(size) != [view.get("width"), view.get("height")]:
                    raise CataloguePackError("Preview dimensions do not match the supplied image.")
                footprint = view.get("footprint")
                if (not isinstance(footprint, list) or len(footprint) != 2
                        or any(type(n) is not int or not 1 <= n <= 16 for n in footprint)):
                    raise CataloguePackError("Footprints need width and height in tiles, from 1 to 16.")
                for field in ("seat", "sleep"):
                    point = view.get(field)
                    if point is not None and (not isinstance(point, list) or len(point) != 2
                            or any(type(n) not in (int, float) or not -256 <= n <= 512 for n in point)):
                        raise CataloguePackError(f"{field} needs a bounded pixel position.")
                if view.get("foreground") and image(view["foreground"]) != size:
                    raise CataloguePackError("The furniture foreground must match its preview dimensions.")
                if "animation_frames" in view:
                    animation(view["animation_frames"], size)
                states = view.get("states", {})
                if not isinstance(states, dict) or any(k not in ("day_off", "day_on", "night_off", "night_on") for k in states):
                    raise CataloguePackError("Furniture states must describe day/night and on/off combinations.")
                for state in states.values():
                    if not isinstance(state, dict) or image(state.get("image")) != size:
                        raise CataloguePackError("Lighting states must match the furniture preview dimensions.")
                    if "animation_frames" in state:
                        animation(state["animation_frames"], size)
                if "lights" in view:
                    lighting(view["lights"])
    backgrounds = data.get("backgrounds", [])
    if not isinstance(backgrounds, list) or len(backgrounds) > 16:
        raise CataloguePackError("Use at most 16 room backgrounds.")
    for background in backgrounds:
        if not isinstance(background, dict):
            raise CataloguePackError("A room background must be an object.")
        _text(background.get("name"), "Background name", 128)
        _text(background.get("id"), "Background identity", 128)
        image(background.get("image"))
    actor = None
    if data.get("actor"):
        actor_path = local_asset(path.parent, data["actor"])
        actor = _json(actor_path, 128 * 1024)
        if actor.get("format") != "pixelheart-preview-actor":
            raise CataloguePackError("This preview actor format is not supported.")
        for field in ("foot_anchor", "native_position_anchor", "native_sleeping_draw_offset"):
            point = actor.get(field)
            if point is not None and (not isinstance(point, list) or len(point) != 2
                    or any(type(n) not in (int, float) or not -256 <= n <= 256 for n in point)):
                raise CataloguePackError("Farmer anchors need bounded pixel positions.")
        offsets = actor.get("native_seated_draw_offset", {})
        if not isinstance(offsets, dict):
            raise CataloguePackError("Seated farmer offsets must map directions to pixel positions.")
        for direction, point in offsets.items():
            if (direction not in ("south", "east", "north", "west") or not isinstance(point, list)
                    or len(point) != 2 or any(type(n) not in (int, float) or not -256 <= n <= 256 for n in point)):
                raise CataloguePackError("Seated farmer offsets need bounded pixel positions.")
        frames = actor.get("frames")
        if not isinstance(frames, dict):
            raise CataloguePackError("The farmer preview needs walking frames.")
        for direction in ("south", "east", "north", "west"):
            entries = frames.get(direction)
            if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
                raise CataloguePackError("The farmer preview needs frames for all four walking directions.")
            for reference in entries:
                image(reference, actor_path.parent)
        for state in ("idle", "seated"):
            poses = actor.get(state, {})
            if not isinstance(poses, dict) or any(k not in frames for k in poses):
                raise CataloguePackError("Actor poses must use the four walking directions.")
            for reference in poses.values():
                image(reference, actor_path.parent)
        for state in ("sleeping", "bed_awake"):
            if actor.get(state):
                image(actor[state], actor_path.parent)
        actor = {**actor, "root": actor_path.parent}
    return DevelopmentPack(path, data, actor)


def discover_development_packs(roots):
    """Search bounded local project trees without following linked folders."""
    found, visited, remaining = [], set(), 4096
    for base in roots:
        base = Path(base).resolve()
        if not base.is_dir():
            continue
        for directory, folders, files in os.walk(base, followlinks=False):
            here = Path(directory)
            remaining -= 1
            if remaining < 0:
                return found
            folders[:] = sorted(f for f in folders if not f.startswith(".") and not (here / f).is_symlink())
            if len(here.relative_to(base).parts) >= 5:
                folders[:] = []
            if "pack.json" not in files:
                continue
            file = here / "pack.json"
            if file.is_symlink() or file in visited:
                continue
            visited.add(file)
            try:
                data = _json(file)
                if data.get("format") == "pixelheart-catalogue-development" and data.get("version") == 1:
                    found.append((file, _text(data.get("title"), "Catalogue title", 256)))
            except CataloguePackError:
                continue
    return found
