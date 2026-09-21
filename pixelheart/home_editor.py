"""Transactional home assignment and supplied-map design for any authored NPC."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from pixelheart_core.homes import assign_home, home_issues, matching_home_stops, new_home_location
from pixelheart_core.locations import LOCATIONS_BY_ID
from pixelheart_core.world import (
    WorldError, asset_path, exported_location_id, import_map, map_bundle, normalize_world,
)
from .editors import connect_change, line, number, set_value, value
from .game_import import LocalMapSourceWidget, game_source_directory
from .location_picker import MapSelector
from .stage_canvas import StageCanvas
from .widgets import button, card, label


FACINGS = ("up", "right", "down", "left")


class HomeCanvas(StageCanvas):
    """A single resident marker, bounded by known supplied-map dimensions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.references = []
        self.setAccessibleName("Home placement grid. Click a tile or use arrow keys to move the resident.")
        self.setToolTip("Click to place the resident, or focus this grid and use the arrow keys. Blue is the player arrival; amber is the exit.")

    def move_selected(self, x, y):
        if self.map_size:
            x = max(0, min(self.map_size[0] - 1, x))
            y = max(0, min(self.map_size[1] - 1, y))
        super().move_selected(x, y)

    def tile_at(self, position):
        tile = super().tile_at(position)
        if tile and self.map_size and not (0 <= tile[0] < self.map_size[0] and 0 <= tile[1] < self.map_size[1]):
            return None
        return tile

    def geometry_grid(self):
        left, top, scale, _, oy = super().geometry_grid()
        return left, top, scale, (self.width() - self.bounds[2] * scale) / 2, oy

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        _, _, scale, _, _ = self.geometry_grid()
        for name, x, y, color in self.references:
            point = self.tile_point(x, y)
            radius = max(4, min(scale * .43, 15))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawRect(QRectF(point.x() - radius, point.y() - radius, radius * 2, radius * 2))
            painter.drawText(point + QPointF(radius + 3, -radius - 2), name)


def _form(layout, rows):
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for caption, widget in rows:
        widget.setAccessibleName(caption)
        form.addRow(caption, widget)
    layout.addLayout(form)


def _scroll(layout):
    panel = QWidget()
    panel.setLayout(layout)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(panel)
    return scroll


class HomeDialog(QDialog):
    """Apply returns copies; cancel never changes the caller's project records."""

    def __init__(self, character, world=None, primary=None, project_file=None, parent=None, *, ensure_saved=None):
        super().__init__(parent)
        self.character = deepcopy(character)
        self.primary = deepcopy(primary if primary is not None else character)
        self.world = normalize_world(world)
        self.project_file = Path(project_file) if project_file else None
        self.ensure_saved = ensure_saved
        self.result_character = self.result_world = None
        self.loading = True
        self.selected_location_id = None
        self.created_ids = set()
        self.changed_ids = set()
        self.pending_ids = {}
        self._map_key = None
        self._map_error = ""
        self._fit_canvas = True
        self.setWindowTitle("Choose and design a home — Pixelheart")
        self.resize(1000, 850)
        self.setMinimumSize(700, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        name = character.get("name") or "Your character"
        root.addWidget(label(f"A home for {name}", "profileName", True))
        root.addWidget(label("Choose where they start the day, or build a place of their own. Daily routes control where they go afterward.", "muted", True))
        choose = QHBoxLayout()
        self.home_map = MapSelector()
        self.home_map.setAccessibleName("Resident home map")
        choose.addWidget(self.home_map, 1)
        self.create_home_button = button("+ Create a home", self.create_home, "primary")
        choose.addWidget(self.create_home_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(choose)
        self.residents = label("", "hint", True)
        root.addWidget(self.residents)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)
        self._build_placement()
        self._build_design()
        self.status = label("", "notice", True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName("Home assignment status")
        root.addWidget(self.status)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(button("Cancel", self.reject, "quiet"))
        self.apply_button = button("Apply home", self.apply, "primary")
        self.apply_button.setDefault(True)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        self._refresh_selectors()
        self.home_map.set_value(character.get("home_map", "Town"))
        set_value(self.home_x, character.get("home_x", 0))
        set_value(self.home_y, character.get("home_y", 0))
        set_value(self.home_facing, character.get("home_facing", "down"))
        self.home_map.changed.connect(self._select_home)
        for widget in (self.home_x, self.home_y, self.home_facing, self.move_route_stops):
            connect_change(widget, self._placement_changed)
        self.loading = False
        self._select_home()

    def _build_placement(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 14, 0, 0)
        row = QHBoxLayout()
        self.home_x, self.home_y = number(), number()
        self.home_facing = QComboBox()
        for direction in FACINGS:
            self.home_facing.addItem(direction.capitalize(), direction)
        for caption, widget in (("Home tile X", self.home_x), ("Home tile Y", self.home_y), ("Facing", self.home_facing)):
            column = QVBoxLayout()
            column.addWidget(label(caption))
            column.addWidget(widget)
            widget.setAccessibleName(caption)
            row.addLayout(column)
        layout.addLayout(row)
        self.canvas = HomeCanvas()
        self.canvas.actorMoved.connect(self._move_resident)
        layout.addWidget(self.canvas)
        self.preview_hint = label("", "hint", True)
        layout.addWidget(self.preview_hint)
        route, content = card("Bring their route home")
        self.move_route_stops = QCheckBox("Move route stops at the old home")
        self.move_route_stops.setChecked(False)
        content.addWidget(self.move_route_stops)
        paths = matching_home_stops(self.character)
        old = self.character
        count = len(paths)
        self.route_hint = label(
            f"{count} matching stop{'s' if count != 1 else ''} at {old.get('home_map', 'Town')} "
            f"({old.get('home_x', 0)}, {old.get('home_y', 0)}). If selected, stops at exactly this map and tile "
            "in their daily route and life routines move to the new home. Each stop keeps its time, facing, and activity. "
            "Other stops and story scenes stay as authored.", "hint", True,
        )
        content.addWidget(self.route_hint)
        self.move_route_stops.setEnabled(bool(paths))
        layout.addWidget(route)
        layout.addStretch()
        self.tabs.addTab(_scroll(layout), "Home && placement")

    def _build_design(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 14, 0, 0)
        details, content = card("Make this place their own", "Import a Tiled map built from a vanilla interior, or paint with your own tilesheet. Game tilesheet references remain linked to the installed game.")
        self.location_fields = {
            "name": line("A name for their home", 80),
            "internal_name": line("StableHomeID", 40),
            **{key: number() for key in ("entry_x", "entry_y", "exit_x", "exit_y")},
        }
        _form(content, (("Place name", self.location_fields["name"]), ("Stable map ID", self.location_fields["internal_name"])))
        self.identity_hint = label("", "hint", True)
        content.addWidget(self.identity_hint)
        actions = QHBoxLayout()
        self.import_button = button("Import Tiled map…", self.import_location)
        self.paint_button = button("Paint a home…", self.create_map, "primary")
        self.edit_map_button = button("Edit painted map…", self.edit_map)
        for widget in (self.import_button, self.paint_button, self.edit_map_button):
            actions.addWidget(widget)
        content.addLayout(actions)
        self.map_status = label("", "hint", True)
        self.map_status.setTextFormat(Qt.TextFormat.PlainText)
        content.addWidget(self.map_status)
        self.game_source = LocalMapSourceWidget(self)
        self.game_source.changed.connect(self._refresh_game_source)
        content.addWidget(self.game_source)
        layout.addWidget(details)
        entrances, content = card("Connect their front door", "The player’s arrival and exit are separate from the resident’s home tile. Choose a clear path between them.")
        self.entrance_fields = {
            "map": MapSelector(compact=True),
            **{key: number() for key in ("x", "y", "arrival_x", "arrival_y")},
        }
        _form(content, (("Entrance map", self.entrance_fields["map"]),))
        for title, fields, x, y in (
            ("Entrance trigger outside", self.entrance_fields, "x", "y"),
            ("Player arrival inside · blue marker", self.location_fields, "entry_x", "entry_y"),
            ("Exit trigger inside · amber marker", self.location_fields, "exit_x", "exit_y"),
            ("Player return outside", self.entrance_fields, "arrival_x", "arrival_y"),
        ):
            row = QHBoxLayout()
            row.addWidget(label(title), 2)
            for caption, key in (("X", x), ("Y", y)):
                row.addWidget(label(caption))
                fields[key].setAccessibleName(title + " " + caption)
                row.addWidget(fields[key], 1)
            content.addLayout(row)
        content.addWidget(label("Keep arrival tiles different from warp triggers so the player can enter and leave without immediately warping back. Test both directions and the resident’s route in-game.", "hint", True))
        content.addWidget(label("Choose an existing doorway or path tile outside. This connection does not add exterior building artwork; supply that separately in your maps.", "hint", True))
        layout.addWidget(entrances)
        layout.addStretch()
        self.tabs.addTab(_scroll(layout), "Design this place")
        for widget in (*self.location_fields.values(), *self.entrance_fields.values()):
            connect_change(widget, self._edit_location)

    def _locations(self):
        return [place for place in self.world["locations"] if not place.get("spouse_room")]

    def select_home_map(self, map_id):
        """Open this editor at a requested place, preserving the resident's tile."""
        self.home_map.set_value(map_id)
        self._select_home()

    def _aliases(self, location):
        return (location["internal_name"], exported_location_id(location, self.primary))

    def _location(self):
        return next((place for place in self.world["locations"] if place["id"] == self.selected_location_id), None)

    def _refresh_selectors(self):
        places = [(place["internal_name"], place["name"] or "Unnamed home") for place in self._locations()]
        self.home_map.set_extra_locations(places)
        self.entrance_fields["map"].set_extra_locations(places)
        self.create_home_button.setEnabled(len(self.world["locations"]) < 32)

    def _select_home(self):
        if self.loading:
            return
        selected = next((place for place in self._locations() if self.home_map.value() in self._aliases(place)), None)
        self.selected_location_id = selected["id"] if selected else None
        self._fit_canvas = True
        self.tabs.setTabEnabled(1, selected is not None)
        if selected is None and self.tabs.currentIndex() == 1:
            self.tabs.setCurrentIndex(0)
        if selected:
            self.loading = True
            for key, widget in self.location_fields.items():
                set_value(widget, self.pending_ids.get(selected["id"], selected[key]) if key == "internal_name" else selected[key])
            for key, widget in self.entrance_fields.items():
                set_value(widget, selected["entrance"][key])
            new = selected["id"] in self.created_ids
            self.location_fields["internal_name"].setReadOnly(not new)
            self.identity_hint.setText(
                "Choose a stable ID before applying. Keep it unchanged once this place is used in a save."
                if new else "This existing map ID stays stable so saved routes and scenes keep finding this place."
            )
            self.loading = False
        self._refresh_map()
        self._placement_changed()

    def _refresh_game_source(self):
        self._refresh_map(refresh=True)
        self._placement_changed()

    def _refresh_map(self, *, refresh=False):
        location = self._location()
        key = (str(self.project_file), location.get("map") if location else None, self.home_map.value(), game_source_directory())
        if refresh or key != self._map_key:
            self._map_key = key
            self._map_error = ""
            path = None
            if location and location.get("map") and self.project_file:
                try:
                    path = asset_path(location["map"], self.project_file.parent)
                except WorldError as exc:
                    self._map_error = str(exc)
            self.canvas.set_map(path, refresh=refresh)
            self.game_source.set_assets(self.canvas.game_assets)
        can_supply = bool(self.project_file or self.ensure_saved)
        self.import_button.setEnabled(bool(location) and can_supply)
        self.paint_button.setEnabled(bool(location) and can_supply)
        self.edit_map_button.setEnabled(bool(location and location.get("map")) and can_supply)
        if location:
            status = ("Supplied map: " + location["map"]) if location.get("map") else "Draft home · paint or import a map before exporting this place."
            if not self.project_file:
                status += " Save the project to keep its map and tilesheet together."
            if self._map_error:
                status += " " + self._map_error
            if self.canvas.preview_error:
                status += " Preview unavailable: " + self.canvas.preview_error
            self.map_status.setText(status)
        if self.canvas.map_size:
            width, height = self.canvas.map_size
            self.canvas.bounds = (0, 0, width, height)
            if self.canvas.preview_error:
                self.preview_hint.setText(f"{width} × {height} tiles · coordinate grid only. {self.canvas.preview_error}")
            else:
                self.canvas.preview_note = "Map artwork · click to place the resident"
                self.preview_hint.setText(f"{width} × {height} tiles · X 0–{width - 1}, Y 0–{height - 1}. Blue: player arrival. Amber: exit. Verify collision and routes in-game.")
        else:
            if self.canvas.preview_error:
                self.preview_hint.setText("Coordinate grid only. " + self.canvas.preview_error)
            else:
                self.canvas.preview_note = "Placement grid · no map artwork loaded"
                self.preview_hint.setText("This grid shows coordinates only. Vanilla and other mods’ map artwork is not bundled; verify the tile and access in your game.")

    def _placement_changed(self):
        if self.loading:
            return
        left, top, width, height = self.canvas.bounds
        x, y = self.home_x.value(), self.home_y.value()
        outside = not (left <= x < left + width and top <= y < top + height)
        self.canvas.load([{
            "name": self.character.get("name") or "Resident", "x": self.home_x.value(),
            "y": self.home_y.value(), "facing": FACINGS.index(self.home_facing.currentData()),
        }], fit=not self.canvas.map_size and (self._fit_canvas or outside))
        self._fit_canvas = False
        location = self._location()
        self.canvas.references = (
            [("In", location["entry_x"], location["entry_y"], "#2b6f96"),
             ("Out", location["exit_x"], location["exit_y"], "#a76824")] if location else []
        )
        self._refresh_residents()
        issues = self._issues()
        errors = [item for item in issues if item["level"] == "error"]
        self.apply_button.setEnabled(not errors)
        if errors:
            self.status.setText("\n".join(item["message"] for item in errors[:3]))
        elif issues:
            self.status.setText("\n".join(item["message"] for item in issues[:2]))
        else:
            self.status.setText("Ready to apply. Check that this home tile is walkable and their route can reach it in-game.")

    def _refresh_residents(self):
        selected = self._location()
        aliases = self._aliases(selected) if selected else (self.home_map.value(),)
        current_id = self.character.get("id")
        others = [self.primary, *(entry["character"] for entry in self.world["characters"])]
        names = [entry.get("name") or "Unnamed resident" for entry in others
                 if entry.get("id") != current_id and entry.get("home_map") in aliases]
        self.residents.setText("Assigned resident: " + (self.character.get("name") or "Your character")
                              + (" · Also living here: " + ", ".join(names) if names else ""))

    def _move_resident(self, _index, x, y):
        self.loading = True
        self.home_x.setValue(x)
        self.home_y.setValue(y)
        self.loading = False
        self._placement_changed()

    def _edit_location(self):
        if self.loading or not (location := self._location()):
            return
        old_alias = location["internal_name"]
        self.changed_ids.add(location["id"])
        location.update({key: value(widget) for key, widget in self.location_fields.items() if key != "internal_name"})
        location["entrance"].update({key: value(widget) for key, widget in self.entrance_fields.items()})
        requested = self.location_fields["internal_name"].text()
        if location["id"] in self.created_ids:
            self.pending_ids[location["id"]] = requested
            if not self._identity_error(location, requested):
                location["internal_name"] = requested
        if old_alias != location["internal_name"]:
            # Only newly created, unpublished IDs are editable in this dialog.
            for place in self.world["locations"]:
                if place["entrance"]["map"] == old_alias:
                    place["entrance"]["map"] = location["internal_name"]
            self.entrance_fields["map"].set_value(location["entrance"]["map"])
            self.home_map.set_value(location["internal_name"])
        self._refresh_selectors()
        self._refresh_map()
        self._placement_changed()

    def _candidate(self):
        return assign_home(self.character, self.home_map.value(), self.home_x.value(), self.home_y.value(),
                           self.home_facing.currentData(), move_route_stops=self.move_route_stops.isChecked())

    def _issues(self):
        try:
            candidate = self._candidate()
        except (ValueError, TypeError) as exc:
            return [{"level": "error", "message": str(exc)}]
        issues = home_issues(candidate, self.world, self.project_file.parent if self.project_file else None, self.primary)
        selected = self._location()
        for location in self.world["locations"]:
            if location["id"] in self.changed_ids | self.created_ids or location is selected:
                for issue in self._place_issues(location):
                    if location is not selected:
                        issue["message"] = (location["name"] or location["internal_name"]) + ": " + issue["message"]
                    issues.append(issue)
        return issues

    def _identity_error(self, location, internal):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", internal):
            return "Use a stable map ID starting with a letter and containing up to 40 letters, numbers, or underscores."
        if internal.casefold() in {name.casefold() for name in LOCATIONS_BY_ID} or any(
                place["id"] != location["id"] and place["internal_name"].casefold() == internal.casefold()
                for place in self.world["locations"]):
            return "Choose a map ID different from every vanilla location and other project place."
        return None

    def _place_issues(self, location):
        """Check edits made here while letting a home without artwork remain a draft."""
        issues = []
        def add(message):
            issues.append({"level": "error", "message": message})
        internal = self.pending_ids.get(location["id"], location["internal_name"])
        if error := self._identity_error(location, internal):
            add(error)
        entrance = location["entrance"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,191}", entrance["map"]):
            add("Choose a valid map for the outside entrance.")
        if (location["entry_x"], location["entry_y"]) == (location["exit_x"], location["exit_y"]):
            add("Give the player different arrival and exit tiles inside the home.")
        if location.get("entrance_mode", "walk") == "walk" and (entrance["x"], entrance["y"]) == (entrance["arrival_x"], entrance["arrival_y"]):
            add("The return outside must use a different tile from the entrance trigger.")
        dimensions = self.canvas.map_size if location is self._location() else None
        if not dimensions and location.get("map") and self.project_file:
            try:
                bundle = map_bundle(asset_path(location["map"], self.project_file.parent))
                dimensions = bundle["width"], bundle["height"]
            except (WorldError, OSError):
                pass
        if dimensions:
            width, height = dimensions
            if any(location[x] >= width or location[y] >= height for x, y in (("entry_x", "entry_y"), ("exit_x", "exit_y"))):
                add(f"Player arrival and exit must fit inside this {width} × {height} map.")
        lookup = {alias: place for place in self.world["locations"] for alias in self._aliases(place)}
        visited = {location["id"]}
        cursor = entrance["map"]
        while cursor in lookup:
            linked = lookup[cursor]
            if linked["id"] in visited or linked.get("spouse_room"):
                add("Connect this home to an outside map that can be reached; its entrance cannot loop back or use a spouse-room section.")
                break
            visited.add(linked["id"])
            cursor = linked["entrance"]["map"]
        for place in self._locations():
            other = place["entrance"]
            if place["id"] != location["id"] and (other["map"], other["x"], other["y"]) == (entrance["map"], entrance["x"], entrance["y"]):
                add("Another project place uses this entrance trigger. Choose a different outside tile.")
                break
        linked = lookup.get(entrance["map"])
        if linked and linked.get("map") and self.project_file:
            try:
                bundle = map_bundle(asset_path(linked["map"], self.project_file.parent))
                if any(entrance[x] >= bundle["width"] or entrance[y] >= bundle["height"] for x, y in (("x", "y"), ("arrival_x", "arrival_y"))):
                    add("The entrance and return tiles must fit inside the connected outside map.")
            except (WorldError, OSError):
                pass  # The linked location's existing asset issue is reported by project checks.
        return issues

    def create_home(self):
        if len(self.world["locations"]) >= 32:
            return
        location = new_home_location(self.character, self.world)
        self.world["locations"].append(location)
        self.created_ids.add(location["id"])
        self.loading = True
        self._refresh_selectors()
        self.home_map.set_value(location["internal_name"])
        self.home_x.setValue(6)
        self.home_y.setValue(6)
        self.loading = False
        self._select_home()
        self.tabs.setCurrentIndex(1)

    def _saved_project(self):
        if self.project_file:
            return True
        if self.ensure_saved:
            path = self.ensure_saved()
            if path:
                self.project_file = Path(path)
                self._refresh_map()
                return True
        self.map_status.setText("Save the project before adding a map so its tilesheets stay with it.")
        return False

    def import_location(self):
        if not (location := self._location()) or not self._saved_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import a home with game or custom tilesheets", "", "Tiled maps (*.tmx)")
        if not path:
            return
        try:
            location["map"] = import_map(path, self.project_file)
            self.changed_ids.add(location["id"])
            self._refresh_map()
            self._placement_changed()
        except (WorldError, OSError) as exc:
            self.map_status.setText("Map import needs attention: " + str(exc))

    def create_map(self):
        self._paint_map()

    def edit_map(self):
        self._paint_map(edit=True)

    def _paint_map(self, *, edit=False):
        if not (location := self._location()) or not self._saved_project():
            return
        from .map_workshop import MapWorkshop
        dialog = MapWorkshop(self.project_file, self)
        try:
            if edit:
                dialog.load_map(location["map"])
            else:
                dialog.set_preset("home_interior")
            spouse_index = dialog.preset.findData("spouse_room")
            if spouse_index >= 0:
                dialog.preset.model().item(spouse_index).setEnabled(False)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_reference:
                if dialog.result_is_spouse_room:
                    self.map_status.setText("A home needs a standalone map. Use the home interior or small location preset.")
                    return
                location["map"] = dialog.result_reference
                self.changed_ids.add(location["id"])
                self._refresh_map()
                self._placement_changed()
        except (WorldError, OSError) as exc:
            self.map_status.setText("Map editing needs attention: " + str(exc))
        finally:
            dialog.deleteLater()

    def apply(self):
        self._placement_changed()
        if not self.apply_button.isEnabled():
            return
        self.result_character = self._candidate()
        self.result_world = deepcopy(self.world)
        self.accept()
