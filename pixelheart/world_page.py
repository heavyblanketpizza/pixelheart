"""The loaded NPC’s residence, spouse room, and optional story locations."""
from copy import deepcopy
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QListWidget,
    QSplitter, QCheckBox, QFileDialog, QScrollArea, QSizePolicy,
    QStackedWidget,
)

from pixelheart_core.world import (
    WorldError, new_world, new_location, normalize_world,
    import_map, asset_path, exported_location_id, render_map_preview,
)
from pixelheart_core.projects import new_project
from .editors import line, number, value, set_value, connect_change
from .widgets import label, button, card
from .location_picker import MapSelector


def form_rows(layout, rows):
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for title, widget in rows:
        widget.setAccessibleName(title)
        form.addRow(title, widget)
    layout.addLayout(form)
    return form


def _room_translations(before, after, offsets=None):
    """Identify room moves without treating a resize as a coordinate transform."""
    if isinstance(offsets, dict):
        return [(room, *offsets[room["id"]]) for room in before.get("rooms", [])
                if room["id"] in offsets and any(offsets[room["id"]])]
    following = {room["id"]: room for room in after["rooms"]}
    result = []
    for room in before.get("rooms", []):
        moved = following.get(room["id"])
        if moved and all(moved[key] == room[key] for key in ("width", "height")):
            dx, dy = moved["x"] - room["x"], moved["y"] - room["y"]
            if dx or dy:
                result.append((room, dx, dy))
    return result


def _translate_room_point(x, y, translations):
    px, py = _tile_coordinate(x), _tile_coordinate(y)
    if px is not None and py is not None:
        for room, dx, dy in translations:
            if room["x"] <= px < room["x"] + room["width"] and room["y"] <= py < room["y"] + room["height"]:
                return px + dx, py + dy
    return x, y


def _tile_coordinate(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,6}", value):
        return int(value)
    return None


def _character_room_points(character, maps):
    """Describe authored destinations without changing live editor models."""
    name = character.get("name") or "NPC"
    if character.get("home_map") in maps:
        yield {"x": character.get("home_x"), "y": character.get("home_y"),
               "label": f"{name}'s home position", "home": True}
    for index, stop in enumerate(character.get("schedule", []), 1):
        if isinstance(stop, dict) and stop.get("location") in maps:
            yield {"x": stop.get("x"), "y": stop.get("y"),
                   "label": f"{name}'s daily schedule at {stop.get('time') or 'stop ' + str(index)}"}
    for routine in character.get("life", {}).get("routines", []):
        if not isinstance(routine, dict):
            continue
        for index, stop in enumerate(routine.get("stops", []), 1):
            if isinstance(stop, dict) and stop.get("location") in maps:
                yield {"x": stop.get("x"), "y": stop.get("y"),
                       "label": f"{name}'s routine “{routine.get('name') or 'Untitled'}”, stop {index}"}
    for event in character.get("events", []):
        if isinstance(event, dict) and event.get("location") in maps:
            for actor in event.get("story", {}).get("actors", []):
                if isinstance(actor, dict):
                    yield {"x": actor.get("x"), "y": actor.get("y"),
                           "label": f"{actor.get('name') or name}'s starting position in “{event.get('name') or 'Untitled scene'}”"}


def _translate_character_rooms(character, maps, translations):
    """Keep authored destinations with their rooms, preserving scene move offsets."""
    def move(point, x="x", y="y"):
        before = point.get(x), point.get(y)
        after = _translate_room_point(*before, translations)
        if after != before:
            point[x], point[y] = after
            return True
        return False

    if character.get("home_map") in maps:
        move(character, "home_x", "home_y")
    for stop in character.get("schedule", []):
        if isinstance(stop, dict) and stop.get("location") in maps:
            move(stop)
    for routine in character.get("life", {}).get("routines", []):
        if isinstance(routine, dict):
            for stop in routine.get("stops", []):
                if isinstance(stop, dict) and stop.get("location") in maps:
                    move(stop)
    scenes_moved = False
    for event in character.get("events", []):
        if isinstance(event, dict) and event.get("location") in maps:
            for actor in event.get("story", {}).get("actors", []):
                if isinstance(actor, dict):
                    scenes_moved = move(actor) or scenes_moved
    return scenes_moved


class WorldPage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.world = new_world()
        self.loading = False
        self.location_index = self.dependency_index = -1
        self.detail_stacks = {}
        self.remove_buttons = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)
        self._build_places()
        self._build_dependencies()
        self.tabs.setTabVisible(1, False)
        self.tabs.tabBar().hide()

    def _split_page(self, title, add, remove):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 14, 0, 0)
        row = QHBoxLayout()
        if title == "Add place":
            self.build_home_button = button("Build residence…", self.build_home, "primary")
            self.design_spouse_button = button("Design spouse room…", self.design_spouse_room)
            row.addWidget(self.build_home_button)
            row.addWidget(self.design_spouse_button)
        else:
            row.addWidget(button("+ " + title, add, "primary"))
        remove_button = button("Remove", remove, "quiet")
        remove_button.setEnabled(False)
        row.addWidget(remove_button)
        row.addStretch()
        root.addLayout(row)
        if title == "Add place":
            self.place_advanced_toggle = button("▸ Advanced / Game connection", lambda: None, "quiet")
            self.place_advanced_toggle.setAccessibleName("Advanced / Game connection")
            self.place_advanced_toggle.setCheckable(True)
            self.place_advanced_toggle.toggled.connect(self.show_place_advanced)
            root.addWidget(self.place_advanced_toggle)
            self.place_creation_tools = QWidget()
            creation = QHBoxLayout(self.place_creation_tools)
            creation.setContentsMargins(0, 0, 0, 0)
            creation.addWidget(button("+ Add story location", add))
            creation.addWidget(label("Optional locations for this NPC’s story, plus map connections and mod dependencies.", "hint", True))
            creation.addStretch()
            self.place_creation_tools.hide()
            root.addWidget(self.place_creation_tools)
            self.legacy_characters_button = button("Legacy bundled characters…", self.edit_legacy_characters, "quiet")
            self.legacy_characters_button.hide()
            root.addWidget(self.legacy_characters_button)
        split = QSplitter()
        listing = QListWidget()
        listing.setWordWrap(True)
        listing.setResizeMode(QListWidget.ResizeMode.Adjust)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setMinimumWidth(155)
        listing.setMaximumWidth(220)
        split.addWidget(listing)
        panel = QWidget()
        content = QVBoxLayout(panel)
        content.setContentsMargins(12, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(0)
        scroll.setWidget(panel)
        stack = QStackedWidget()
        stack.addWidget(scroll)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(30, 24, 30, 24)
        empty_layout.addStretch()
        heading, hint = {
            "Add place": ("Make a place that feels like them", "Build this NPC’s residence, or design the room they will bring to the farmhouse."),
            "Add dependency": ("Everything your story needs", "Add a dependency when your mod uses another creator's maps or content."),
        }[title]
        for text, style in ((heading, "sectionTitle"), (hint, "muted")):
            caption = label(text, style, True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(caption)
        empty_layout.addStretch()
        stack.addWidget(empty)
        self.detail_stacks[panel] = stack
        self.remove_buttons[panel] = remove_button
        split.addWidget(stack)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        return page, listing, panel, content

    def _build_places(self):
        page, self.location_list, self.location_panel, layout = self._split_page("Add place", self.add_location, self.remove_location)
        self.location_fields = {"name": line("Their workshop, a cottage, a hidden garden…", 80),
                                "internal_name": line("StableMapID", 40), "spouse_room": QCheckBox("Use this map section as their spouse room"),
                                **{key: number(0, 1000) for key in ("room_x", "room_y", "entry_x", "entry_y", "exit_x", "exit_y")}}
        self.entrance_fields = {"map": MapSelector(compact=True), **{key: number(0, 1000) for key in ("x", "y", "arrival_x", "arrival_y")}}
        details, content = card("A place that belongs to them")
        form_rows(content, [("Place name", self.location_fields["name"])])
        self.interior_button = button("Design interior…", self.design_interior, "primary")
        content.addWidget(self.interior_button)
        self.place_role = label("", "hint", True)
        content.addWidget(self.place_role)
        self.map_status = label("Open the designer to shape and furnish their home.", "hint", True)
        content.addWidget(self.map_status)
        self.interior_notice = label("", "notice", True)
        self.interior_notice.hide()
        content.addWidget(self.interior_notice)
        self.map_preview = label("Their interior preview will appear here.", "muted", True)
        self.map_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.map_preview.setMinimumHeight(150)
        self.map_preview.setMaximumHeight(250)
        self._preview_key = None
        content.addWidget(self.map_preview)
        self.connection_hint = label("When the interior is ready, choose Advanced / Game connection to connect its entrance to the valley.", "hint", True)
        content.addWidget(self.connection_hint)
        layout.addWidget(details)
        self.location_advanced = QWidget()
        advanced_layout = QVBoxLayout(self.location_advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        map_tools, content = card("Map tools", "For imported maps and the game's location settings. Keep stable IDs unchanged after using a pack in a save.")
        form_rows(content, [("Stable map ID", self.location_fields["internal_name"])])
        self.map_identity = label("", "hint", True)
        content.addWidget(self.map_identity)
        content.addWidget(button("Import Tiled map…", self.import_location))
        content.addWidget(button("Create map from a tilesheet…", self.create_map))
        self.edit_map_button = button("Edit painted map…", self.edit_map)
        content.addWidget(self.edit_map_button)
        content.addWidget(label("Imported maps need 16×16 tiles, Back / Buildings / Front layers, and local TSX / PNG files. Collision, actions, and routes need in-game testing.", "hint", True))
        self.assign_home_button = button("Use this story location as their residence", self.assign_home)
        content.addWidget(self.assign_home_button)
        content.addWidget(self.location_fields["spouse_room"])
        advanced_layout.addWidget(map_tools)
        self.warps_card, content = card("Give the player a way in and out", "The entrance and return arrival must be different tiles. Check their walkability in-game.")
        form_rows(content, [("Entrance map", self.entrance_fields["map"]), ("Entrance trigger X", self.entrance_fields["x"]),
                            ("Entrance trigger Y", self.entrance_fields["y"]), ("Arrive inside at X", self.location_fields["entry_x"]),
                            ("Arrive inside at Y", self.location_fields["entry_y"]), ("Exit trigger X", self.location_fields["exit_x"]),
                            ("Exit trigger Y", self.location_fields["exit_y"]), ("Return outside at X", self.entrance_fields["arrival_x"]),
                            ("Return outside at Y", self.entrance_fields["arrival_y"])])
        advanced_layout.addWidget(self.warps_card)
        self.room_card, content = card("Their room in the farmhouse", "Choose the top-left tile of a 6×9 section. The game places it in the farmhouse when the player marries this NPC.")
        form_rows(content, [("Room section X", self.location_fields["room_x"]), ("Room section Y", self.location_fields["room_y"])])
        advanced_layout.addWidget(self.room_card)
        self.location_advanced.hide()
        layout.addWidget(self.location_advanced)
        layout.addStretch()
        self.location_list.currentRowChanged.connect(self.select_location)
        for widget in (*self.location_fields.values(), *self.entrance_fields.values()):
            connect_change(widget, self.edit_location)
        self.tabs.addTab(page, "Residence && spouse room")

    def _build_dependencies(self):
        page, self.dependency_list, self.dependency_panel, layout = self._split_page("Add dependency", self.add_dependency, self.remove_dependency)
        self.dependency_fields = {"id": line("Author.ModName", 192), "minimum_version": line("1.0.0 (optional)", 80), "required": QCheckBox("This mod is required for the pack to work")}
        info, content = card("Tell players what this story needs", "Use the UniqueID from the other mod’s manifest.json. Adding a dependency does not download or bundle another creator’s files.")
        form_rows(content, [("Mod UniqueID", self.dependency_fields["id"]), ("Minimum version", self.dependency_fields["minimum_version"]), ("Required", self.dependency_fields["required"])])
        layout.addWidget(info)
        layout.addStretch()
        self.dependency_list.currentRowChanged.connect(self.select_dependency)
        for widget in self.dependency_fields.values():
            connect_change(widget, self.edit_dependency)
        self.tabs.addTab(page, "Advanced: mod dependencies")

    def load(self, world=None):
        selected_ids = {}
        for collection, index in (("locations", self.location_index), ("dependencies", self.dependency_index)):
            if 0 <= index < len(self.world[collection]):
                selected_ids[collection] = self.world[collection][index]["id"]
        self.loading = True
        self.world = normalize_world(world)
        self._refresh_lists()
        for collection, listing in (("locations", self.location_list), ("dependencies", self.dependency_list)):
            index = next((index for index, entry in enumerate(self.world[collection]) if entry["id"] == selected_ids.get(collection)),
                         0 if self.world[collection] else -1)
            listing.setCurrentRow(index)
        self.loading = False
        self.select_location(self.location_list.currentRow())
        self.select_dependency(self.dependency_list.currentRow())
        self.refresh_home_actions()
        self.legacy_characters_button.setVisible(self.place_advanced_toggle.isChecked() and bool(self.world["characters"]))

    def dump(self):
        return deepcopy(self.world)

    def _refresh_lists(self):
        for listing, collection, text in ((self.location_list, "locations", lambda item: item.get("name") or "Unnamed place"),
                                           (self.dependency_list, "dependencies", lambda item: item.get("id") or "Unnamed dependency")):
            listing.clear()
            listing.addItems([text(item) for item in self.world[collection]])

    def show_place_advanced(self, visible):
        self.place_advanced_toggle.setText(("▾ " if visible else "▸ ") + "Advanced / Game connection")
        self.place_creation_tools.setVisible(visible)
        self.tabs.setTabVisible(1, visible)
        self.tabs.tabBar().setVisible(visible)
        self.legacy_characters_button.setVisible(visible and bool(self.world["characters"]))
        if hasattr(self, "location_advanced"):
            self.location_advanced.setVisible(visible)

    def _character(self):
        character = deepcopy(self.window.document["character"])
        if hasattr(self.window, "identity"):
            character.update(self.window.identity.dump())
        return character

    def _is_residence(self, record):
        character = self._character()
        return (not record["spouse_room"] and character.get("home_map") in
                (record["internal_name"], exported_location_id(record, character)))

    def _spouse_index(self, *, excluding=None):
        return next((index for index, record in enumerate(self.world["locations"])
                     if record["spouse_room"] and record["id"] != excluding), None)

    def refresh_home_actions(self):
        self.build_home_button.setText("Edit residence…" if any(self._is_residence(record)
                                       for record in self.world["locations"]) else "Build residence…")
        self.design_spouse_button.setText("Edit spouse room…" if self._spouse_index() is not None
                                         else "Design spouse room…")

    def build_home(self):
        self._start_interior_place(spouse=False)

    def edit_legacy_characters(self, checked=False, *, index=0):
        if not self.world["characters"]:
            return
        from .legacy_characters import LegacyCharactersDialog
        dialog = LegacyCharactersDialog(self, index=index)
        dialog.exec()
        dialog.deleteLater()

    def design_spouse_room(self):
        self._start_interior_place(spouse=True)

    def _start_interior_place(self, *, spouse):
        existing = (self._spouse_index() if spouse else
                    next((index for index, record in enumerate(self.world["locations"])
                          if self._is_residence(record)), None))
        if existing is not None:
            self.location_list.setCurrentRow(existing)
            self.design_interior()
            return
        if len(self.world["locations"]) >= 32:
            self.window.show_error("Place limit reached", "This project already has 32 places. Edit an existing home or remove a place before adding another.")
            return
        # Save the existing project before adding a pending place. Cancelling
        # either dialog must not leave a new empty record in the saved file.
        if not self.window.ensure_saved():
            return
        previous_id = (self.world["locations"][self.location_index]["id"]
                       if self.location_index >= 0 else None)
        character = self._character()
        name = str(character.get("name", "")).strip()
        base = re.sub(r"[^A-Za-z0-9_]", "", str(character.get("internal_name") or name)) or "NPC"
        if not re.match(r"[A-Za-z]", base):
            base = "NPC" + base
        suffix = "SpouseRoom" if spouse else "Home"
        base = base[:40 - len(suffix)] + suffix
        used = {record["internal_name"].casefold() for record in self.world["locations"]}
        internal, serial = base, 2
        while internal.casefold() in used:
            number_text = str(serial)
            internal = base[:40 - len(number_text)] + number_text
            serial += 1
        record = new_location()
        record.update(name=(name[:60] + "'s " if name else "Their ") + ("spouse room" if spouse else "home"),
                      internal_name=internal, spouse_room=spouse)
        self.world["locations"].append(record)
        self.location_list.addItem(record["name"])
        self.location_list.setCurrentRow(len(self.world["locations"]) - 1)
        if self.design_interior(allow_rebase=True):
            if not spouse:
                self.assign_home()
            return
        # The pending record is private to this interaction; restore the prior
        # selection and leave existing places and residence assignment intact.
        self.world["locations"] = [entry for entry in self.world["locations"] if entry["id"] != record["id"]]
        self.load(self.world)
        self.location_list.setCurrentRow(next((index for index, entry in enumerate(self.world["locations"])
                                              if entry["id"] == previous_id), -1))

    def add_location(self):
        if len(self.world["locations"]) >= 32:
            return
        self.world["locations"].append(new_location())
        self.location_list.addItem("New place")
        self.location_list.setCurrentRow(len(self.world["locations"]) - 1)
        self.changed.emit()

    def remove_location(self):
        if self.location_index >= 0:
            if self._is_residence(self.world["locations"][self.location_index]):
                self._reset_home()
            del self.world["locations"][self.location_index]
            self.load(self.world)
            self.changed.emit()

    def select_location(self, index):
        if self.loading:
            return
        self.location_index = index
        self.interior_notice.hide()
        self.location_panel.setEnabled(index >= 0)
        self.location_list.setVisible(bool(self.world["locations"]))
        self.remove_buttons[self.location_panel].setEnabled(index >= 0)
        self.detail_stacks[self.location_panel].setCurrentIndex(0 if index >= 0 else 1)
        if index < 0:
            return
        self.loading = True
        record = self.world["locations"][index]
        for key, widget in self.location_fields.items():
            set_value(widget, record[key])
        for key, widget in self.entrance_fields.items():
            set_value(widget, record["entrance"][key])
        self.loading = False
        self.refresh_location()

    def edit_location(self):
        if self.loading or self.location_index < 0:
            return
        record = self.world["locations"][self.location_index]
        residence = self._is_residence(record)
        previous_entry = (record["entry_x"], record["entry_y"])
        if self.location_fields["spouse_room"].isChecked() and self._spouse_index(excluding=record["id"]) is not None:
            self.location_fields["spouse_room"].blockSignals(True)
            self.location_fields["spouse_room"].setChecked(False)
            self.location_fields["spouse_room"].blockSignals(False)
        record.update({key: value(widget) for key, widget in self.location_fields.items()})
        record["entrance"].update({key: value(widget) for key, widget in self.entrance_fields.items()})
        if residence:
            if record["spouse_room"]:
                self._reset_home()
            else:
                self._sync_home(record, previous_entry)
        self.location_list.item(self.location_index).setText(record["name"] or "Unnamed place")
        self.refresh_location()
        self.changed.emit()

    def refresh_location(self):
        record = self.world["locations"][self.location_index]
        design = record.get("interior")
        self.edit_map_button.setEnabled(bool(record["map"]) and design is None)
        self.interior_button.setText("Edit interior…" if design else "Design interior…")
        residence = self._is_residence(record)
        self.place_role.setText("Their spouse room in the farmhouse." if record["spouse_room"] else
                                "Their residence. Changes here apply to the NPC in this project." if residence else
                                "An optional location for their story.")
        self.assign_home_button.setVisible(not record["spouse_room"] and not residence)
        self.assign_home_button.setEnabled(bool(design or record["map"]))
        self.location_fields["spouse_room"].setEnabled(design is None and self._spouse_index(excluding=record["id"]) is None)
        self.refresh_home_actions()
        self.warps_card.setVisible(not record["spouse_room"])
        self.room_card.setVisible(record["spouse_room"])
        self.connection_hint.setText("This room joins the farmhouse after marriage. Keep their marked standing spot clear in the designer." if record["spouse_room"] else "When the interior is ready, choose Advanced / Game connection to connect its entrance to the valley.")
        self.map_identity.setText("This map section is placed in FarmHouse; it is not a separate location." if record["spouse_room"] else "Use “" + record["internal_name"] + "” for schedules and story locations. Game map: " + exported_location_id(record, self.window.document["character"]))
        self.map_status.setText(f"{len(design['rooms'])} room(s) · {len(design['furniture'])} furniture item(s)" if design else "Imported map · Open Advanced / Game connection for map tools." if record["map"] else "Open the designer to shape and furnish their home.")
        key = (str(self.window.project_file), record["map"], repr(design))
        if key != self._preview_key:
            self._preview_key = key
            if (design or record["map"]) and self.window.project_file:
                try:
                    from PIL.ImageQt import ImageQt
                    if design:
                        from pixelheart_core.interiors import render_interior
                        preview = render_interior(design, self.window.project_file.parent)
                    else:
                        preview = render_map_preview(asset_path(record["map"], self.window.project_file.parent), 600)
                    pixmap = QPixmap.fromImage(ImageQt(preview))
                    self.map_preview.setPixmap(pixmap.scaled(400, 230, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                except WorldError as exc:
                    self.map_preview.setText("Preview unavailable: " + str(exc))
            else:
                self.map_preview.setText("Their interior preview will appear here.")

    def import_location(self):
        if self.location_index < 0:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import a Tiled map and its local tilesheets", "", "Tiled maps (*.tmx)")
        if not path:
            return
        identity = self.world["locations"][self.location_index]["id"]
        try:
            if not self.window.ensure_saved():
                return
            self.location_list.setCurrentRow(next(index for index, entry in enumerate(self.world["locations"]) if entry["id"] == identity))
            reference = import_map(path, self.window.project_file)
            self.world["locations"][self.location_index]["map"] = reference
            self.world["locations"][self.location_index].pop("interior", None)
            self.refresh_location()
            self.changed.emit()
        except (WorldError, OSError) as exc:
            self.window.show_error("Map import needs attention", str(exc))

    def create_map(self):
        self._paint_map()

    def design_interior(self, checked=False, *, allow_rebase=False):
        if self.location_index < 0:
            return False
        identity = self.world["locations"][self.location_index]["id"]
        if not self.window.ensure_saved():
            return False
        self.location_list.setCurrentRow(next(index for index, item in enumerate(self.world["locations"]) if item["id"] == identity))
        from .interior_editor import InteriorEditor
        from PySide6.QtWidgets import QDialog
        from pixelheart_core.interiors import doorway_exit, reachable_tiles
        record = self.world["locations"][self.location_index]
        accepted = False
        dialog = None
        try:
            initial_design = deepcopy(record.get("interior", {}))
            protected_points = self._protected_room_points(record)
            layout_options = {}
            if protected_points:
                layout_options["validate_layout"] = lambda candidate, offsets: self._validate_room_points(
                    candidate, protected_points, _room_translations(initial_design, candidate, offsets))
            dialog = InteriorEditor(self.window.project_file, record.get("interior"),
                                    "spouse" if record["spouse_room"] else "residence", self,
                                    resident_name=self._character().get("name", ""),
                                    allow_rebase=allow_rebase, **layout_options)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_design:
                previous_entry = (record["entry_x"], record["entry_y"])
                entry = tuple(dialog.result_design["entry"])
                floors = reachable_tiles(dialog.result_design)
                if len(floors) < 2 and not record["spouse_room"]:
                    raise WorldError("Leave a free exit tile reachable from the interior entry.")
                exit_position = (record["exit_x"], record["exit_y"])
                translations = _room_translations(initial_design, dialog.result_design,
                                                  getattr(dialog, "result_room_translations", None))
                self._validate_room_points(dialog.result_design, protected_points, translations)
                if "doorway" in dialog.result_design:
                    exit_position = doorway_exit(dialog.result_design)
                elif not record["spouse_room"]:
                    exit_position = _translate_room_point(*exit_position, translations)
                if not record["spouse_room"] and (exit_position not in floors or exit_position == entry):
                    exit_position = min(floors - {entry}, key=lambda p: (abs(p[0]-entry[0])+abs(p[1]-entry[1]), p[1], p[0]))
                scenes_moved = self._move_authored_room_positions(record, translations) if translations and not record["spouse_room"] else False
                record["interior"] = dialog.result_design
                record["map"] = None
                record["room_x"] = record["room_y"] = 0
                record["entry_x"], record["entry_y"] = dialog.result_design["entry"]
                record["exit_x"], record["exit_y"] = exit_position
                if self._is_residence(record):
                    self._sync_home(record, _translate_room_point(*previous_entry, translations))
                self.select_location(self.location_index)
                if scenes_moved:
                    self.interior_notice.setText("Room moved. Review scene blocking and walking routes.")
                    self.interior_notice.show()
                self.changed.emit()
                accepted = True
        except (ValueError, OSError) as exc:
            self.window.show_error("Interior needs attention", str(exc))
        finally:
            if dialog is not None:
                dialog.deleteLater()
        return accepted

    def _move_authored_room_positions(self, record, translations):
        character = self._live_character()
        before = deepcopy(character)
        maps = {record["internal_name"], exported_location_id(record, character)}
        scenes_moved = _translate_character_rooms(character, maps, translations)
        self.window.document["character"].update(character)
        if (character.get("home_x"), character.get("home_y")) != (before.get("home_x"), before.get("home_y")) and hasattr(self.window, "identity"):
            self.window.identity.load(character)
        for key in ("schedule", "events", "life"):
            if character.get(key) != before.get(key) and hasattr(self.window, key):
                getattr(self.window, key).load(character if key == "life" else character[key])
        for bundled in self.world["characters"]:
            scenes_moved = _translate_character_rooms(bundled["character"], maps, translations) or scenes_moved
        for location in self.world["locations"]:
            entrance = location["entrance"]
            if location is not record and entrance["map"] in maps:
                for x, y in (("x", "y"), ("arrival_x", "arrival_y")):
                    entrance[x], entrance[y] = _translate_room_point(entrance[x], entrance[y], translations)
        return scenes_moved

    def _live_character(self):
        # Read live editors without collecting the world while a new place is
        # pending. Their unsaved content is the next collect()'s source of truth.
        character = self._character()
        for key in ("schedule", "events", "life"):
            if hasattr(self.window, key):
                character[key] = getattr(self.window, key).dump()
        return character

    def _protected_room_points(self, record):
        """Only protect existing reachable points, leaving old drafts repairable."""
        from pixelheart_core.interiors import reachable_tiles
        previous = record.get("interior")
        if previous is None or record["spouse_room"]:
            return []
        reachable = reachable_tiles(previous)
        character = self._live_character()
        maps = {record["internal_name"], exported_location_id(record, character)}
        points = list(_character_room_points(character, maps))
        for point in points:
            if point.pop("home", False) and (point["x"], point["y"]) == (record["entry_x"], record["entry_y"]):
                point["follows_entry"] = True
        for bundled in self.world["characters"]:
            points.extend(_character_room_points(bundled["character"], maps))
        for location in self.world["locations"]:
            entrance = location["entrance"]
            if location is not record and entrance["map"] in maps:
                for x, y, purpose in (("x", "y", "entrance"), ("arrival_x", "arrival_y", "return arrival")):
                    points.append({"x": entrance[x], "y": entrance[y],
                                   "label": f"{location['name'] or 'Another place'}'s {purpose}"})
        protected = []
        for point in points:
            point["x"], point["y"] = _tile_coordinate(point["x"]), _tile_coordinate(point["y"])
            point.pop("home", None)
            if (point["x"], point["y"]) in reachable:
                protected.append(point)
        return protected

    @staticmethod
    def _validate_room_points(design, points, translations):
        from pixelheart_core.interiors import reachable_tiles
        reachable = reachable_tiles(design)
        for point in points:
            position = tuple(design["entry"]) if point.get("follows_entry") else _translate_room_point(point["x"], point["y"], translations)
            if position not in reachable:
                raise WorldError(f"Keep {point['label']} at tile {position[0]}, {position[1]} reachable from the entrance. "
                                 "Move that destination first, or leave a passage to it.")

    def assign_home(self):
        if self.location_index < 0:
            return
        record = self.world["locations"][self.location_index]
        if record["spouse_room"]:
            return
        self._update_home(record["internal_name"], record["entry_x"], record["entry_y"])
        self.refresh_location()
        self.changed.emit()

    def _update_home(self, home_map, home_x, home_y):
        # Update the identity form too: collect() reads it as the source of truth.
        character = self._character()
        character.update(home_map=home_map, home_x=home_x, home_y=home_y)
        self.window.document["character"].update(character)
        if hasattr(self.window, "identity"):
            self.window.identity.load(character)

    def _sync_home(self, record, previous_entry):
        character = self._character()
        standing_tile = (character["home_x"], character["home_y"])
        if standing_tile == previous_entry:
            standing_tile = (record["entry_x"], record["entry_y"])
        self._update_home(record["internal_name"], *standing_tile)

    def _reset_home(self):
        defaults = new_project()["character"]
        self._update_home(defaults["home_map"], defaults["home_x"], defaults["home_y"])

    def edit_map(self):
        self._paint_map(edit=True)

    def _paint_map(self, *, edit=False):
        if self.location_index < 0:
            return
        identity = self.world["locations"][self.location_index]["id"]
        if not self.window.ensure_saved():
            return
        self.location_list.setCurrentRow(next(index for index, entry in enumerate(self.world["locations"]) if entry["id"] == identity))
        from .map_workshop import MapWorkshop
        from PySide6.QtWidgets import QDialog
        dialog = MapWorkshop(self.window.project_file, self)
        record = self.world["locations"][self.location_index]
        try:
            if edit:
                dialog.load_map(record["map"])
            elif record["spouse_room"]:
                dialog.set_preset("spouse_room")
        except WorldError as exc:
            self.window.show_error("Map editing needs attention", str(exc))
            dialog.deleteLater()
            return
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_reference:
            record = self.world["locations"][self.location_index]
            if dialog.result_is_spouse_room and self._spouse_index(excluding=record["id"]) is not None:
                self.window.show_error("Spouse room already exists", "Use Edit spouse room to work on this NPC’s existing room.")
                dialog.deleteLater()
                return
            residence = self._is_residence(record)
            record["map"] = dialog.result_reference
            record.pop("interior", None)
            if dialog.result_is_spouse_room:
                record.update(spouse_room=True, room_x=0, room_y=0)
                if residence:
                    self._reset_home()
            self.select_location(self.location_index)
            self.changed.emit()
        dialog.deleteLater()

    def add_dependency(self):
        if len(self.world["dependencies"]) >= 64:
            return
        self.world["dependencies"].append({"id": "Author.Mod" + str(len(self.world["dependencies"]) + 1), "minimum_version": "", "required": True})
        self.dependency_list.addItem(self.world["dependencies"][-1]["id"])
        self.dependency_list.setCurrentRow(len(self.world["dependencies"]) - 1)
        self.changed.emit()

    def remove_dependency(self):
        if self.dependency_index >= 0:
            del self.world["dependencies"][self.dependency_index]
            self.load(self.world)
            self.changed.emit()

    def select_dependency(self, index):
        if self.loading:
            return
        self.dependency_index = index
        self.dependency_panel.setEnabled(index >= 0)
        self.dependency_list.setVisible(bool(self.world["dependencies"]))
        self.remove_buttons[self.dependency_panel].setEnabled(index >= 0)
        self.detail_stacks[self.dependency_panel].setCurrentIndex(0 if index >= 0 else 1)
        if index < 0:
            return
        self.loading = True
        record = self.world["dependencies"][index]
        for key, widget in self.dependency_fields.items():
            set_value(widget, record.get(key, True if key == "required" else ""))
        self.loading = False

    def edit_dependency(self):
        if self.loading or self.dependency_index < 0:
            return
        record = self.world["dependencies"][self.dependency_index]
        record.update({key: value(widget) for key, widget in self.dependency_fields.items()})
        self.dependency_list.item(self.dependency_index).setText(record["id"] or "Unnamed dependency")
        self.changed.emit()

    def open_issue(self, field):
        parts = field.split(".")
        if parts and parts[0] == "world":
            parts = parts[1:]
        if not parts:
            return
        if parts[0] == "characters":
            self.place_advanced_toggle.setChecked(True)
            self.edit_legacy_characters(index=int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
            return
        controls = {"locations": (0, self.location_list, {**self.location_fields, **self.entrance_fields}),
                    "dependencies": (1, self.dependency_list, self.dependency_fields)}
        if parts[0] in controls:
            tab, listing, fields = controls[parts[0]]
            if parts[0] == "dependencies" or parts[-1] != "name":
                self.place_advanced_toggle.setChecked(True)
            self.tabs.setCurrentIndex(tab)
            if len(parts) > 1 and parts[1].isdigit():
                listing.setCurrentRow(int(parts[1]))
            widget = fields.get(parts[-1])
            if widget:
                widget.setFocus()
