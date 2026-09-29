"""What each painter tool does with a press, drag and release.

Coordinates are canvas pixels and may fall outside the sheet while dragging.
A refused action raises ``PixelError`` before anything changes. Each stroke,
shape or fill is one undo step; moving a selection stays one step until the
floating pixels are dropped.
"""
from __future__ import annotations

from .pixel_document import CLEAR
from .pixel_raster import (
    Mask, brush_mask, ellipse_mask, equal_color_mask, flood_mask, line_points, mirror_mask,
    rectangle_mask, union,
)


TOOLS = ("pencil", "eraser", "line", "rectangle", "ellipse", "fill", "picker", "select")
LABELS = {"pencil": "Pencil", "eraser": "Eraser", "line": "Line", "rectangle": "Rectangle",
          "ellipse": "Ellipse", "fill": "Fill", "picker": "Pick color", "select": "Select"}
MAX_RECENT_COLORS = 16
INK = (38, 34, 29, 255)


def _sign(value):
    return (value > 0) - (value < 0)


class PixelTools:
    def __init__(self, document):
        self.document = document
        self.tool = "pencil"
        self.primary = INK
        self.secondary = CLEAR
        self.brush = 1
        self.filled = False
        self.contiguous = True
        self.mirror = False
        self.recent = []
        self._stroke = None
        self._last_point = None

    @property
    def active(self):
        return self._stroke is not None

    def swap_colors(self):
        self.primary, self.secondary = self.secondary, self.primary

    def remember_color(self, color):
        color = tuple(color)
        if color[3] == 0:
            return
        self.recent = [color] + [recent for recent in self.recent if recent != color]
        del self.recent[MAX_RECENT_COLORS:]

    # Pointer events --------------------------------------------------------

    def press(self, x, y, *, secondary=False, shift=False, alt=False):
        self.cancel()
        tool = "picker" if alt else self.tool
        if tool == "picker":
            self._stroke = {"tool": tool, "secondary": secondary}
            self._pick(x, y)
            return
        if tool == "select":
            self._press_select(x, y)
            return
        color = CLEAR if tool == "eraser" else self.secondary if secondary else self.primary
        self.document.begin_edit(LABELS[tool])
        self._stroke = {"tool": tool, "color": tuple(color), "start": (x, y), "last": (x, y), "shift": shift}
        if tool in ("pencil", "eraser"):
            start = self._last_point if shift and self._last_point is not None else (x, y)
            self._paint(self._line(start, (x, y)))
        elif tool == "fill":
            self._fill(x, y)
            self._finish((x, y))
        else:
            self._shape((x, y))

    def move(self, x, y, *, shift=None):
        stroke = self._stroke
        if stroke is None:
            return
        if shift is not None and "shift" in stroke:
            stroke["shift"] = shift
        tool = stroke["tool"]
        if tool == "picker":
            self._pick(x, y)
        elif tool == "select":
            self._move_select(x, y)
        elif tool in ("pencil", "eraser"):
            if (x, y) != stroke["last"]:
                self._paint(self._line(stroke["last"], (x, y)))
        elif tool in ("line", "rectangle", "ellipse"):
            self._shape((x, y))
        stroke["last"] = (x, y)

    def release(self, x, y):
        stroke = self._stroke
        if stroke is None:
            return
        self.move(x, y)
        if stroke["tool"] == "select":
            if not stroke["moved"] and stroke["mode"] == "select":
                self.document.set_selection(None)
            self._stroke = None
        elif stroke["tool"] == "picker":
            self._stroke = None
        else:
            self._finish((x, y))

    def cancel(self):
        """Abandon a stroke in progress, leaving the sheet as it was."""
        stroke, self._stroke = self._stroke, None
        if stroke is not None and stroke["tool"] not in ("picker", "select") and self.document.editing:
            self.document.cancel_edit()

    # Painting ----------------------------------------------------------------

    def _finish(self, point):
        stroke, self._stroke = self._stroke, None
        if stroke is None:
            return
        if self.document.commit_edit():
            self.remember_color(stroke["color"])
        self._last_point = point

    def _bounds(self):
        return self.document.width, self.document.height

    def _mirrored(self, mask):
        if not self.mirror:
            return mask
        return mirror_mask(mask, frame_width=self.document.frame_size[0], canvas_width=self.document.width)

    def _paint(self, mask):
        self.document.paint(self._mirrored(mask), self._stroke["color"])

    def _line(self, start, end):
        return brush_mask(line_points(*start, *end), self.brush, self._bounds())

    def _shape(self, point):
        stroke = self._stroke
        (x0, y0), (x1, y1) = stroke["start"], point
        dx, dy = x1 - x0, y1 - y0
        tool = stroke["tool"]
        if stroke["shift"]:
            if tool == "line":
                if abs(dx) > 2 * abs(dy):
                    dy = 0
                elif abs(dy) > 2 * abs(dx):
                    dx = 0
                else:
                    length = (abs(dx) + abs(dy)) // 2
                    dx, dy = _sign(dx) * length, _sign(dy) * length
            else:
                side = max(abs(dx), abs(dy))
                dx, dy = (_sign(dx) or 1) * side, (_sign(dy) or 1) * side
        x1, y1 = x0 + dx, y0 + dy
        self.document.restore_edit()
        if tool == "line":
            mask = self._line((x0, y0), (x1, y1))
        elif tool == "rectangle":
            mask = rectangle_mask(x0, y0, x1, y1, filled=self.filled, bounds=self._bounds())
        else:
            mask = ellipse_mask(x0, y0, x1, y1, filled=self.filled, bounds=self._bounds())
        self._paint(mask)

    def _fill(self, x, y):
        image = self.document.active_layer.image
        points = [(x, y)]
        if self.mirror:
            width = self.document.frame_size[0]
            left = x // width * width
            points.append((left + width - 1 - (x - left), y))
        masks = []
        for point_x, point_y in points:
            if not (0 <= point_x < image.width and 0 <= point_y < image.height):
                continue
            if self.contiguous:
                masks.append(flood_mask(image, point_x, point_y))
            else:
                masks.append(Mask(equal_color_mask(image, image.getpixel((point_x, point_y))), 0, 0))
        mask = union(*masks)
        if mask is not None:
            self.document.paint(mask, self._stroke["color"])

    def _pick(self, x, y):
        color = self.document.pixel_at(x, y)
        if color is None:
            return
        if self._stroke["secondary"]:
            self.secondary = color
        else:
            self.primary = color

    # Selection ---------------------------------------------------------------

    def _inside_selection(self, x, y):
        box = self.document.selection
        return box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]

    def _press_select(self, x, y):
        if self._inside_selection(x, y):
            if not self.document.floating:
                self.document.lift_selection()
            mode = "move"
        else:
            self.document.drop_floating()
            self.document.set_selection(None)
            mode = "select"
        self._stroke = {"tool": "select", "mode": mode, "start": (x, y), "last": (x, y), "moved": False}

    def _move_select(self, x, y):
        stroke = self._stroke
        if (x, y) != stroke["start"]:
            stroke["moved"] = True
        if stroke["mode"] == "move":
            last_x, last_y = stroke["last"]
            self.document.move_floating(x - last_x, y - last_y)
        elif stroke["moved"]:
            x0, y0 = stroke["start"]
            self.document.set_selection((min(x0, x), min(y0, y), max(x0, x) + 1, max(y0, y) + 1))
