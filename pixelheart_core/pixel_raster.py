"""Exact, aliased raster helpers for the pixel painter.

A ``Mask`` marks canvas pixels with 255 in a mode ``L`` image placed at
``(left, top)``. Painting replaces masked pixels outright, alpha included, so a
stroke never blends with what was underneath. Nothing here antialiases.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw


@dataclass(frozen=True)
class Mask:
    image: Image.Image
    left: int
    top: int

    @property
    def box(self):
        width, height = self.image.size
        return self.left, self.top, self.left + width, self.top + height


def _clip(box, bounds):
    left, top, right, bottom = box
    width, height = bounds
    clipped = max(0, left), max(0, top), min(width, right), min(height, bottom)
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def _trim(mask):
    """Shrink a mask to its painted pixels, or None when nothing is painted."""
    if mask is None:
        return None
    bbox = mask.image.getbbox()
    if bbox is None:
        return None
    if bbox == (0, 0, *mask.image.size):
        return mask
    return Mask(mask.image.crop(bbox), mask.left + bbox[0], mask.top + bbox[1])


def _canvas_mask(box, bounds, draw):
    """Draw in canvas coordinates onto a mask covering ``box`` clipped to the canvas."""
    clipped = _clip(box, bounds)
    if clipped is None:
        return None
    left, top, right, bottom = clipped
    image = Image.new("L", (right - left, bottom - top))
    draw(ImageDraw.Draw(image), left, top)
    return _trim(Mask(image, left, top))


def line_points(x0, y0, x1, y1):
    """Bresenham's line, both ends included, with no diagonal gaps skipped."""
    points = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    step_x, step_y = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            return points
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x += step_x
        if doubled <= dx:
            error += dx
            y += step_y


def brush_mask(points, size, bounds):
    """Square brush stamps; even sizes extend right and down from the point."""
    points = list(points)
    if not points:
        return None
    size = max(1, int(size))
    before, after = (size - 1) // 2, size // 2
    xs, ys = [x for x, _ in points], [y for _, y in points]
    box = min(xs) - before, min(ys) - before, max(xs) + after + 1, max(ys) + after + 1

    def draw(canvas, left, top):
        for x, y in points:
            canvas.rectangle((x - before - left, y - before - top, x + after - left, y + after - top), fill=255)
    return _canvas_mask(box, bounds, draw)


def _ordered(x0, y0, x1, y1):
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def rectangle_mask(x0, y0, x1, y1, *, filled, bounds):
    left, top, right, bottom = _ordered(x0, y0, x1, y1)

    def draw(canvas, origin_x, origin_y):
        shape = (left - origin_x, top - origin_y, right - origin_x, bottom - origin_y)
        canvas.rectangle(shape, fill=255 if filled else None, outline=255)
    return _canvas_mask((left, top, right + 1, bottom + 1), bounds, draw)


def ellipse_mask(x0, y0, x1, y1, *, filled, bounds):
    left, top, right, bottom = _ordered(x0, y0, x1, y1)
    # Draw unclipped first so a partly off-canvas ellipse keeps its true shape.
    shape = Image.new("L", (right - left + 1, bottom - top + 1))
    ImageDraw.Draw(shape).ellipse((0, 0, right - left, bottom - top), fill=255 if filled else None, outline=255, width=1)
    clipped = _clip((left, top, right + 1, bottom + 1), bounds)
    if clipped is None:
        return None
    crop = (clipped[0] - left, clipped[1] - top, clipped[2] - left, clipped[3] - top)
    return _trim(Mask(shape.crop(crop), clipped[0], clipped[1]))


def union(*masks):
    masks = [mask for mask in masks if mask is not None]
    if not masks:
        return None
    if len(masks) == 1:
        return masks[0]
    boxes = [mask.box for mask in masks]
    left, top = min(box[0] for box in boxes), min(box[1] for box in boxes)
    right, bottom = max(box[2] for box in boxes), max(box[3] for box in boxes)
    image = Image.new("L", (right - left, bottom - top))
    for mask in masks:
        region = (mask.left - left, mask.top - top, mask.left - left + mask.image.width, mask.top - top + mask.image.height)
        image.paste(ImageChops.lighter(image.crop(region), mask.image), region[:2])
    return Mask(image, left, top)


def mirror_mask(mask, *, frame_width, canvas_width):
    """Add each frame column's left-right reflection of the mask."""
    if mask is None:
        return None
    frame_width = max(1, min(int(frame_width), canvas_width))
    band = Image.new("L", (canvas_width, mask.image.height))
    band.paste(mask.image, (mask.left, 0))
    mirrored = band.copy()
    for left in range(0, canvas_width, frame_width):
        right = min(canvas_width, left + frame_width)
        cell = band.crop((left, 0, right, band.height))
        if cell.getbbox():
            reflected = cell.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            mirrored.paste(ImageChops.lighter(mirrored.crop((left, 0, right, band.height)), reflected), (left, 0))
    return _trim(Mask(mirrored, 0, mask.top))


def equal_color_mask(image, color):
    """255 where a pixel equals ``color``; every fully transparent pixel is equal."""
    red, green, blue, alpha = color
    if alpha == 0:
        return image.getchannel("A").point(lambda value: 255 if value == 0 else 0)
    result = None
    for band, target in zip(image.split(), (red, green, blue, alpha)):
        matches = band.point(lambda value, target=target: 255 if value == target else 0)
        result = matches if result is None else ImageChops.multiply(result, matches)
    return result


def flood_mask(image, x, y):
    """The four-connected region of exactly the clicked color."""
    if not (0 <= x < image.width and 0 <= y < image.height):
        return None
    region = equal_color_mask(image, image.getpixel((x, y)))
    ImageDraw.floodfill(region, (x, y), 128, thresh=0)
    return _trim(Mask(region.point(lambda value: 255 if value == 128 else 0), 0, 0))


def apply_mask(image, mask, color, *, clip=None):
    """Replace masked pixels with ``color``; return the touched box or None."""
    if mask is None:
        return None
    box = _clip(mask.box, image.size)
    if box is not None and clip is not None:
        box = _clip((max(box[0], clip[0]), max(box[1], clip[1]), min(box[2], clip[2]), min(box[3], clip[3])), image.size)
    if box is None:
        return None
    local = (box[0] - mask.left, box[1] - mask.top, box[2] - mask.left, box[3] - mask.top)
    region = mask.image.crop(local)
    if region.getbbox() is None:
        return None
    image.paste(tuple(color), box, region)
    return box
