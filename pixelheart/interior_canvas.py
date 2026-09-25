"""Direct manipulation of an interior, using the same rules as saved designs.

The canvas proposes edits to its owning editor, so a
preview, a cancelled drag, or an invalid placement never enters undo history.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pixelheart_core.interiors import (
    footprint, normalize_interior, render_interior, validate_furniture_placement, validate_spouse_access,
)
from pixelheart_core.interior_furniture import preview_frame, preview_frame_offset, frame_at, held_item_preview_offset
from pixelheart_core.interior_layout import partition_rectangle, partition_opening_rectangle
from pixelheart_core.interior_spouse_context import SPOUSE_CONTEXT_ORIGIN, SPOUSE_CONTEXT_SIZE, spouse_context_layers


FURNITURE_MIME = "application/x-pixelheart-interior-furniture"
ROOM_MIME = "application/x-pixelheart-interior-room"


class InteriorCanvas(QWidget):
    tile_clicked = Signal(int, int)
    moved = Signal(str, int, int)
    selected = Signal(str)
    hovered = Signal(int, int)
    canceled = Signal()
    rotate_requested = Signal()
    room_drawn = Signal(int, int, int, int)
    room_selected = Signal(str)
    room_moved = Signal(str, int, int)
    room_resized = Signal(str, int, int, int, int)
    corridor_drawn = Signal(int, int, int, int)
    partition_drawn = Signal(str, int, int, int)
    partition_selected = Signal(str)
    doorway_moved = Signal(int, int)
    architecture_placed = Signal(str, int, int)
    architecture_moved = Signal(str, int, int)
    architecture_selected = Signal(str)
    preview_changed = Signal(bool, str)

    def __init__(self, draft, parent=None, *, root=None):
        super().__init__(parent)
        self.draft = draft
        self.scale = 2
        self.image = QPixmap()
        self._context_background = QPixmap()
        self._context_foreground = QPixmap()
        self._context_key = None
        self.tool = "select"
        self.selected_id = ""
        self.selected_room_id = ""
        self.selected_partition_id = ""
        self.selected_architecture_id = ""
        self.architecture_candidate = None
        self.furniture_validator = None
        self._architecture_placement = None
        self._pending_architecture_place = None
        self._pending_architecture_move = None
        self._architecture_drag = None
        self._architecture_preview = None
        self._architecture_clearance = set()
        self._architecture_candidate_data = None
        self._architecture_candidate_image = QPixmap()
        self._architecture_ghost_image = QPixmap()
        self._architecture_edit_key = None
        self._architecture_image_key = None
        self.project_root = root
        self.grid = False
        self.elapsed_ms = 0
        self.time_of_day = "day"
        self.lights_on = False
        self.drag = None
        self._pending_move = None
        self.catalogue_source = None
        self.place_catalog_drop = None
        self.catalogue_drag = None
        self.ghost = None
        self.preview_valid = True
        self.preview_message = ""
        self.cursor_tile = None
        self.cursor_position = None
        self._placement = None
        self._room_start = None
        self._room_preview = None
        self._pending_room_move = None
        self._room_drag = None
        self._pending_room_resize = None
        self._room_resize = None
        self._structure_start = None
        self._structure_kind = None
        self.room_catalogue_source = None
        self.room_dimensions = None
        self.room_top_edge_origin = None
        self.room_candidate = None
        self.room_resize_candidate = None
        self.corridor_candidate = None
        self.partition_candidate = None
        self.partition_thickness = lambda axis: 1
        self.opening_candidate = None
        self.place_room_drop = None
        self.room_catalogue_drag = None
        self._room_drop_origin = None
        self._room_candidate_data = None
        self._room_candidate_image = QPixmap()
        self._room_edit_key = None
        self.doorway_candidate = None
        self._pending_doorway_move = None
        self._doorway_drag = None
        self._doorway_preview = None
        self._hover_room_id = ""
        self._drag_image = QPixmap()
        self._validation_key = None
        self._validation_result = (True, "")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Room designer: drag furniture into the room; in Rooms, drag a new room onto the layout or drag an existing room to move it")
        self.refresh_size()

    def refresh_size(self):
        width, height = self.view_size
        self.setFixedSize(width * 16 * self.scale, height * 16 * self.scale)
        self._update_ghost()
        self._update_architecture_preview()
        self.update()

    @property
    def view_origin(self):
        """Offset of editable map tiles within the surrounding preview."""
        return SPOUSE_CONTEXT_ORIGIN if self.draft.data["kind"] == "spouse" else (0, 0)

    @property
    def view_size(self):
        return SPOUSE_CONTEXT_SIZE if self.draft.data["kind"] == "spouse" else (self.draft.data["width"], self.draft.data["height"])

    def _refresh_context(self):
        key = (self.draft.data["kind"], str(self.project_root), self.draft.data.get("spouse_context"))
        if key == self._context_key:
            return
        self._context_background = QPixmap()
        self._context_foreground = QPixmap()
        if self.draft.data["kind"] == "spouse":
            background, foreground = spouse_context_layers(self.draft.data, self.project_root)
            try:
                self._context_background = QPixmap.fromImage(ImageQt(background))
                self._context_foreground = QPixmap.fromImage(ImageQt(foreground))
            finally:
                background.close()
                foreground.close()
        self._context_key = deepcopy(key)

    def set_scale(self, scale):
        if type(scale) is not int or not 1 <= scale <= 8:
            raise ValueError("Choose a whole-number zoom from 1 to 8.")
        self.scale = scale
        self.refresh_size()

    def set_image(self, image):
        self._refresh_context()
        self.image = QPixmap.fromImage(ImageQt(image))
        if self.drag:
            self._refresh_drag_image()
        self._update_ghost()
        self._update_architecture_preview()
        self.update()

    def set_placement(self, definition, rotation=0, root=None):
        """Hold a real library item at the cursor until placed or cancelled."""
        self.clear_architecture_interaction()
        self._placement = (deepcopy(definition), rotation)
        if root is not None:
            self.project_root = root
        self.tool = "place"
        self.drag = None
        self._pending_move = None
        self._drag_image = QPixmap()
        self._room_start = None
        self._room_preview = None
        self._pending_room_move = None
        self._room_drag = None
        self._pending_room_resize = None
        self._room_resize = None
        self._structure_start = None
        self._structure_kind = None
        self.room_catalogue_drag = None
        self._room_drop_origin = None
        self._room_candidate_data = None
        self._room_candidate_image = QPixmap()
        self._room_edit_key = None
        self._validation_key = None
        self._update_ghost()
        self._update_cursor()
        self.update()

    def clear_placement(self):
        self._placement = None
        self._pending_move = None
        self.ghost = None
        self._validation_key = None
        self._set_preview(True, "")
        self._update_cursor()
        self.update()

    def clear_catalogue_drag(self):
        """Remove a native drag preview, including drops outside the room."""
        self.catalogue_drag = None
        self.cursor_tile = None
        self.ghost = None
        self._validation_key = None
        self._set_preview(True, "")
        self._update_cursor()
        self.update()

    def clear_room_catalogue_drag(self):
        """Discard a new-room drag preview without making an edit."""
        self.room_catalogue_drag = None
        self._room_drop_origin = None
        self.cursor_tile = None
        self.set_room_preview(None)
        self._update_cursor()

    def clear_room_interaction(self):
        """Cancel room gestures when the editor changes mode or history."""
        self.clear_architecture_interaction()
        self._pending_room_move = None
        self._room_drag = None
        self._pending_room_resize = None
        self._room_resize = None
        self._structure_start = None
        self._structure_kind = None
        self._room_start = None
        self._pending_doorway_move = None
        self._doorway_drag = None
        self._doorway_preview = None
        self.clear_room_catalogue_drag()

    def set_architecture_placement(self, definition, root=None):
        """Pick up a static map piece independently of real furniture."""
        self.clear_room_interaction()
        self.clear_placement()
        self.drag = None
        self._drag_image = QPixmap()
        self._architecture_placement = deepcopy(definition)
        if root is not None:
            self.project_root = root
        self.tool = "architecture-place"
        self._update_architecture_preview()
        self._update_cursor()
        self.update()

    def clear_architecture_interaction(self, clear_placement=True):
        self._pending_architecture_place = None
        self._pending_architecture_move = None
        self._architecture_drag = None
        self._architecture_preview = None
        self._architecture_clearance = set()
        self._architecture_candidate_data = None
        self._architecture_candidate_image = QPixmap()
        self._architecture_ghost_image = QPixmap()
        self._architecture_edit_key = None
        self._architecture_image_key = None
        if clear_placement:
            self._architecture_placement = None
        self._set_preview(True, "")
        self._update_cursor()
        self.update()

    def _architecture_definition(self, identity):
        return next((piece for piece in self.draft.data.get("architecture_catalog", [])
                     if piece["id"] == identity), None)

    def _architecture_at(self, x, y):
        active = {room["id"] for room in self.draft.data["rooms"] if room["enabled"]}
        for placed in reversed(self.draft.data.get("architecture", [])):
            definition = self._architecture_definition(placed["piece_id"])
            if (definition is not None and placed["room_id"] in active
                    and placed["x"] <= x < placed["x"] + definition["width"]
                    and placed["y"] <= y < placed["y"] + definition["height"]):
                return placed
        return None

    def _preview_architecture_edit(self, piece_id, x, y, placement_id=None, *, force=False):
        key = (id(self.draft.data), piece_id, x, y, placement_id,
               self.elapsed_ms, self.time_of_day, self.lights_on, self.grid)
        if not force and key == self._architecture_edit_key:
            return self.preview_valid
        self._architecture_edit_key = key
        self._architecture_candidate_data = None
        self._architecture_candidate_image = QPixmap()
        definition = self._architecture_definition(piece_id)
        if definition is None:
            self._architecture_preview = None
            self._architecture_clearance = set()
            self._set_preview(False, "Choose an architectural piece from this project's catalogue.")
            return False
        self._architecture_preview = (x, y, definition["width"], definition["height"])
        from pixelheart_core.interior_architecture_rules import architecture_clearance_cells
        artwork = {(column, row) for column in range(x, x + definition["width"])
                   for row in range(y, y + definition["height"])}
        self._architecture_clearance = architecture_clearance_cells({"x": x, "y": y}, definition) - artwork
        try:
            if self.architecture_candidate is not None:
                candidate = self.architecture_candidate(piece_id, x, y, placement_id)
            else:
                from pixelheart_core.interior_architecture import architecture_candidate
                candidate = architecture_candidate(self._collision_candidate(), piece_id, x, y,
                                                   placement_id=placement_id)
            self._architecture_candidate_data = candidate
            from pixelheart_core.interior_levels import stair_connection_clearance
            proposed = next((item for item in candidate.get("architecture", []) if item["id"] == placement_id), None)
            if proposed is None and candidate.get("architecture"):
                proposed = candidate["architecture"][-1]
            if proposed is not None:
                self._architecture_clearance = stair_connection_clearance(candidate, proposed, definition) - artwork
            self._set_preview(True, "Release to move the piece" if placement_id else "Click to place the piece")
            if self.project_root is not None:
                with render_interior(candidate, self.project_root, self.elapsed_ms, self.grid,
                                     time_of_day=self.time_of_day, lights_on=self.lights_on) as image:
                    self._architecture_candidate_image = QPixmap.fromImage(ImageQt(image))
        except (ValueError, OSError) as exc:
            self._set_preview(False, str(exc))
        image_key = (id(self.draft.data), piece_id, self.project_root)
        if image_key != self._architecture_image_key:
            self._architecture_image_key = image_key
            self._architecture_ghost_image = QPixmap()
            if self.project_root is not None:
                try:
                    from pixelheart_core.interior_architecture import architecture_preview
                    with architecture_preview(definition, self.draft.data, self.project_root) as image:
                        self._architecture_ghost_image = QPixmap.fromImage(ImageQt(image))
                except (ValueError, OSError):
                    pass
        self.update()
        return self.preview_valid

    def _update_architecture_preview(self):
        if self.cursor_tile is None:
            return
        x, y = self.cursor_tile
        if self._architecture_drag:
            identity, start_x, start_y, original_x, original_y = self._architecture_drag
            placed = next((piece for piece in self.draft.data.get("architecture", []) if piece["id"] == identity), None)
            if placed is None:
                self.clear_architecture_interaction()
                return
            self._preview_architecture_edit(placed["piece_id"], original_x + x - start_x,
                                            original_y + y - start_y, identity)
        elif self.tool == "architecture-place" and self._architecture_placement is not None:
            self._preview_architecture_edit(self._architecture_placement["id"], x, y)

    def _doorway_at(self, x, y):
        point = self.draft.data.get("doorway")
        return point is not None and x == point[0] and y in (point[1], point[1] + 1)

    def _preview_doorway_edit(self, x, y, *, force=False):
        key = (id(self.draft.data), "doorway", x, y)
        if not force and key == self._room_edit_key:
            return self.preview_valid
        self._room_edit_key = key
        self._doorway_preview = (x, y)
        self._room_candidate_data = None
        self._room_candidate_image = QPixmap()
        try:
            if self.doorway_candidate is not None:
                candidate = self.doorway_candidate(x, y)
            else:
                from pixelheart_core.interiors import place_doorway
                candidate = place_doorway(self._collision_candidate(), x, y)
            self._room_candidate_data = candidate
            self._set_preview(True, "Release to place the doorway" if self._doorway_drag
                              else "Click to place the doorway")
            if self.project_root is not None:
                with render_interior(candidate, self.project_root, self.elapsed_ms, self.grid,
                                     time_of_day=self.time_of_day, lights_on=self.lights_on) as image:
                    self._room_candidate_image = QPixmap.fromImage(ImageQt(image))
        except (ValueError, OSError) as exc:
            self._set_preview(False, str(exc))
        self.update()
        return self.preview_valid

    def _room_drop_dimensions(self, event):
        if (self.tool != "room-select" or self.room_catalogue_source is None
                or event.source() is not self.room_catalogue_source
                or not event.possibleActions() & Qt.DropAction.CopyAction
                or not event.mimeData().hasFormat(ROOM_MIME)
                or bytes(event.mimeData().data(ROOM_MIME)) != b"new-room"
                or self.room_dimensions is None):
            return None
        return self.room_dimensions()

    def _preview_room_edit(self, identity, x, y, width, height, *, force=False, resize=False):
        """Show the core's snapped rectangle, keeping the draft untouched."""
        key = (id(self.draft.data), "resize" if resize else "move", identity, x, y, width, height)
        if not force and key == self._room_edit_key:
            return self.preview_valid
        self._room_edit_key = key
        self._room_preview = (x, y, width, height)
        self._room_candidate_data = None
        self._room_candidate_image = QPixmap()
        try:
            if resize:
                if self.room_resize_candidate is None:
                    raise ValueError("Room resizing is unavailable.")
                candidate = self.room_resize_candidate(identity, x, y, width, height)
            elif self.room_candidate is not None:
                candidate = self.room_candidate(identity, x, y, width, height)
            else:
                from pixelheart_core.interiors import room_edit_candidate
                candidate = room_edit_candidate(self.draft.data, room_id=identity,
                                                x=x, y=y, width=width, height=height)
            room = (next(room for room in candidate["rooms"] if room["id"] == identity)
                    if identity else next(room for room in reversed(candidate["rooms"])
                                          if room.get("kind") != "stairway"))
            if resize and tuple(room[key] for key in ("x", "y", "width", "height")) != (x, y, width, height):
                raise ValueError("Resize the room without shifting its opposite edge.")
            self._room_preview = tuple(room[key] for key in ("x", "y", "width", "height"))
            self._room_candidate_data = candidate
            self._set_preview(True, "")
        except ValueError as exc:
            self._set_preview(False, str(exc))
        if self._room_candidate_data is not None and self.project_root is not None:
            try:
                with render_interior(self._room_candidate_data, self.project_root, self.elapsed_ms, self.grid,
                                     time_of_day=self.time_of_day, lights_on=self.lights_on) as image:
                    self._room_candidate_image = QPixmap.fromImage(ImageQt(image))
            except (ValueError, OSError):
                pass
        self.update()
        return self.preview_valid

    def _preview_room_catalogue_drag(self, event, dimensions, *, force=False):
        self.room_catalogue_drag = dimensions
        self.cursor_tile = pointer_x, pointer_y = self._position(event)
        # Hold the new room at its center so the pointer can reach both the
        # left and right side of an existing room without leaving the canvas.
        width, height = dimensions
        x, y = pointer_x - width // 2, pointer_y - height // 2
        self._room_drop_origin = x, y
        valid = self._preview_room_edit(None, x, y, width, height, force=force)
        if not valid and 0 <= pointer_y <= 1 and self.room_top_edge_origin is not None:
            origin = self.room_top_edge_origin(x, width, height)
            if origin is not None:
                self._room_drop_origin = origin
                return self._preview_room_edit(None, *origin, width, height, force=force)
        if valid or pointer_x > 1 or pointer_x < 0:
            return valid
        # A wide room's center can fall beyond the canvas when adding space
        # on the left. At the outermost tile, let an exterior edge catch the
        # room, while ordinary overlap and disconnected drops stay invalid.
        exterior = min((room["x"] for room in self.draft.data["rooms"] if room["enabled"]), default=0)
        for room in self.draft.data["rooms"]:
            if (room["enabled"] and room["x"] == exterior and pointer_x < room["x"]
                    and x < room["x"] < x + width
                    and min(y + height, room["y"] + room["height"]) > max(y, room["y"])):
                x = room["x"] - width
                self._room_drop_origin = x, y
                return self._preview_room_edit(None, x, y, width, height, force=force)
        return False

    def _preview_structure_edit(self, kind, *proposal, force=False):
        thickness = self.partition_thickness(proposal[0]) if kind == "partition" else None
        key = (id(self.draft.data), kind, self.selected_partition_id, thickness, *proposal)
        if not force and key == self._room_edit_key:
            return self.preview_valid
        self._room_edit_key = key
        self._structure_kind = kind
        if kind == "partition":
            axis, x, y, length = proposal
            self._room_preview = partition_rectangle(
                {"axis": axis, "x": x, "y": y, "length": length, "thickness": thickness}, visual=True)
        elif kind == "opening":
            self._room_preview = (*proposal, 1, 1)
        else:
            self._room_preview = proposal
        self._room_candidate_data = None
        self._room_candidate_image = QPixmap()
        try:
            callback = getattr(self, kind + "_candidate")
            if callback is None:
                raise ValueError("This floorplan tool is unavailable.")
            candidate = callback(*proposal)
            self._room_candidate_data = candidate
            if kind == "corridor":
                room = candidate["rooms"][-1]
                self._room_preview = tuple(room[key] for key in ("x", "y", "width", "height"))
            elif kind == "partition":
                self._room_preview = partition_rectangle(candidate["partitions"][-1], visual=True)
            elif kind == "opening":
                source = self._partition_at(*proposal)
                wall = next((wall for wall in candidate.get("partitions", [])
                             if wall["id"] == (source["id"] if source else self.selected_partition_id)), None)
                if wall:
                    for opening in wall["openings"]:
                        rectangle = partition_opening_rectangle(wall, opening, visual=True)
                        x, y = rectangle[:2]
                        if x <= proposal[0] < x + rectangle[2] and y <= proposal[1] < y + rectangle[3]:
                            self._room_preview = rectangle
                            break
            self._set_preview(True, "")
            if self.project_root is not None:
                with render_interior(candidate, self.project_root, self.elapsed_ms, self.grid,
                                     time_of_day=self.time_of_day, lights_on=self.lights_on) as image:
                    self._room_candidate_image = QPixmap.fromImage(ImageQt(image))
        except (ValueError, OSError) as exc:
            self._set_preview(False, str(exc))
        self.update()
        return self.preview_valid

    @staticmethod
    def _structure_proposal(kind, start_x, start_y, x, y):
        if kind == "corridor":
            return min(start_x, x), min(start_y, y), abs(x - start_x) + 1, abs(y - start_y) + 1
        if abs(x - start_x) >= abs(y - start_y):
            return "horizontal", min(start_x, x), start_y, abs(x - start_x) + 1
        return "vertical", start_x, min(start_y, y), abs(y - start_y) + 1

    def _catalogue_definition(self, event):
        # Assets belong to this editor's staging folder. A payload alone must
        # never import furniture from another editor or an external program.
        if (self.catalogue_source is None or event.source() is not self.catalogue_source
                or not event.possibleActions() & Qt.DropAction.CopyAction
                or not event.mimeData().hasFormat(FURNITURE_MIME)):
            return None
        payload = bytes(event.mimeData().data(FURNITURE_MIME))
        if len(payload) > 1024:
            return None
        try:
            identity = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return next((item for item in self.draft.data["catalog"]
                     if item["id"] == identity and item.get("footprint")), None)

    def _preview_catalogue_drag(self, event, definition):
        self.catalogue_drag = (definition, 0)
        self.cursor_tile = self._position(event)
        self._update_ghost()
        self.update()

    def dragEnterEvent(self, event):
        dimensions = self._room_drop_dimensions(event)
        if dimensions is not None:
            self._preview_room_catalogue_drag(event, dimensions)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        definition = self._catalogue_definition(event)
        if definition is None:
            event.ignore()
            return
        self._preview_catalogue_drag(event, definition)
        # Accept entry even over a wall so the pointer can reach clear floor.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event):
        dimensions = self._room_drop_dimensions(event)
        if dimensions is not None:
            if self._preview_room_catalogue_drag(event, dimensions):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
            else:
                event.ignore()
            return
        if self.room_catalogue_drag is not None:
            self.clear_room_catalogue_drag()
        definition = self._catalogue_definition(event)
        if definition is None:
            self.clear_catalogue_drag()
            event.ignore()
            return
        self._preview_catalogue_drag(event, definition)
        if self.ghost and self.ghost["valid"]:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.clear_catalogue_drag()
        self.clear_room_catalogue_drag()
        event.accept()

    def dropEvent(self, event):
        dimensions = self._room_drop_dimensions(event)
        if dimensions is not None:
            valid = self._preview_room_catalogue_drag(event, dimensions, force=True)
            x, y = self._room_drop_origin
            self.clear_room_catalogue_drag()
            if valid and self.place_room_drop and self.place_room_drop(x, y, *dimensions):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                self.setFocus()
            else:
                event.ignore()
            return
        if self.room_catalogue_drag is not None:
            self.clear_room_catalogue_drag()
        definition = self._catalogue_definition(event)
        if definition is not None:
            self._preview_catalogue_drag(event, definition)
        valid = definition is not None and self.ghost and self.ghost["valid"]
        x, y = self._position(event)
        self.clear_catalogue_drag()
        # The editor returns success only after the core accepts the edit. No
        # preview or rejected drop changes the draft or its undo/redo stacks.
        if valid and self.place_catalog_drop and self.place_catalog_drop(definition["id"], x, y):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.setFocus()
        else:
            event.ignore()

    def set_room_preview(self, rectangle=None):
        """Highlight a proposed floor rectangle, in tile coordinates."""
        if isinstance(rectangle, dict):
            rectangle = tuple(rectangle[key] for key in ("x", "y", "width", "height"))
        if rectangle is not None and self.room_candidate is not None:
            self._preview_room_edit(None, *rectangle)
            return
        self._room_preview = tuple(rectangle) if rectangle is not None else None
        self._validation_key = None
        if self._room_preview is not None:
            self._set_preview(*self._validate_room(self._room_preview))
        else:
            self._room_candidate_data = None
            self._room_candidate_image = QPixmap()
            self._room_edit_key = None
            self._structure_kind = None
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
                frame = frame_at(definition, placed.get("rotation", 0), self.elapsed_ms,
                                 time_of_day=self.time_of_day, lights_on=self.lights_on)
                sprite_width, sprite_height = frame["rect"][2:]
                dx, dy = frame.get("offset", (0, 0))
                left = placed["x"] + dx / 16
                bottom = placed["y"] + height + dy / 16
                top = bottom - sprite_height / 16
                if left <= x + .5 < left + sprite_width / 16 and top <= y + .5 < bottom:
                    return placed
        return None

    def _room_at(self, x, y):
        room = next((room for room in reversed(self.draft.data["rooms"])
                     if room["enabled"] and room["x"] <= x < room["x"] + room["width"]
                     and room["y"] <= y < room["y"] + room["height"]), None)
        if room is not None and room.get("kind") == "stairway":
            return next((upper for upper in self.draft.data["rooms"]
                         if upper["id"] == room.get("upper_room_id") and upper["enabled"]), None)
        return room

    def room_at(self, x, y, *, include_walls=False):
        """Resolve visible floor before wall strips that can overlap another room."""
        room = self._room_at(x, y)
        if room is not None or not include_walls:
            return room
        return next((room for room in reversed(self.draft.data["rooms"])
                     if room["enabled"] and room.get("kind") != "stairway"
                     and room["x"] <= x < room["x"] + room["width"]
                     and room["y"] - 3 <= y < room["y"]), None)

    def _partition_at(self, x, y):
        active = {room["id"] for room in self.draft.data["rooms"] if room["enabled"]}
        walls = sorted(self.draft.data.get("partitions", []), key=lambda wall: wall["axis"] == "vertical")
        for wall in reversed(walls):
            left, top, width, height = partition_rectangle(wall, visual=True)
            if wall["room_id"] in active and left <= x < left + width and top <= y < top + height:
                return wall
        return None

    def _position(self, event):
        cell = 16 * self.scale
        ox, oy = self.view_origin
        return math.floor(event.position().x() / cell) - ox, math.floor(event.position().y() / cell) - oy

    def _resize_handles(self, data=None):
        data = self.draft.data if data is None else data
        if self.tool != "room-select" or data["kind"] == "spouse":
            return {}
        room = next((room for room in data["rooms"]
                     if room["id"] == self.selected_room_id and room["enabled"]
                     and room.get("kind") != "stairway"), None)
        if room is None:
            return {}
        cell = self.scale * 16
        left, top = room["x"] * cell, room["y"] * cell
        right, bottom = left + room["width"] * cell, top + room["height"] * cell
        middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
        return {"nw": (left, top), "n": (middle_x, top), "ne": (right, top),
                "e": (right, middle_y), "se": (right, bottom), "s": (middle_x, bottom),
                "sw": (left, bottom), "w": (left, middle_y)}

    def _resize_handle_at(self, point):
        if point is not None:
            for handle, (x, y) in self._resize_handles().items():
                if QRect(x - 7, y - 7, 15, 15).contains(point.toPoint() if hasattr(point, "toPoint") else point):
                    return handle
        return None

    @staticmethod
    def _resize_cursor(handle):
        if handle in ("n", "s"):
            return Qt.CursorShape.SizeVerCursor
        if handle in ("e", "w"):
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeFDiagCursor if handle in ("nw", "se") else Qt.CursorShape.SizeBDiagCursor

    def _resized_rectangle(self, point):
        _, handle, x, y, width, height, origin = self._room_resize
        cell = 16 * self.scale
        def tiles(distance):
            return math.floor(abs(distance) / cell + .5) * (1 if distance >= 0 else -1)
        dx, dy = tiles(point.x() - origin.x()), tiles(point.y() - origin.y())
        if "w" in handle:
            x, width = x + dx, width - dx
        elif "e" in handle:
            width += dx
        if "n" in handle:
            y, height = y + dy, height - dy
        elif "s" in handle:
            height += dy
        return x, y, width, height

    def _update_cursor(self):
        if self._architecture_drag:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.tool == "architecture-select" and self.cursor_tile and self._architecture_at(*self.cursor_tile):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._room_resize or self._pending_room_resize:
            self.setCursor(self._resize_cursor((self._room_resize or self._pending_room_resize)[1]))
        elif self.drag or self._room_drag or self._doorway_drag:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.tool == "room-select" and self.cursor_tile and self._doorway_at(*self.cursor_tile):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif (handle := self._resize_handle_at(self.cursor_position)) is not None:
            self.setCursor(self._resize_cursor(handle))
        elif self.tool in ("room-select", "wall-select") and self.cursor_tile and self._partition_at(*self.cursor_tile):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.tool == "room-select" and self.cursor_tile and self._room_at(*self.cursor_tile):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.tool in ("select", "place") and self.cursor_tile and self._at(*self.cursor_tile):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.tool in ("place", "entry", "corridor", "partition", "opening", "room", "architecture-place"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

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
                proposed = normalize_interior(candidate())
                from pixelheart_core.interior_architecture_rules import validate_architecture_rules
                validate_architecture_rules(proposed, before=self.draft.data)
                validate_spouse_access(proposed, before=self.draft.data)
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
                placed = {"id": "__cursor_preview__", "item_id": definition["id"],
                          "x": x, "y": y, "rotation": rotation,
                          "mod_data": deepcopy(definition.get("mod_data", {}))}
                data["furniture"].append(placed)
            validate_furniture_placement(data, placed, definition)
            if self.furniture_validator is not None:
                self.furniture_validator(data)
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
        used.update(item["held_item"]["item_id"] for item in data["furniture"] if "held_item" in item)
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
        held_definition = None
        if self.drag:
            identity, start_x, start_y, original_x, original_y = self.drag
            placed = next((item for item in self.draft.data["furniture"] if item["id"] == identity), None)
            if placed is None:
                self.drag = None
                return
            definition = next(item for item in self.draft.data["catalog"] if item["id"] == placed["item_id"])
            if "held_item" in placed:
                held_definition = next(item for item in self.draft.data["catalog"]
                                       if item["id"] == placed["held_item"]["item_id"])
            rotation = placed.get("rotation", 0)
            x, y = original_x + x - start_x, original_y + y - start_y
        elif self.catalogue_drag:
            definition, rotation = self.catalogue_drag
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
                          held_definition=held_definition, rotation=rotation, valid=valid, message=message)
        self._set_preview(valid, message)

    def _refresh_drag_image(self):
        self._drag_image = QPixmap()
        if not self.drag or self.project_root is None:
            return
        data = self.draft.snapshot()
        data["furniture"] = [item for item in data["furniture"] if item["id"] != self.drag[0]]
        with render_interior(data, self.project_root, self.elapsed_ms, self.grid,
                             time_of_day=self.time_of_day, lights_on=self.lights_on) as image:
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
        cell = 16 * self.scale
        if not self._context_background.isNull():
            painter.drawPixmap(self.rect(), self._context_background)
        ox, oy = self.view_origin
        painter.translate(ox * cell, oy * cell)
        base = self._drag_image if self.drag and not self._drag_image.isNull() else self.image
        shown = self._architecture_candidate_data or self._room_candidate_data or self.draft.data
        if not self._architecture_candidate_image.isNull():
            painter.drawPixmap(QRect(0, 0, shown["width"] * 16 * self.scale,
                                     shown["height"] * 16 * self.scale), self._architecture_candidate_image)
        elif not self._room_candidate_image.isNull():
            painter.drawPixmap(QRect(0, 0, shown["width"] * 16 * self.scale,
                                     shown["height"] * 16 * self.scale), self._room_candidate_image)
        elif not base.isNull():
            painter.drawPixmap(QRect(0, 0, shown["width"] * cell, shown["height"] * cell), base)
        for room in shown["rooms"]:
            if room["id"] == self.selected_room_id or (self.tool in ("room", "room-select") and room["id"] == self._hover_room_id):
                rect = self._draw_outline(painter, tuple(room[key] for key in ("x", "y", "width", "height")),
                                          "#d6bb7d", 12)
                painter.drawText(rect.adjusted(7, 4, -7, -4), Qt.AlignmentFlag.AlignTop, room["name"])
        if self.tool in ("room-select", "wall-select", "corridor", "partition", "opening", "room"):
            wall = next((wall for wall in shown.get("partitions", []) if wall["id"] == self.selected_partition_id), None)
            if wall:
                rectangle = partition_rectangle(wall, visual=True)
                self._draw_outline(painter, rectangle, "#d6bb7d", 16)
        # Arrival is map metadata, not a doorway graphic. The residence's
        # actual opening is already part of the same map image we export.
        if shown["kind"] == "spouse":
            x, y = shown["spouse_stand"]
            rect = QRect(x * cell, y * cell, cell, cell)
            tint = QColor("#dba392")
            tint.setAlpha(145 if self.tool == "spouse_stand" else 75)
            painter.fillRect(rect, tint)
            painter.setPen(QColor("#fffdf5"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♥")
        if self._doorway_preview:
            color = "#81d2a1" if self.preview_valid else "#f38a87"
            self._draw_outline(painter, (*self._doorway_preview, 1, 2), color, 16, dashed=True)
        elif (self.tool == "room-select" and self.cursor_tile
              and self._doorway_at(*self.cursor_tile)):
            self._draw_outline(painter, (*shown["doorway"], 1, 2), "#d6bb7d", 10)
        placed = next((item for item in self.draft.data["furniture"] if item["id"] == self.selected_id), None)
        if placed and not self.drag:
            self._draw_outline(painter, (placed["x"], placed["y"], *self.footprint(placed)), "#edc784", 18)
        if self.ghost:
            ghost = self.ghost
            if self.project_root is not None:
                try:
                    with preview_frame(ghost["definition"], self.project_root, ghost["rotation"], self.elapsed_ms,
                                       time_of_day=self.time_of_day, lights_on=self.lights_on) as sprite:
                        pixmap = QPixmap.fromImage(ImageQt(sprite))
                    dx, dy = preview_frame_offset(ghost["definition"], ghost["rotation"], self.elapsed_ms,
                                                  time_of_day=self.time_of_day, lights_on=self.lights_on)
                    painter.setOpacity(.78)
                    painter.drawPixmap(QRect(ghost["x"] * cell + dx * self.scale,
                                              (ghost["y"] + ghost["height"]) * cell + (dy - pixmap.height()) * self.scale,
                                              pixmap.width() * self.scale, pixmap.height() * self.scale), pixmap)
                    if ghost["held_definition"] is not None:
                        with preview_frame(ghost["held_definition"], self.project_root, 0, self.elapsed_ms,
                                           time_of_day=self.time_of_day, lights_on=self.lights_on) as sprite:
                            dx, dy = held_item_preview_offset(ghost["definition"], ghost["rotation"],
                                                              sprite_size=sprite.size)
                            pixmap = QPixmap.fromImage(ImageQt(sprite))
                        painter.drawPixmap(QRect(ghost["x"] * cell + dx * self.scale,
                                                  ghost["y"] * cell + dy * self.scale,
                                                  pixmap.width() * self.scale, pixmap.height() * self.scale), pixmap)
                except (ValueError, OSError):
                    pass
                finally:
                    painter.setOpacity(1)
            color = "#81d2a1" if ghost["valid"] else "#f38a87"
            self._draw_outline(painter, tuple(ghost[key] for key in ("x", "y", "width", "height")), color, 55)
        if self._room_preview:
            color = "#81d2a1" if self.preview_valid else "#f38a87"
            rect = self._draw_outline(painter, self._room_preview, color, 55, dashed=True)
            caption = ("Wall" if self._structure_kind == "partition" else "Opening" if self._structure_kind == "opening"
                       else f"{self._room_preview[2]} × {self._room_preview[3]}")
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, caption)
        if self.tool in ("architecture-select", "architecture-place"):
            selected_piece = next((piece for piece in self.draft.data.get("architecture", [])
                                   if piece["id"] == self.selected_architecture_id), None)
            if selected_piece is not None and self._architecture_drag is None:
                definition = self._architecture_definition(selected_piece["piece_id"])
                if definition is not None:
                    self._draw_outline(painter, (selected_piece["x"], selected_piece["y"],
                                                  definition["width"], definition["height"]), "#edc784", 18)
            if self._architecture_preview is not None:
                x, y, width, height = self._architecture_preview
                clearance_color = "#7bb8dc" if self.preview_valid else "#f38a87"
                for column, row in sorted(self._architecture_clearance):
                    self._draw_outline(painter, (column, row, 1, 1), clearance_color, 16, dashed=True)
                if self._architecture_candidate_image.isNull() and not self._architecture_ghost_image.isNull():
                    painter.setOpacity(.65)
                    painter.drawPixmap(QRect(x * cell, y * cell, width * cell, height * cell),
                                       self._architecture_ghost_image)
                    painter.setOpacity(1)
                color = "#81d2a1" if self.preview_valid else "#f38a87"
                self._draw_outline(painter, self._architecture_preview, color, 35, dashed=True)
        # The farmhouse's lower Front trim covers the bottom of the insert,
        # including furniture being dragged. It is never part of map exports.
        if not self._context_foreground.isNull():
            painter.drawPixmap(QRect(-ox * cell, -oy * cell, self.width(), self.height()), self._context_foreground)
        painter.setPen(QPen(QColor("#6b8051"), 1))
        painter.setBrush(QColor("#fffdf5"))
        for x, y in self._resize_handles(shown).values():
            painter.drawRect(QRect(x - 4, y - 4, 8, 8))
        painter.end()

    def _cancel(self):
        self.clear_architecture_interaction()
        self.drag = None
        self._pending_move = None
        self._drag_image = QPixmap()
        self._room_start = None
        self._room_preview = None
        self._pending_room_move = None
        self._room_drag = None
        self._pending_room_resize = None
        self._room_resize = None
        self._structure_start = None
        self._structure_kind = None
        self._pending_doorway_move = None
        self._doorway_drag = None
        self._doorway_preview = None
        self.clear_catalogue_drag()
        self.clear_room_catalogue_drag()
        self.clear_placement()
        self.tool = "select"
        self.canceled.emit()
        self._update_cursor()
        self.update()

    def _start_move(self, placed, x, y):
        self.selected_id = placed["id"]
        self.drag = (placed["id"], x, y, placed["x"], placed["y"])
        self.selected_room_id = ""
        self.selected.emit(self.selected_id)
        self._refresh_drag_image()

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
        self.cursor_position = event.position().toPoint()
        if self.draft.data["kind"] == "spouse" and not (0 <= x < 6 and 0 <= y < 9):
            self._set_preview(False, "The farmhouse surroundings are preview only. Decorate inside the room.")
            self.update()
            return
        if self.tool == "architecture-place":
            if self._architecture_placement is not None:
                self._pending_architecture_place = self._architecture_placement["id"]
                self._preview_architecture_edit(self._pending_architecture_place, x, y, force=True)
        elif self.tool == "architecture-select":
            placed = self._architecture_at(x, y)
            self.selected_architecture_id = placed["id"] if placed else ""
            self.selected_id = self.selected_room_id = self.selected_partition_id = ""
            self.architecture_selected.emit(self.selected_architecture_id)
            if placed is not None and self.tool == "architecture-select":
                self._pending_architecture_move = (placed["id"], x, y, placed["x"], placed["y"],
                                                   event.position().toPoint())
        elif self.tool == "room":
            self._room_start = x, y
            self.set_room_preview((x, y, 1, 1))
        elif self.tool in ("corridor", "partition"):
            self._structure_start = self.tool, x, y
            self._preview_structure_edit(self.tool, *self._structure_proposal(self.tool, x, y, x, y))
        elif self.tool == "room-select" and self._doorway_at(x, y):
            self.selected_room_id = ""
            self._pending_doorway_move = (x, y, *self.draft.data["doorway"], event.position().toPoint())
        elif (handle := self._resize_handle_at(event.position())) is not None:
            room = next(room for room in self.draft.data["rooms"] if room["id"] == self.selected_room_id)
            self._pending_room_resize = (room["id"], handle, room["x"], room["y"],
                                         room["width"], room["height"], event.position().toPoint())
        elif self.tool == "wall-select":
            wall = self._partition_at(x, y)
            self.selected_partition_id = wall["id"] if wall else ""
            self.selected_room_id = self.selected_id = ""
            self.partition_selected.emit(self.selected_partition_id)
        elif self.tool == "room-select" and (wall := self._partition_at(x, y)) is not None:
            self.selected_partition_id = wall["id"]
            self.partition_selected.emit(wall["id"])
        elif self.tool in ("select", "room-select"):
            placed = self._at(x, y) if self.tool == "select" else None
            if placed:
                self._start_move(placed, x, y)
            else:
                self.selected_id = ""
                self.selected_partition_id = ""
                self.partition_selected.emit("")
                self.drag = None
                self.selected.emit("")
                room = self._room_at(x, y)
                self.selected_room_id = room["id"] if room else ""
                self.room_selected.emit(self.selected_room_id)
                if room and self.tool == "room-select":
                    self._pending_room_move = (room["id"], x, y, room["x"], room["y"],
                                               room["width"], room["height"], event.position().toPoint())
        elif self.tool == "place" and (placed := self._at(x, y)):
            # A click still places another piece (including a rug underneath),
            # while a deliberate drag moves the piece that was grabbed.
            self._pending_move = (placed["id"], x, y, event.position().toPoint())
        else:
            self.tile_clicked.emit(x, y)
        self._update_ghost()
        self._update_cursor()
        self.update()

    def mouseMoveEvent(self, event):
        self.cursor_tile = x, y = self._position(event)
        self.cursor_position = event.position().toPoint()
        self.hovered.emit(x, y)
        if self._pending_move and event.buttons() & Qt.MouseButton.LeftButton:
            identity, start_x, start_y, point = self._pending_move
            if (event.position().toPoint() - point).manhattanLength() >= QApplication.startDragDistance():
                placed = next((item for item in self.draft.data["furniture"] if item["id"] == identity), None)
                self._cancel()
                self.cursor_tile = x, y
                if placed:
                    self._start_move(placed, start_x, start_y)
        room = self._room_at(x, y)
        self._hover_room_id = room["id"] if room else ""
        if self._pending_doorway_move and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._pending_doorway_move[-1]).manhattanLength() >= QApplication.startDragDistance():
                self._doorway_drag = self._pending_doorway_move[:-1]
                self._pending_doorway_move = None
        if self._pending_room_move and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._pending_room_move[-1]).manhattanLength() >= QApplication.startDragDistance():
                self._room_drag = self._pending_room_move[:-1]
                self._pending_room_move = None
        if self._pending_room_resize and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._pending_room_resize[-1]).manhattanLength() >= QApplication.startDragDistance():
                self._room_resize = self._pending_room_resize
                self._pending_room_resize = None
        if self._pending_architecture_move and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._pending_architecture_move[-1]).manhattanLength() >= QApplication.startDragDistance():
                self._architecture_drag = self._pending_architecture_move[:-1]
                self._pending_architecture_move = None
        if self._architecture_drag or self.tool == "architecture-place":
            self._update_architecture_preview()
        elif self._doorway_drag:
            start_x, start_y, original_x, original_y = self._doorway_drag
            self._preview_doorway_edit(original_x + x - start_x, original_y + y - start_y)
        elif self.tool == "entry":
            self._preview_doorway_edit(x, y)
        elif self.tool == "opening":
            self._preview_structure_edit("opening", x, y, force=True)
        elif self._structure_start:
            kind, start_x, start_y = self._structure_start
            self._preview_structure_edit(kind, *self._structure_proposal(kind, start_x, start_y, x, y))
        elif self._room_resize:
            self._preview_room_edit(self._room_resize[0], *self._resized_rectangle(event.position()), resize=True)
        elif self._room_drag:
            identity, start_x, start_y, original_x, original_y, width, height = self._room_drag
            self._preview_room_edit(identity, original_x + x - start_x, original_y + y - start_y, width, height)
        elif self._room_start:
            start_x, start_y = self._room_start
            self.set_room_preview((min(start_x, x), min(start_y, y), abs(x-start_x)+1, abs(y-start_y)+1))
        else:
            self._update_ghost()
        self._update_cursor()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = self._position(event)
        self.cursor_tile = x, y
        self.cursor_position = event.position().toPoint()
        if self._pending_architecture_place is not None:
            piece_id = self._pending_architecture_place
            self._pending_architecture_place = None
            if (self.tool == "architecture-place" and self._architecture_placement is not None
                    and self._architecture_placement["id"] == piece_id
                    and self._preview_architecture_edit(piece_id, x, y, force=True)):
                self.architecture_placed.emit(piece_id, x, y)
                self._architecture_edit_key = None
                self._update_architecture_preview()
        elif self._pending_architecture_move is not None:
            self._pending_architecture_move = None
        elif self._architecture_drag is not None:
            identity, start_x, start_y, original_x, original_y = self._architecture_drag
            destination = original_x + x - start_x, original_y + y - start_y
            placed = next((piece for piece in self.draft.data.get("architecture", []) if piece["id"] == identity), None)
            valid = (self.tool == "architecture-select" and placed is not None
                     and self._preview_architecture_edit(placed["piece_id"], *destination, identity, force=True))
            self.clear_architecture_interaction()
            if valid and (x, y) != (start_x, start_y):
                self.architecture_moved.emit(identity, *destination)
        elif self._structure_start:
            kind, start_x, start_y = self._structure_start
            proposal = self._structure_proposal(kind, start_x, start_y, x, y)
            valid = self._preview_structure_edit(kind, *proposal, force=True)
            self.clear_room_interaction()
            if valid:
                (self.corridor_drawn if kind == "corridor" else self.partition_drawn).emit(*proposal)
        elif self._pending_doorway_move:
            self._pending_doorway_move = None
        elif self._doorway_drag:
            start_x, start_y, original_x, original_y = self._doorway_drag
            destination = original_x + x - start_x, original_y + y - start_y
            valid = self._preview_doorway_edit(*destination, force=True)
            self.clear_room_interaction()
            if valid and (x, y) != (start_x, start_y):
                self.doorway_moved.emit(*destination)
        elif self._pending_room_resize:
            self._pending_room_resize = None
        elif self._room_resize:
            identity = self._room_resize[0]
            original = self._room_resize[2:6]
            rectangle = self._resized_rectangle(event.position())
            valid = self._preview_room_edit(identity, *rectangle, force=True, resize=True)
            self.clear_room_interaction()
            if valid and rectangle != original:
                self.room_resized.emit(identity, *rectangle)
        elif self._pending_room_move:
            self._pending_room_move = None
        elif self._room_drag:
            identity, start_x, start_y, original_x, original_y, width, height = self._room_drag
            destination = original_x + x - start_x, original_y + y - start_y
            valid = self._preview_room_edit(identity, *destination, width, height, force=True)
            self._room_drag = None
            self.set_room_preview(None)
            if valid and (x, y) != (start_x, start_y):
                self.room_moved.emit(identity, *destination)
        elif self._pending_move:
            _, start_x, start_y, _ = self._pending_move
            self._pending_move = None
            self.tile_clicked.emit(start_x, start_y)
        elif self._room_start:
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
            self._set_preview(True, "")
        self._update_cursor()
        self.update()

    def leaveEvent(self, event):
        if not self.drag and not self._architecture_drag and not self._room_start and not self._structure_start and not self._room_drag and not self._room_resize and not self._doorway_drag:
            self.cursor_tile = None
            self.cursor_position = None
            self.ghost = None
            self._hover_room_id = ""
            self._architecture_preview = None
            self._architecture_clearance = set()
            self._architecture_candidate_data = None
            self._architecture_candidate_image = QPixmap()
            self._architecture_edit_key = None
            if self.tool in ("entry", "opening"):
                self._doorway_preview = None
                self.set_room_preview(None)
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
        else:
            super().keyPressEvent(event)
