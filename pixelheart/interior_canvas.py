"""Direct manipulation of an interior, using the same rules as saved designs.

The canvas proposes edits through signals. The owning editor applies them, so a
preview, a cancelled drag, or an invalid placement never enters undo history.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from pixelheart_core.interiors import footprint, normalize_interior, render_interior
from pixelheart_core.interior_furniture import preview_frame


class InteriorCanvas(QWidget):
    tile_clicked = Signal(int, int)
    moved = Signal(str, int, int)
    selected = Signal(str)
    hovered = Signal(int, int)
    canceled = Signal()
    rotate_requested = Signal()
    room_drawn = Signal(int, int, int, int)
    room_selected = Signal(str)
    preview_changed = Signal(bool, str)

    def __init__(self, draft, parent=None, *, root=None):
        super().__init__(parent)
        self.draft = draft
        self.scale = 2
        self.image = QPixmap()
        self.tool = "select"
        self.selected_id = ""
        self.selected_room_id = ""
        self.project_root = root
        self.grid = False
        self.elapsed_ms = 0
        self.drag = None
        self.ghost = None
        self.preview_valid = True
        self.preview_message = ""
        self.cursor_tile = None
        self._placement = None
        self._room_start = None
        self._room_preview = None
        self._hover_room_id = ""
        self._drag_image = QPixmap()
        self._validation_key = None
        self._validation_result = (True, "")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Room designer: choose furniture, then click to place; drag to move")
        self.refresh_size()

    def refresh_size(self):
        self.setFixedSize(self.draft.data["width"] * 16 * self.scale,
                          self.draft.data["height"] * 16 * self.scale)
        self._update_ghost()
        self.update()

    def set_scale(self, scale):
        if type(scale) is not int or not 1 <= scale <= 8:
            raise ValueError("Choose a whole-number zoom from 1 to 8.")
        self.scale = scale
        self.refresh_size()

    def set_image(self, image):
        self.image = QPixmap.fromImage(ImageQt(image))
        if self.drag:
            self._refresh_drag_image()
        self._update_ghost()
        self.update()

    def set_placement(self, definition, rotation=0, root=None):
        """Hold a real library item at the cursor until placed or cancelled."""
        self._placement = (deepcopy(definition), rotation)
        if root is not None:
            self.project_root = root
        self.tool = "place"
        self.drag = None
        self._drag_image = QPixmap()
        self._room_start = None
        self._room_preview = None
        self._validation_key = None
        self._update_ghost()
        self.update()

    def clear_placement(self):
        self._placement = None
        self.ghost = None
        self._validation_key = None
        self._set_preview(True, "")
        self.update()

    def set_room_preview(self, rectangle=None):
        """Highlight a proposed floor rectangle, in tile coordinates."""
        if isinstance(rectangle, dict):
            rectangle = tuple(rectangle[key] for key in ("x", "y", "width", "height"))
        self._room_preview = tuple(rectangle) if rectangle is not None else None
        self._validation_key = None
        if self._room_preview is not None:
            self._set_preview(*self._validate_room(self._room_preview))
        else:
            self._set_preview(True, "")
        self.update()

    def footprint(self, placed):
        definition = next((item for item in self.draft.data["catalog"]
                           if item["id"] == placed["item_id"]), {})
        return footprint(definition, placed.get("rotation", 0))

    def _at(self, x, y):
        definitions = {item["id"]: item for item in self.draft.data["catalog"]}
        # Match the render order, so a chair on a rug is selected first.
        ordered = sorted(self.draft.data["furniture"], key=lambda item: (
            definitions[item["item_id"]]["kind"] != "rug",
            item["y"] + self.footprint(item)[1]))
        for placed in reversed(ordered):
            width, height = self.footprint(placed)
            if placed["x"] <= x < placed["x"] + width and placed["y"] <= y < placed["y"] + height:
                return placed
            definition = definitions[placed["item_id"]]
            frames = [frame for frame in definition.get("frames", [])
                      if frame["rotation"] == placed.get("rotation", 0)]
            if definition.get("preview_asset") and frames:
                # Sprites extend up from their floor footprint, just as in the
                # renderer. Tall cabinets can be grabbed by their visible top.
                sprite_width, sprite_height = frames[0]["rect"][2:]
                top = placed["y"] + height - sprite_height / 16
                if placed["x"] <= x + .5 < placed["x"] + sprite_width / 16 and top <= y + .5 < placed["y"] + height:
                    return placed
        return None

    def _room_at(self, x, y):
        return next((room for room in reversed(self.draft.data["rooms"])
                     if room["enabled"] and room["x"] <= x < room["x"] + room["width"]
                     and room["y"] <= y < room["y"] + room["height"]), None)

    def _position(self, event):
        cell = 16 * self.scale
        return math.floor(event.position().x() / cell), math.floor(event.position().y() / cell)

    def _set_preview(self, valid, message):
        state = valid, message
        changed = state != (self.preview_valid, self.preview_message)
        self.preview_valid, self.preview_message = state
        if changed:
            self.preview_changed.emit(*state)

    def _validate(self, key, candidate):
        key = (id(self.draft.data), *key)
        if key != self._validation_key:
            try:
                normalize_interior(candidate())
                result = (True, "")
            except ValueError as exc:
                result = (False, str(exc))
            self._validation_key, self._validation_result = key, result
        return self._validation_result

    def _validate_furniture(self, definition, rotation, x, y, identity=""):
        def candidate():
            data = self._collision_candidate(definition)
            if identity:
                placed = next(item for item in data["furniture"] if item["id"] == identity)
                placed.update(x=x, y=y, rotation=rotation)
            else:
                if not any(item["id"] == definition["id"] for item in data["catalog"]):
                    data["catalog"].append(deepcopy(definition))
                data["furniture"].append({"id": "__cursor_preview__", "item_id": definition["id"],
                                          "x": x, "y": y, "rotation": rotation,
                                          "mod_data": deepcopy(definition.get("mod_data", {}))})
            return data
        return self._validate(("furniture", definition["id"], rotation, x, y, identity), candidate)

    def _validate_room(self, rectangle):
        def candidate():
            data = self._collision_candidate()
            x, y, width, height = rectangle
            data["rooms"].append({"id": "__room_preview__", "name": "New room", "x": x, "y": y,
                                  "width": width, "height": height, "enabled": True, "optional": False})
            return data
        return self._validate(("room", *rectangle), candidate)

    def _collision_candidate(self, extra_definition=None):
        # The draft is already validated. Unplaced catalogue entries have no
        # collision effect; copying and validating thousands on each pointer
        # movement makes a large connected library feel sluggish.
        data = dict(self.draft.data)
        data["rooms"] = list(data["rooms"])
        data["furniture"] = [dict(item) for item in data["furniture"]]
        used = {item["item_id"] for item in data["furniture"]}
        if extra_definition:
            used.add(extra_definition["id"])
        data["catalog"] = [item for item in data["catalog"] if item["id"] in used]
        return data

    def _update_ghost(self):
        self.ghost = None
        if self.cursor_tile is None:
            return
        x, y = self.cursor_tile
        identity = ""
        if self.drag:
            identity, start_x, start_y, original_x, original_y = self.drag
            placed = next((item for item in self.draft.data["furniture"] if item["id"] == identity), None)
            if placed is None:
                self.drag = None
                return
            definition = next(item for item in self.draft.data["catalog"] if item["id"] == placed["item_id"])
            rotation = placed.get("rotation", 0)
            x, y = original_x + x - start_x, original_y + y - start_y
        elif self.tool == "place" and self._placement:
            definition, rotation = self._placement
        else:
            return
        try:
            width, height = footprint(definition, rotation)
        except ValueError as exc:
            self._set_preview(False, str(exc))
            return
        valid, message = self._validate_furniture(definition, rotation, x, y, identity)
        self.ghost = dict(x=x, y=y, width=width, height=height, definition=definition,
                          rotation=rotation, valid=valid, message=message)
        self._set_preview(valid, message)

    def _refresh_drag_image(self):
        self._drag_image = QPixmap()
        if not self.drag or self.project_root is None:
            return
        data = self.draft.snapshot()
        data["furniture"] = [item for item in data["furniture"] if item["id"] != self.drag[0]]
        with render_interior(data, self.project_root, self.elapsed_ms, self.grid) as image:
            self._drag_image = QPixmap.fromImage(ImageQt(image))

    def _draw_outline(self, painter, rectangle, color, fill_alpha=40, *, dashed=False):
        x, y, width, height = rectangle
        cell = 16 * self.scale
        rectangle = QRect(x * cell + 1, y * cell + 1, width * cell - 2, height * cell - 2)
        tint = QColor(color)
        tint.setAlpha(fill_alpha)
        painter.fillRect(rectangle, tint)
        pen = QPen(QColor(color), 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rectangle)
        return rectangle

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#292d30"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        base = self._drag_image if self.drag and not self._drag_image.isNull() else self.image
        if not base.isNull():
            painter.drawPixmap(self.rect(), base)
        cell = 16 * self.scale
        for room in self.draft.data["rooms"]:
            if room["id"] == self.selected_room_id or (self.tool in ("room", "room-select") and room["id"] == self._hover_room_id):
                rect = self._draw_outline(painter, tuple(room[key] for key in ("x", "y", "width", "height")),
                                          "#d6bb7d", 12)
                painter.drawText(rect.adjusted(7, 4, -7, -4), Qt.AlignmentFlag.AlignTop, room["name"])
        for key, caption, color in (("entry", "IN", "#78ab90"), ("spouse_stand", "♥", "#dba392")):
            if (key == "spouse_stand") != (self.draft.data["kind"] == "spouse"):
                continue
            x, y = self.draft.data[key]
            rect = QRect(x * cell, y * cell, cell, cell)
            tint = QColor(color)
            tint.setAlpha(145 if self.tool == key else 75)
            painter.fillRect(rect, tint)
            painter.setPen(QColor("#fffdf5"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, caption)
        placed = next((item for item in self.draft.data["furniture"] if item["id"] == self.selected_id), None)
        if placed and not self.drag:
            self._draw_outline(painter, (placed["x"], placed["y"], *self.footprint(placed)), "#edc784", 18)
        if self.ghost:
            ghost = self.ghost
            if self.project_root is not None:
                try:
                    with preview_frame(ghost["definition"], self.project_root, ghost["rotation"], self.elapsed_ms) as sprite:
                        pixmap = QPixmap.fromImage(ImageQt(sprite))
                    painter.setOpacity(.78)
                    painter.drawPixmap(QRect(ghost["x"] * cell,
                                              (ghost["y"] + ghost["height"]) * cell - pixmap.height() * self.scale,
                                              pixmap.width() * self.scale, pixmap.height() * self.scale), pixmap)
                    painter.setOpacity(1)
                except (ValueError, OSError):
                    pass
            color = "#81d2a1" if ghost["valid"] else "#f38a87"
            self._draw_outline(painter, tuple(ghost[key] for key in ("x", "y", "width", "height")), color, 55)
        if self._room_preview:
            color = "#81d2a1" if self.preview_valid else "#f38a87"
            rect = self._draw_outline(painter, self._room_preview, color, 55, dashed=True)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self._room_preview[2]} × {self._room_preview[3]}")
        painter.end()

    def _cancel(self):
        self.drag = None
        self._drag_image = QPixmap()
        self._room_start = None
        self._room_preview = None
        self.clear_placement()
        self.tool = "select"
        self.canceled.emit()
        self.update()

    def cancel_interaction(self):
        """Stop a held item or unfinished drag without discarding the design."""
        self._cancel()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            if self.tool == "place" and self._placement:
                self.rotate_requested.emit()
            else:
                self._cancel()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        x, y = self._position(event)
        self.cursor_tile = x, y
        if self.tool == "room":
            self._room_start = x, y
            self.set_room_preview((x, y, 1, 1))
        elif self.tool in ("select", "room-select"):
            placed = self._at(x, y) if self.tool == "select" else None
            self.selected_id = placed["id"] if placed else ""
            self.drag = (placed["id"], x, y, placed["x"], placed["y"]) if placed else None
            self.selected.emit(self.selected_id)
            if placed:
                self.selected_room_id = ""
                self._refresh_drag_image()
            else:
                room = self._room_at(x, y)
                self.selected_room_id = room["id"] if room else ""
                self.room_selected.emit(self.selected_room_id)
        else:
            self.tile_clicked.emit(x, y)
        self._update_ghost()
        self.update()

    def mouseMoveEvent(self, event):
        self.cursor_tile = x, y = self._position(event)
        self.hovered.emit(x, y)
        room = self._room_at(x, y)
        self._hover_room_id = room["id"] if room else ""
        if self._room_start:
            start_x, start_y = self._room_start
            self.set_room_preview((min(start_x, x), min(start_y, y), abs(x-start_x)+1, abs(y-start_y)+1))
        else:
            self._update_ghost()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = self._position(event)
        self.cursor_tile = x, y
        if self._room_start:
            start_x, start_y = self._room_start
            rectangle = min(start_x, x), min(start_y, y), abs(x-start_x)+1, abs(y-start_y)+1
            self._room_start = None
            self.set_room_preview(None)
            self.room_drawn.emit(*rectangle)
        elif self.drag:
            identity, start_x, start_y, original_x, original_y = self.drag
            self.drag = None
            self._drag_image = QPixmap()
            self.ghost = None
            if (x, y) != (start_x, start_y):
                self.moved.emit(identity, original_x + x - start_x, original_y + y - start_y)
        self.update()

    def leaveEvent(self, event):
        if not self.drag and not self._room_start:
            self.cursor_tile = None
            self.ghost = None
            self._hover_room_id = ""
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
        else:
            super().keyPressEvent(event)
