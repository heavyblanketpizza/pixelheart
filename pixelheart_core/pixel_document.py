"""Layered RGBA sheets with exact pixel edits and bounded undo/redo.

Layers are listed bottom first. Every change is one history step: pixel edits
store only the changed rectangle before and after, and layer changes store the
layer list, which shares unchanged images. History is strictly linear, so an
image shared between steps is always in the state the step expects.

Reference layers help while painting but never reach the flattened sheet, and
hidden layers are left out as well. Nothing here depends on Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from PIL import Image, ImageChops

from .pixel_raster import apply_mask, equal_color_mask
from .pixel_sheets import MAX_CANVAS_PIXELS


MAX_LAYERS = 32
MAX_HISTORY = 200
MAX_HISTORY_BYTES = 256 * 1024 * 1024
MAX_LAYER_NAME = 64
REFERENCE_OPACITY = 40
CLEAR = (0, 0, 0, 0)


class PixelError(ValueError):
    """A painting action cannot be applied, with a message for the painter."""


@dataclass(frozen=True)
class Layer:
    id: str
    name: str
    image: Image.Image = field(repr=False, compare=False)
    visible: bool = True
    locked: bool = False
    opacity: int = 100
    reference: bool = False


@dataclass(frozen=True)
class RegionClip:
    """Pixels copied from one box of one or more layers, keyed by layer ID."""
    width: int
    height: int
    images: dict


@dataclass(frozen=True)
class _State:
    width: int
    height: int
    layers: tuple
    active: int


@dataclass
class _PixelStep:
    label: str
    patches: list

    @property
    def size(self):
        return sum(before.width * before.height * 8 for _, _, before, _ in self.patches)


@dataclass
class _StackStep:
    label: str
    before: _State
    after: _State
    merge_key: object = None

    @property
    def size(self):
        before = {id(layer.image): layer.image for layer in self.before.layers}
        after = {id(layer.image): layer.image for layer in self.after.layers}
        changed = [image for key, image in {**before, **after}.items() if (key in before) != (key in after)]
        return sum(image.width * image.height * 4 for image in changed)


def _union(first, second):
    if first is None:
        return second
    if second is None:
        return first
    return min(first[0], second[0]), min(first[1], second[1]), max(first[2], second[2]), max(first[3], second[3])


def _clip_box(box, width, height):
    left, top, right, bottom = box
    clipped = max(0, left), max(0, top), min(width, right), min(height, bottom)
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def _composite_at(target, image, x, y):
    """Composite ``image`` over ``target`` at (x, y), clipped; return the box drawn."""
    box = _clip_box((x, y, x + image.width, y + image.height), target.width, target.height)
    if box is None:
        return None
    region = target.crop(box)
    source = image.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
    # Over a transparent pixel the result is the source pixel exactly, so a
    # lifted selection dropped back in place is byte-for-byte unchanged.
    empty = region.getchannel("A").point(lambda value: 255 if value == 0 else 0)
    blended = region.copy()
    blended.alpha_composite(source)
    blended.paste(source, (0, 0), empty)
    target.paste(blended, box[:2])
    return box


def _with_opacity(layer, box=None):
    image = layer.image if box is None else layer.image.crop(box)
    if layer.opacity >= 100:
        return image
    faded = image.copy()
    faded.putalpha(image.getchannel("A").point(lambda value: (value * layer.opacity + 50) // 100))
    return faded


class PixelDocument:
    def __init__(self, width, height, layers, *, frame=None, active=0):
        if type(width) is not int or type(height) is not int or width < 1 or height < 1:
            raise PixelError("A sheet needs a positive width and height.")
        if width * height > MAX_CANVAS_PIXELS:
            raise PixelError("The painter edits sheets of up to 4,194,304 pixels, such as 2048 × 2048.")
        layers = tuple(layers)
        if not layers or len(layers) > MAX_LAYERS:
            raise PixelError(f"A sheet needs between 1 and {MAX_LAYERS} layers.")
        if any(layer.image.mode != "RGBA" or layer.image.size != (width, height) for layer in layers):
            raise PixelError("Every layer must be an RGBA image the size of the sheet.")
        frame_width, frame_height = frame or (width, height)
        if not (0 < frame_width <= width and 0 < frame_height <= height):
            raise PixelError("Frames must fit inside the sheet.")
        self.width, self.height = width, height
        self.frame_size = frame_width, frame_height
        self._layers = layers
        self._active = max(0, min(int(active), len(layers) - 1))
        self._next_id = len(layers) + 1
        self._undo, self._redo = [], []
        self._history_bytes = 0
        self._saved = None
        self._edit = None
        self._float = None
        self.selection = None
        self._dirty = (0, 0, width, height)

    @classmethod
    def blank(cls, width, height, *, frame=None, name="Layer 1"):
        return cls(width, height, [Layer("layer-1", name, Image.new("RGBA", (width, height)))], frame=frame)

    @classmethod
    def from_image(cls, image, *, frame=None, name="Layer 1"):
        pixels = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
        return cls(pixels.width, pixels.height, [Layer("layer-1", name, pixels)], frame=frame)

    # State ---------------------------------------------------------------

    @property
    def layers(self):
        return self._layers

    @property
    def active_index(self):
        return self._active

    @property
    def active_layer(self):
        return self._layers[self._active]

    @property
    def editing(self):
        return self._edit is not None

    @property
    def floating(self):
        return self._float is not None

    @property
    def modified(self):
        top = self._undo[-1] if self._undo else None
        return top is not self._saved or self._float is not None

    @property
    def can_undo(self):
        return bool(self._undo) or self._edit is not None

    @property
    def can_redo(self):
        return bool(self._redo)

    @property
    def undo_label(self):
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self):
        return self._redo[-1].label if self._redo else None

    def mark_saved(self):
        self._saved = self._undo[-1] if self._undo else None

    def set_active(self, index):
        if not 0 <= index < len(self._layers):
            raise PixelError("Choose a layer in this sheet.")
        self._settle()
        self._active = index

    def take_dirty(self):
        dirty, self._dirty = self._dirty, None
        return dirty

    def _mark(self, box):
        if box is not None:
            self._dirty = _union(self._dirty, box)

    def _mark_all(self):
        self._dirty = (0, 0, self.width, self.height)

    def _index(self, layer_id):
        for index, layer in enumerate(self._layers):
            if layer.id == layer_id:
                return index
        raise PixelError("That layer is no longer in this sheet.")

    def _layer(self, layer_id):
        return self._layers[self._index(layer_id)]

    def _resolve(self, index):
        index = self._active if index is None else index
        if not 0 <= index < len(self._layers):
            raise PixelError("Choose a layer in this sheet.")
        return index

    # Frames --------------------------------------------------------------

    @property
    def frame_columns(self):
        return self.width // self.frame_size[0]

    @property
    def frame_count(self):
        return self.frame_columns * (self.height // self.frame_size[1])

    def frame_box(self, index):
        if not 0 <= index < self.frame_count:
            raise PixelError("Choose a frame inside the sheet.")
        width, height = self.frame_size
        left, top = index % self.frame_columns * width, index // self.frame_columns * height
        return left, top, left + width, top + height

    def frame_at(self, x, y):
        width, height = self.frame_size
        if not (0 <= x < self.frame_columns * width and 0 <= y < self.height // height * height):
            return None
        return y // height * self.frame_columns + x // width

    # Rendering -----------------------------------------------------------

    def composite(self, box=None, *, include_reference=True):
        """Visible layers, bottom first; references included unless excluded."""
        size = (self.width, self.height) if box is None else (box[2] - box[0], box[3] - box[1])
        result = Image.new("RGBA", size)
        for layer in self._layers:
            if layer.visible and (include_reference or not layer.reference):
                result.alpha_composite(_with_opacity(layer, box))
        return result

    def flatten(self):
        """The sheet the game receives: visible, non-reference layers only."""
        return self.composite(include_reference=False)

    def pixel_at(self, x, y):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        return self.composite((x, y, x + 1, y + 1), include_reference=False).getpixel((0, 0))

    def sheet_colors(self):
        """Colors in the flattened sheet, most used first, without transparency."""
        colors = self.flatten().getcolors(maxcolors=self.width * self.height) or []
        return [(color, count) for count, color in sorted(colors, key=lambda item: (-item[0], item[1])) if color[3]]

    # History -------------------------------------------------------------

    def _push(self, step):
        self._undo.append(step)
        self._history_bytes += step.size
        for dropped in self._redo:
            self._history_bytes -= dropped.size
        self._redo.clear()
        while len(self._undo) > MAX_HISTORY or (len(self._undo) > 1 and self._history_bytes > MAX_HISTORY_BYTES):
            self._history_bytes -= self._undo.pop(0).size

    def _state(self):
        return _State(self.width, self.height, self._layers, self._active)

    def _restore(self, state):
        self.width, self.height, self._layers = state.width, state.height, state.layers
        self._active = max(0, min(state.active, len(state.layers) - 1))
        if self.selection is not None:
            self.selection = _clip_box(self.selection, self.width, self.height)
        self._mark_all()

    def _apply(self, step, *, undo):
        if isinstance(step, _PixelStep):
            for layer_id, box, before, after in step.patches:
                self._layer(layer_id).image.paste(before if undo else after, box[:2])
                self._mark(box)
        else:
            self._restore(step.before if undo else step.after)

    def undo(self):
        """Undo the last step; an unfinished float or edit is cancelled first."""
        if self._float is not None:
            self.cancel_floating()
            return True
        if self._edit is not None:
            self.cancel_edit()
            return True
        if not self._undo:
            return False
        step = self._undo.pop()
        self._apply(step, undo=True)
        self._redo.append(step)
        return True

    def redo(self):
        self._settle()
        if not self._redo:
            return False
        step = self._redo.pop()
        self._apply(step, undo=False)
        self._undo.append(step)
        return True

    def _settle(self):
        """Finish a floating selection or open edit before another kind of change."""
        if self._float is not None:
            self.drop_floating()
        if self._edit is not None:
            self.commit_edit()

    # Pixel edits ---------------------------------------------------------

    def _check_paintable(self, layer):
        if not layer.visible:
            raise PixelError("Show this layer before painting on it.")
        if layer.locked:
            raise PixelError("Unlock this layer before painting on it.")

    def _open(self, label, layers):
        self._settle()
        self._edit = {"label": label, "before": {layer.id: layer.image.copy() for layer in layers}, "touched": None}

    def begin_edit(self, label):
        """Start one undo step of pixel changes on the active layer."""
        layer = self.active_layer
        self._check_paintable(layer)
        self._open(label, [layer])

    def _touch(self, box):
        if box is not None and self._edit is not None:
            self._edit["touched"] = _union(self._edit["touched"], box)
            self._mark(box)

    def paint(self, mask, color, *, layer_id=None):
        """Replace masked pixels on an edited layer, clipped to the selection."""
        if self._edit is None:
            raise PixelError("Start a change before painting.")
        layer_id = layer_id or self.active_layer.id
        if layer_id not in self._edit["before"]:
            raise PixelError("That layer is not part of this change.")
        box = apply_mask(self._layer(layer_id).image, mask, tuple(color), clip=self.selection)
        self._touch(box)
        return box

    def restore_edit(self):
        """Return edited layers to how they were when the edit began."""
        if self._edit is None or self._edit["touched"] is None:
            return
        touched = self._edit["touched"]
        for layer_id, before in self._edit["before"].items():
            self._layer(layer_id).image.paste(before.crop(touched), touched[:2])
        self._mark(touched)
        self._edit["touched"] = None

    def cancel_edit(self):
        self.restore_edit()
        self._edit = None

    def commit_edit(self):
        """Record the open edit as one step; unchanged edits leave no history."""
        if self._edit is None:
            return False
        edit, self._edit = self._edit, None
        touched = edit["touched"]
        patches = []
        if touched is not None:
            for layer_id, before in edit["before"].items():
                after = self._layer(layer_id).image
                box = ImageChops.difference(before.crop(touched), after.crop(touched)).getbbox(alpha_only=False)
                if box is not None:
                    box = touched[0] + box[0], touched[1] + box[1], touched[0] + box[2], touched[1] + box[3]
                    patches.append((layer_id, box, before.crop(box), after.crop(box)))
        if not patches:
            return False
        self._push(_PixelStep(edit["label"], patches))
        return True

    def _multi_targets(self, all_layers):
        """Layers a region command changes: the active one, or every unlocked artwork layer."""
        if not all_layers:
            layer = self.active_layer
            self._check_paintable(layer)
            return [layer]
        targets = [layer for layer in self._layers if not layer.locked and not layer.reference]
        if not targets:
            raise PixelError("Unlock a layer before changing it.")
        return targets

    def _region_edit(self, label, box, all_layers, change):
        box = _clip_box(box, self.width, self.height)
        if box is None:
            return False
        targets = self._multi_targets(all_layers)
        self._open(label, targets)
        for layer in targets:
            change(layer.image, box)
        self._touch(box)
        return self.commit_edit()

    def flip_region(self, box, *, horizontal=True, all_layers=False):
        direction = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
        return self._region_edit("Flip", box, all_layers,
                                 lambda image, region: image.paste(image.crop(region).transpose(direction), region[:2]))

    def clear_region(self, box, *, all_layers=False):
        return self._region_edit("Clear", box, all_layers, lambda image, region: image.paste(CLEAR, region))

    def copy_region(self, box, *, all_layers=False):
        box = _clip_box(box, self.width, self.height)
        if box is None:
            raise PixelError("Choose an area inside the sheet.")
        layers = [layer for layer in self._layers if not layer.reference] if all_layers else [self.active_layer]
        return RegionClip(box[2] - box[0], box[3] - box[1], {layer.id: layer.image.crop(box) for layer in layers})

    def paste_region(self, clip, x, y):
        """Replace a box on the copied layers that still exist and are unlocked."""
        present = {layer.id for layer in self._layers if not layer.locked and not layer.reference}
        targets = [self._layer(layer_id) for layer_id in clip.images if layer_id in present]
        if not targets:
            raise PixelError("The copied layers are locked or no longer in this sheet.")
        box = _clip_box((x, y, x + clip.width, y + clip.height), self.width, self.height)
        if box is None:
            return False
        self._open("Paste frame", targets)
        for layer in targets:
            layer.image.paste(clip.images[layer.id], (x, y))
        self._touch(box)
        return self.commit_edit()

    def replace_color(self, old, new, *, all_layers=False):
        """Swap one exact color for another; returns how many pixels matched."""
        old, new = tuple(old), tuple(new)
        if old == new:
            return 0
        targets = self._multi_targets(all_layers)
        self._open("Replace color", targets)
        matched = 0
        for layer in targets:
            mask = equal_color_mask(layer.image, old)
            if self.selection is not None:
                clipped = Image.new("L", mask.size)
                clipped.paste(mask.crop(self.selection), self.selection[:2])
                mask = clipped
            count = mask.histogram()[255]
            if count:
                matched += count
                layer.image.paste(new, (0, 0, self.width, self.height), mask)
                self._touch(mask.getbbox())
        self.commit_edit()
        return matched

    # Selection and floating pixels --------------------------------------

    def set_selection(self, box):
        if box is None:
            self.selection = None
            return
        x0, y0, x1, y1 = box
        self.selection = _clip_box((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), self.width, self.height)

    def select_all(self):
        self._settle()
        self.selection = (0, 0, self.width, self.height)

    def clear_selection(self):
        self._settle()
        self.selection = None

    def _start_float(self, label, pixels, x, y, origin):
        layer = self.active_layer
        self._check_paintable(layer)
        self._open(label, [layer])
        if origin is not None:
            layer.image.paste(CLEAR, origin)
            self._touch(origin)
        self._float = {"image": pixels, "x": x, "y": y, "base": layer.image.copy(), "layer": layer.id,
                       "origin": origin, "drawn": None}
        self._render_float()

    def _render_float(self):
        state = self._float
        layer = self._layer(state["layer"])
        previous = state["drawn"]
        if previous is not None:
            layer.image.paste(state["base"].crop(previous), previous[:2])
            self._touch(previous)
        state["drawn"] = _composite_at(layer.image, state["image"], state["x"], state["y"])
        self._touch(state["drawn"])
        self.selection = state["drawn"]

    def lift_selection(self):
        """Cut the selection from the active layer into movable floating pixels."""
        if self._float is not None:
            return
        if self.selection is None:
            raise PixelError("Select an area first.")
        box = self.selection
        pixels = self.active_layer.image.crop(box)
        self._start_float("Move", pixels, box[0], box[1], box)

    def paste(self, image, x, y):
        """Float an image over the active layer until it is dropped."""
        pixels = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
        if pixels.width * pixels.height > MAX_CANVAS_PIXELS:
            raise PixelError("The pasted image is too large for the painter.")
        self._start_float("Paste", pixels, x, y, None)

    def move_floating(self, dx, dy):
        if self._float is None or (dx == 0 and dy == 0):
            return
        self._float["x"] += dx
        self._float["y"] += dy
        self._render_float()

    def flip_floating(self, *, horizontal=True):
        if self._float is None:
            return
        direction = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
        self._float["image"] = self._float["image"].transpose(direction)
        self._render_float()

    def floating_image(self):
        return self._float["image"].copy() if self._float is not None else None

    def drop_floating(self):
        if self._float is None:
            return False
        state, self._float = self._float, None
        self.selection = state["drawn"]
        return self.commit_edit()

    def cancel_floating(self):
        if self._float is None:
            return
        state, self._float = self._float, None
        self.cancel_edit()
        self.selection = state["origin"]

    def copy_selection(self):
        if self._float is not None:
            return self.floating_image()
        if self.selection is None:
            return None
        return self.active_layer.image.crop(self.selection)

    def delete_selection(self):
        if self._float is not None:
            state = self._float
            self._float = None
            layer = self._layer(state["layer"])
            layer.image.paste(state["base"])
            self._touch(state["drawn"])
            self._edit["label"] = "Delete"
            self.selection = None
            return self.commit_edit()
        if self.selection is None:
            return False
        box = self.selection
        return self._region_edit("Delete", box, False, lambda image, region: image.paste(CLEAR, region))

    # Layers ----------------------------------------------------------------

    def _change(self, label, layers, *, active=None, height=None, merge_key=None):
        self._settle()
        before = self._state()
        layers = tuple(layers)
        active = self._active if active is None else active
        after = _State(self.width, self.height if height is None else height, layers, max(0, min(active, len(layers) - 1)))
        top = self._undo[-1] if self._undo else None
        if (merge_key is not None and isinstance(top, _StackStep) and top.merge_key == merge_key
                and top is not self._saved and not self._redo):
            self._history_bytes -= top.size
            top.after = after
            self._history_bytes += top.size
        else:
            self._push(_StackStep(label, before, after, merge_key))
        self._restore(after)

    def _new_id(self):
        identity = f"layer-{self._next_id}"
        self._next_id += 1
        return identity

    def _name(self, name):
        if name is None:
            used = {layer.name for layer in self._layers}
            number = len(self._layers) + 1
            while f"Layer {number}" in used:
                number += 1
            return f"Layer {number}"
        if not isinstance(name, str) or not name.strip():
            raise PixelError("Give the layer a name.")
        return name.strip()[:MAX_LAYER_NAME]

    def _check_room(self):
        if len(self._layers) >= MAX_LAYERS:
            raise PixelError(f"A sheet can have up to {MAX_LAYERS} layers. Merge or delete one first.")

    def add_layer(self, name=None):
        self._check_room()
        layer = Layer(self._new_id(), self._name(name), Image.new("RGBA", (self.width, self.height)))
        index = self._active + 1
        self._change("New layer", self._layers[:index] + (layer,) + self._layers[index:], active=index)
        return layer

    def duplicate_layer(self, index=None):
        index = self._resolve(index)
        self._check_room()
        source = self._layers[index]
        copy = replace(source, id=self._new_id(), name=self._name(f"{source.name} copy"), image=source.image.copy())
        self._change("Duplicate layer", self._layers[:index + 1] + (copy,) + self._layers[index + 1:], active=index + 1)
        return copy

    def delete_layer(self, index=None):
        index = self._resolve(index)
        if len(self._layers) == 1:
            raise PixelError("A sheet needs at least one layer.")
        active = self._active - 1 if index < self._active else max(0, index - 1) if index == self._active else self._active
        self._change("Delete layer", self._layers[:index] + self._layers[index + 1:], active=active)

    def move_layer(self, index, new_index):
        index = self._resolve(index)
        new_index = max(0, min(int(new_index), len(self._layers) - 1))
        if index == new_index:
            return
        active_id = self.active_layer.id
        layers = list(self._layers)
        layers.insert(new_index, layers.pop(index))
        active = next(position for position, layer in enumerate(layers) if layer.id == active_id)
        self._change("Move layer", layers, active=active)

    def update_layer(self, index=None, *, name=None, visible=None, locked=None, opacity=None, merge=False):
        """Rename, show/hide, lock or fade a layer; returns whether it changed."""
        index = self._resolve(index)
        layer = self._layers[index]
        changes = {}
        if name is not None:
            changes["name"] = self._name(name)
        for key, value in (("visible", visible), ("locked", locked)):
            if value is not None:
                changes[key] = bool(value)
        if opacity is not None:
            if type(opacity) is not int or not 0 <= opacity <= 100:
                raise PixelError("Layer opacity must be between 0 and 100.")
            changes["opacity"] = opacity
        changes = {key: value for key, value in changes.items() if getattr(layer, key) != value}
        if not changes:
            return False
        labels = {"name": "Rename layer", "visible": "Show or hide layer", "locked": "Lock layer", "opacity": "Layer opacity"}
        layers = self._layers[:index] + (replace(layer, **changes),) + self._layers[index + 1:]
        key = ("update", layer.id, tuple(sorted(changes))) if merge else None
        self._change(labels[next(iter(changes))], layers, merge_key=key)
        return True

    def merge_down(self, index=None):
        """Bake a layer into the one below, keeping the flattened look."""
        index = self._resolve(index)
        if index == 0:
            raise PixelError("There's no layer below to merge into.")
        upper, lower = self._layers[index], self._layers[index - 1]
        if upper.reference or lower.reference:
            raise PixelError("Reference layers stay separate so they never reach the game.")
        if not upper.visible or not lower.visible:
            raise PixelError("Show both layers before merging them.")
        if lower.locked or upper.locked:
            raise PixelError("Unlock both layers before merging them.")
        merged = _with_opacity(lower).copy()
        merged.alpha_composite(_with_opacity(upper))
        layers = self._layers[:index - 1] + (replace(lower, image=merged, opacity=100),) + self._layers[index + 1:]
        self._change("Merge down", layers, active=index - 1)

    def add_reference(self, image, name="Reference"):
        """Add a faded, locked tracing layer on top; painting stays on the active layer."""
        self._check_room()
        pixels = Image.new("RGBA", (self.width, self.height))
        source = image.convert("RGBA")
        pixels.paste(source.crop((0, 0, min(source.width, self.width), min(source.height, self.height))), (0, 0))
        layer = Layer(self._new_id(), self._name(name), pixels, locked=True, opacity=REFERENCE_OPACITY, reference=True)
        self._change("Add reference", self._layers + (layer,))
        return layer

    def resize_height(self, height):
        """Add or remove rows at the bottom of every layer."""
        if type(height) is not int or height < 1:
            raise PixelError("A sheet needs a positive height.")
        if self.width * height > MAX_CANVAS_PIXELS:
            raise PixelError("The painter edits sheets of up to 4,194,304 pixels, such as 2048 × 2048.")
        if height == self.height:
            return
        layers = []
        for layer in self._layers:
            image = Image.new("RGBA", (self.width, height))
            image.paste(layer.image.crop((0, 0, self.width, min(height, self.height))), (0, 0))
            layers.append(replace(layer, image=image))
        self._change("Add row" if height > self.height else "Remove row", layers, height=height)
