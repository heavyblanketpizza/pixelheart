"""Direct interior workspace with project map settings kept out of the canvas."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QListWidget,
    QSplitter, QCheckBox, QFileDialog, QScrollArea, QSizePolicy,
    QStackedWidget, QLabel, QDialog, QTabBar,
)

from pixelheart_core.world import (
    WorldError, new_world, new_location, normalize_world,
    import_map, asset_path, exported_location_id, render_map_preview,
)
from pixelheart_core.projects import new_project
from .editors import line, number, value, set_value, connect_change
from .widgets import label, button, card
from .location_picker import MapSelector


class PlacePreview(QLabel):
    """Fit the complete home preview even when a small window shrinks its card."""

    def setPixmap(self, pixmap):
        self._source = pixmap
        self._fit()

    def setText(self, text):
        self._source = None
        super().setText(text)

    def _fit(self):
        source = getattr(self, "_source", None)
        if source is not None and not source.isNull():
            super().setPixmap(source.scaled(max(1, self.width()), max(1, self.height()),
                                          Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.FastTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


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
    draft_changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.world = new_world()
        self.loading = False
        self.location_index = self.dependency_index = -1
        self.detail_stacks = {}
        self.remove_buttons = {}
        self._removed_places = []
        self.interior_editor = None
        self._draft_project = None
        self._editor_record_id = None
        self._editor_baseline = None
        self._room_kind = "residence"
        self._room_views = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.home_switch = QTabBar()
        self.home_switch.setAccessibleName("Home workspace")
        self.home_switch.addTab("Rooms")
        self.home_switch.addTab("Catalogue development")
        self.home_switch.setExpanding(False)
        root.addWidget(self.home_switch)
        self.home_stack = QStackedWidget()
        root.addWidget(self.home_stack, 1)
        rooms = QWidget()
        room_layout = QVBoxLayout(rooms)
        room_layout.setContentsMargins(0, 0, 0, 0)
        room_layout.setSpacing(10)
        self.home_stack.addWidget(rooms)
        from .catalogue_workshop import CatalogueWorkshop
        self.catalogue_workshop = CatalogueWorkshop()
        self.home_stack.addWidget(self.catalogue_workshop)
        self.home_switch.currentChanged.connect(self.switch_home_workspace)
        switch = QHBoxLayout()
        self.room_switch = QTabBar()
        self.room_switch.setAccessibleName("Interior to edit")
        self.room_switch.addTab("Pre-spouse residence")
        self.room_switch.addTab("Spouse room")
        self.room_switch.setExpanding(False)
        self.room_switch.currentChanged.connect(self.switch_room)
        switch.addWidget(self.room_switch)
        switch.addStretch()
        self.entrance_action = button("Connect entrance…", lambda: self.open_settings(connection=True), "quiet")
        switch.addWidget(self.entrance_action)
        self.settings_button = button("Room settings…", self.open_settings, "quiet")
        switch.addWidget(self.settings_button)
        room_layout.addLayout(switch)
        self.room_hint = label("Their home before marriage. Shape the rooms and make it their own.", "hint", True)
        room_layout.addWidget(self.room_hint)
        self.editor_host = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        room_layout.addWidget(self.editor_host, 1)

        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("Home settings — Pixelheart")
        self.settings_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.settings_dialog.resize(860, 720)
        settings = QVBoxLayout(self.settings_dialog)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        settings.addWidget(self.tabs, 1)
        settings.addWidget(button("Back to decorating", self.settings_dialog.accept, "primary"))
        self.settings_dialog.finished.connect(lambda *_: self.activate_workspace() if self.isVisible() else None)
        self._build_places()
        self._build_dependencies()
        self.tabs.setTabVisible(1, False)
        self.tabs.tabBar().hide()

    @property
    def draft_project_file(self):
        return Path(self._draft_project.name) / "character.json" if self._draft_project else None

    def _interior_project_file(self):
        if self.window.project_file:
            return self.window.project_file
        if self._draft_project is None:
            self._draft_project = tempfile.TemporaryDirectory(prefix="pixelheart-home-")
        return self.draft_project_file

    def dispose_editor(self):
        if self.interior_editor is not None:
            editor = self.interior_editor
            self._room_views[editor.draft.data["kind"]] = {
                "tab": editor.tabs.currentIndex(), "zoom": editor.zoom.currentIndex(),
                "auto_fit": editor._auto_fit, "grid": editor.grid.isChecked(),
                "time": editor.preview_time.currentIndex(), "lights": editor.preview_lights.currentIndex(),
            }
            self.interior_editor.dispose()
            self.editor_layout.removeWidget(self.interior_editor)
            self.interior_editor.hide()
            self.interior_editor.deleteLater()
            self.interior_editor = None
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._editor_record_id = self._editor_baseline = None

    def reset_workspace(self):
        self.settings_dialog.hide()
        self.catalogue_workshop.stop()
        self.home_switch.blockSignals(True)
        self.home_switch.setCurrentIndex(0)
        self.home_switch.blockSignals(False)
        self.home_stack.setCurrentIndex(0)
        self.dispose_editor()
        self._room_views.clear()
        if self._draft_project is not None:
            self._draft_project.cleanup()
            self._draft_project = None
        self._room_kind = "residence"
        self.room_switch.blockSignals(True)
        self.room_switch.setCurrentIndex(0)
        self.room_switch.blockSignals(False)

    def _role_index(self):
        return (self._spouse_index() if self._room_kind == "spouse" else
                next((index for index, record in enumerate(self.world["locations"])
                      if self._is_residence(record)), None))

    def activate_workspace(self):
        if self.home_switch.currentIndex() == 1:
            self.catalogue_workshop.refresh(self.window.project_file)
            return
        if self.settings_dialog.isVisible():
            return
        if self.interior_editor is not None:
            return
        self.dispose_editor()
        index = self._role_index()
        record = self.world["locations"][index] if index is not None else None
        if index is not None:
            self.location_list.setCurrentRow(index)
            self.select_location(index)
        spouse = self._room_kind == "spouse"
        self.room_hint.setText("Their room in the farmhouse after marriage. Keep a path to the heart clear." if spouse else
                               "Their home before marriage. Shape the rooms and make it their own.")
        self.entrance_action.setVisible(not spouse)
        self.entrance_action.setText("Entrance…" if record and record["entrance"].get("confirmed", True) else "Connect entrance…")
        if record and record["map"] and not record.get("interior"):
            # Imported TMX is not a structured interior. Never replace it just
            # because its role is selected in the decorating workspace.
            self.editor_layout.addWidget(label(record["name"] or "Imported interior", "profileName"))
            self.editor_layout.addWidget(label("This home uses an imported map. Open its map editor to keep working on the original layout.", "muted", True))
            self.editor_layout.addWidget(button("Edit imported map…", self.edit_imported_interior, "primary"))
            self.editor_layout.addStretch()
            return
        try:
            from .interior_editor import InteriorEditor
            initial = deepcopy(record.get("interior", {})) if record else {}
            protected = self._protected_room_points(record) if record else []
            options = {}
            if protected:
                options["validate_layout"] = lambda candidate, offsets: self._validate_room_points(
                    candidate, protected, _room_translations(initial, candidate, offsets))
            editor = InteriorEditor(self._interior_project_file(), initial or None, self._room_kind,
                                    self.editor_host, resident_name=self._character().get("name", ""),
                                    allow_rebase=record is None, embedded=True, **options)
            self.interior_editor = editor
            self._editor_record_id = record["id"] if record else None
            self._editor_initial_design = initial
            self._editor_baseline = editor.draft.snapshot()
            editor.draft_changed.connect(self.draft_changed)
            self.editor_layout.addWidget(editor, 1)
            view = self._room_views.get(self._room_kind)
            if view:
                editor.tabs.setCurrentIndex(view["tab"])
                editor.grid.setChecked(view["grid"])
                editor.preview_time.setCurrentIndex(view["time"])
                editor.preview_lights.setCurrentIndex(view["lights"])
                if not view["auto_fit"]:
                    editor.zoom.setCurrentIndex(view["zoom"])
                    editor._auto_fit = False
            editor.show()
        except (ValueError, OSError) as exc:
            self.editor_layout.addWidget(label("This interior needs attention: " + str(exc), "notice", True))
            self.editor_layout.addWidget(button("Open room settings…", self.open_settings))
            self.editor_layout.addStretch()

    def switch_home_workspace(self, index):
        if not self.flush_editor():
            self.home_switch.blockSignals(True)
            self.home_switch.setCurrentIndex(self.home_stack.currentIndex())
            self.home_switch.blockSignals(False)
            return
        self.catalogue_workshop.stop()
        self.home_stack.setCurrentIndex(index)
        self.activate_workspace()

    def switch_room(self, index):
        if not self.flush_editor():
            self.room_switch.blockSignals(True)
            self.room_switch.setCurrentIndex(1 if self._room_kind == "spouse" else 0)
            self.room_switch.blockSignals(False)
            return
        self._room_kind = "spouse" if index == 1 else "residence"
        self.activate_workspace()

    def flush_editor(self, *, force=False):
        """Apply the visible draft once before saving or changing context."""
        editor = self.interior_editor
        if editor is None:
            return True
        if not force and editor.draft.data == self._editor_baseline:
            self.dispose_editor()
            return True
        index = next((i for i, record in enumerate(self.world["locations"])
                      if record["id"] == self._editor_record_id), None)
        if self._editor_record_id and index is None:
            editor.notice("This room was removed. Reopen Home to choose a room.")
            return False
        if index is None and len(self.world["locations"]) >= 32:
            editor.notice("This project already has 32 places. Remove an unused imported place in Room settings first.")
            return False
        if not editor.prepare_design(self._interior_project_file()):
            return False
        pending = index is None
        record = self._new_interior_record(spouse=self._room_kind == "spouse") if pending else self.world["locations"][index]
        try:
            self._apply_interior_result(record, editor.result_design, self._editor_initial_design,
                                        editor.result_room_translations)
        except (ValueError, OSError) as exc:
            editor.notice(str(exc))
            return False
        if pending:
            self.world["locations"].append(record)
            index = len(self.world["locations"]) - 1
            self.location_list.addItem(record["name"])
            if not record["spouse_room"]:
                self._update_home(record["internal_name"], record["entry_x"], record["entry_y"])
        self.dispose_editor()
        self.location_list.setCurrentRow(index)
        self.select_location(index)
        self.changed.emit()
        return True

    def open_settings(self, checked=False, *, connection=False):
        if not self.flush_editor(force=True):
            return
        index = self._role_index()
        if index is not None:
            self.location_list.setCurrentRow(index)
            self.select_location(index)
        self.tabs.setCurrentIndex(0)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        if connection:
            self.connection_button.setChecked(True)

    def edit_imported_interior(self):
        self.edit_map()
        self.activate_workspace()

    def _split_page(self, title, add, remove):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 14, 0, 0)
        row = QHBoxLayout()
        if title == "Add place":
            self.build_home_button = button("Build residence…", self.build_home, "primary")
            self.design_spouse_button = button("Design spouse room…", self.design_spouse_room)
            self.build_home_button.setParent(page)
            self.design_spouse_button.setParent(page)
            self.build_home_button.hide()
            self.design_spouse_button.hide()
            self.story_place_button = button("+ Story place", add, "quiet")
            self.story_place_button.setParent(page)
            self.story_place_button.hide()
            self.undo_remove_button = button("Undo removal", self.undo_remove_location, "quiet")
            self.undo_remove_button.hide()
            row.addWidget(self.undo_remove_button)
        else:
            row.addWidget(button("+ " + title, add, "primary"))
        remove_button = button("Remove place" if title == "Add place" else "Remove", remove, "quiet")
        remove_button.setEnabled(False)
        if title == "Add place":
            remove_button.hide()
        row.addWidget(remove_button)
        row.addStretch()
        root.addLayout(row)
        if title == "Add place":
            self.place_advanced_toggle = button("▸ Map tools && dependencies", lambda: None, "quiet")
            self.place_advanced_toggle.setAccessibleName("Map tools and dependencies")
            self.place_advanced_toggle.setCheckable(True)
            self.place_advanced_toggle.toggled.connect(self.show_place_advanced)
            root.addWidget(self.place_advanced_toggle)
            self.legacy_characters_button = button("Legacy bundled characters…", self.edit_legacy_characters, "quiet")
            self.legacy_characters_button.hide()
            root.addWidget(self.legacy_characters_button)
        split = QSplitter()
        listing = QListWidget()
        listing.setWordWrap(True)
        listing.setResizeMode(QListWidget.ResizeMode.Adjust)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setMinimumWidth(140)
        listing.setMaximumWidth(180)
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
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 900])
        root.addWidget(split, 1)
        return page, listing, panel, content

    def _build_places(self):
        page, self.location_list, self.location_panel, layout = self._split_page("Add place", self.add_location, self.remove_location)
        self.location_fields = {"name": line("Their workshop, a cottage, a hidden garden…", 80),
                                "internal_name": line("StableMapID", 40), "spouse_room": QCheckBox("Use this map section as their spouse room"),
                                **{key: number(0, 1000) for key in ("room_x", "room_y", "entry_x", "entry_y", "exit_x", "exit_y")}}
        self.entrance_fields = {"map": MapSelector(compact=True), **{key: number(0, 1000) for key in ("x", "y", "arrival_x", "arrival_y")}}
        details, content = card()
        content.setContentsMargins(16, 14, 16, 14)
        content.setSpacing(10)
        form_rows(content, [("Place name", self.location_fields["name"])])
        self.readiness = label("", "notice", True)
        content.addWidget(self.readiness)
        self.interior_button = button("Design interior…", self.design_interior, "primary")
        actions = QHBoxLayout()
        actions.addWidget(self.interior_button, 1)
        self.connection_button = button("Connect entrance…", lambda: None)
        self.connection_button.setCheckable(True)
        self.connection_button.toggled.connect(self.show_connection)
        actions.addWidget(self.connection_button)
        content.addLayout(actions)
        self.place_role = label("", "hint", True)
        content.addWidget(self.place_role)
        self.map_status = label("Open the designer to shape and furnish their home.", "hint", True)
        content.addWidget(self.map_status)
        self.interior_notice = label("", "notice", True)
        self.interior_notice.hide()
        content.addWidget(self.interior_notice)
        self.map_preview = PlacePreview("Their interior preview will appear here.")
        self.map_preview.setObjectName("muted")
        self.map_preview.setWordWrap(True)
        self.map_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.map_preview.setMinimumHeight(150)
        self.map_preview.setMaximumHeight(210)
        self._preview_key = None
        content.addWidget(self.map_preview)
        self.connection_hint = label("Choose where the player enters this place.", "hint", True)
        content.addWidget(self.connection_hint)
        # The room itself is already open in Home. Settings only contains the
        # name, connection and compatibility controls, without another preview.
        for widget in (self.interior_button, self.map_preview, self.map_status, self.place_role):
            widget.hide()
        layout.addWidget(details)
        self.warps_card, connection = card("Connect the entrance", "Choose an outside trigger and a separate return tile. Check both in the game.")
        connection.setContentsMargins(16, 14, 16, 14)
        connection.setSpacing(10)
        def coordinates(fields, x, y):
            pair = QWidget()
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            for axis, key in (("X", x), ("Y", y)):
                row.addWidget(label(axis, "hint"))
                fields[key].setAccessibleName(key.replace("_", " "))
                row.addWidget(fields[key], 1)
            return pair
        form_rows(connection, [("Outside map", self.entrance_fields["map"]),
                               ("Enter from", coordinates(self.entrance_fields, "x", "y")),
                               ("Return to", coordinates(self.entrance_fields, "arrival_x", "arrival_y"))])
        self.interior_connection_fields = QWidget()
        inside = QVBoxLayout(self.interior_connection_fields)
        inside.setContentsMargins(0, 0, 0, 0)
        form_rows(inside, [("Arrive inside", coordinates(self.location_fields, "entry_x", "entry_y")),
                          ("Exit from", coordinates(self.location_fields, "exit_x", "exit_y"))])
        connection.addWidget(self.interior_connection_fields)
        self.doorway_summary = label("", "hint", True)
        connection.addWidget(self.doorway_summary)
        self.doorway_button = button("Move doorway in designer…", self.edit_doorway, "quiet")
        connection.addWidget(self.doorway_button)
        self.confirm_entrance_button = button("Use this entrance", self.confirm_entrance, "primary")
        connection.addWidget(self.confirm_entrance_button)
        self.connection_error = label("", "notice", True)
        self.connection_error.hide()
        connection.addWidget(self.connection_error)
        self.warps_card.hide()
        layout.addWidget(self.warps_card)
        page.layout().removeWidget(self.place_advanced_toggle)
        page.layout().removeWidget(self.legacy_characters_button)
        page.layout().addWidget(self.place_advanced_toggle)
        page.layout().addWidget(self.legacy_characters_button)
        self.location_advanced = QWidget()
        advanced_layout = QVBoxLayout(self.location_advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        map_tools, content = card("Map tools", "For imported maps and the game's location settings. Keep stable IDs unchanged after using a pack in a save.")
        form_rows(content, [("Stable map ID", self.location_fields["internal_name"])])
        self.map_identity = label("", "hint", True)
        content.addWidget(self.map_identity)
        self.import_map_button = button("Import Tiled map…", self.import_location)
        content.addWidget(self.import_map_button)
        content.addWidget(button("Create map from a tilesheet…", self.create_map))
        self.edit_map_button = button("Edit painted map…", self.edit_map)
        content.addWidget(self.edit_map_button)
        content.addWidget(label("Imported maps need 16×16 tiles, Back / Buildings / Front layers, and local TSX / PNG files. Collision, actions, and routes need in-game testing.", "hint", True))
        self.assign_home_button = button("Use this story location as their residence", self.assign_home)
        content.addWidget(self.assign_home_button)
        content.addWidget(self.location_fields["spouse_room"])
        advanced_layout.addWidget(map_tools)
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

    def load(self, world=None, *, preserve_history=False):
        self.dispose_editor()
        if not preserve_history:
            self._removed_places.clear()
            self.undo_remove_button.hide()
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
        self.place_advanced_toggle.setText(("▾ " if visible else "▸ ") + "Map tools && dependencies")
        self.tabs.setTabVisible(1, visible)
        self.tabs.tabBar().setVisible(visible)
        self.legacy_characters_button.setVisible(visible and bool(self.world["characters"]))
        if hasattr(self, "location_advanced"):
            self.location_advanced.setVisible(visible)
        self.location_list.setVisible(visible and len(self.world["locations"]) > 1)
        self.remove_buttons[self.location_panel].setVisible(visible)
        if self.location_index >= 0:
            record = self.world["locations"][self.location_index]
            self.interior_button.setVisible(visible and not record["spouse_room"] and not self._is_residence(record))

    def edit_doorway(self):
        if self.location_index < 0:
            return
        record = self.world["locations"][self.location_index]
        if self._is_residence(record):
            self._room_kind = "residence"
            self.room_switch.blockSignals(True)
            self.room_switch.setCurrentIndex(0)
            self.room_switch.blockSignals(False)
            self.settings_dialog.accept()
            self.activate_workspace()
            if self.interior_editor:
                self.interior_editor.tabs.setCurrentIndex(2)
                self.interior_editor.set_tool("entry")
        else:
            self.design_interior(doorway=True)

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
        self.build_home_button.hide()
        self.design_spouse_button.hide()

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
        record = self._new_interior_record(spouse=spouse)
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
        self.load(self.world, preserve_history=True)
        self.location_list.setCurrentRow(next((index for index, entry in enumerate(self.world["locations"])
                                              if entry["id"] == previous_id), -1))

    def _new_interior_record(self, *, spouse):
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
        record["entrance"]["confirmed"] = False
        record.update(name=(name[:60] + "'s " if name else "Their ") + ("spouse room" if spouse else "home"),
                      internal_name=internal, spouse_room=spouse)
        return record

    def add_location(self):
        if len(self.world["locations"]) >= 32:
            return
        record = new_location()
        used = {item["internal_name"].casefold() for item in self.world["locations"]}
        serial = 2
        while record["internal_name"].casefold() in used:
            record["internal_name"] = "NewPlace" + str(serial)
            serial += 1
        record["entrance"]["confirmed"] = False
        self.world["locations"].append(record)
        self.location_list.addItem("New place")
        self.location_list.setCurrentRow(len(self.world["locations"]) - 1)
        self.changed.emit()

    def remove_location(self):
        if self.location_index < 0:
            return
        from pixelheart_core.place_references import place_removal_blockers
        record = self.world["locations"][self.location_index]
        character = self._live_character()
        blockers = place_removal_blockers(record, character, self.world)
        if blockers:
            self.interior_notice.setText("This place is still used. Change these destinations before removing it:\n" +
                                         "\n".join("• " + item["description"] for item in blockers))
            self.interior_notice.show()
            QTimer.singleShot(0, lambda: self.detail_stacks[self.location_panel].widget(0).ensureWidgetVisible(self.interior_notice, 0, 12))
            return
        assigned = character.get("home_map") in (record["internal_name"], exported_location_id(record, character))
        home = tuple(character[key] for key in ("home_map", "home_x", "home_y")) if assigned else None
        self._removed_places.append((self.location_index, deepcopy(record), home))
        if home:
            self._reset_home()
        del self.world["locations"][self.location_index]
        self.load(self.world, preserve_history=True)
        self.undo_remove_button.show()
        self.undo_remove_button.setToolTip("Restore the removed place. Available until you save or open a project.")
        self.changed.emit()

    def undo_remove_location(self):
        if not self._removed_places:
            return
        index, record, home = self._removed_places[-1]
        if len(self.world["locations"]) >= 32 or any(
                item["internal_name"].casefold() == record["internal_name"].casefold() or item["id"] == record["id"]
                for item in self.world["locations"]):
            self.window.show_error("Cannot restore place", "Remove or rename the conflicting place before restoring this one.")
            return
        self._removed_places.pop()
        self.world["locations"].insert(min(index, len(self.world["locations"])), record)
        defaults = new_project()["character"]
        character = self._character()
        if home and all(character[key] == defaults[key] for key in ("home_map", "home_x", "home_y")):
            self._update_home(*home)
        self.load(self.world, preserve_history=True)
        self.location_list.setCurrentRow(next(i for i, item in enumerate(self.world["locations"]) if item["id"] == record["id"]))
        self.undo_remove_button.setVisible(bool(self._removed_places))
        self.changed.emit()

    def show_connection(self, visible):
        if self.location_index < 0:
            self.warps_card.hide()
            return
        record = self.world["locations"][self.location_index]
        self.warps_card.setVisible(visible and not record["spouse_room"])
        if visible and not record["spouse_room"]:
            self.settings_dialog.show()
            # The expanded form receives its final position on the next layout
            # pass. Scroll there only after that pass, not to its hidden geometry.
            QTimer.singleShot(0, self._scroll_to_connection)

    def _scroll_to_connection(self):
        if self.connection_button.isChecked() and self.warps_card.isVisible():
            self.location_panel.layout().activate()
            scroll = self.detail_stacks[self.location_panel].widget(0)
            scroll.verticalScrollBar().setValue(self.warps_card.y())

    def confirm_entrance(self):
        if self.location_index < 0:
            return
        record = self.world["locations"][self.location_index]
        entrance = record["entrance"]
        from pixelheart_core.world import IDENTIFIER, world_issues
        message = ""
        if not IDENTIFIER.fullmatch(entrance["map"]):
            message = "Choose the outside map before connecting this entrance."
        elif (entrance["x"], entrance["y"]) == (entrance["arrival_x"], entrance["arrival_y"]):
            message = "Choose a return tile different from the entrance trigger."
        else:
            character = self._character()
            places = {}
            for place in self.world["locations"]:
                places[place["internal_name"]] = places[exported_location_id(place, character)] = place
            visited = {record["id"]}
            destination = entrance["map"]
            while destination in places:
                place = places[destination]
                if place["id"] in visited or place["spouse_room"]:
                    message = "Choose a reachable outside map. The entrance cannot lead through a closed loop or a spouse room."
                    break
                visited.add(place["id"])
                destination = place["entrance"]["map"]
            for place in self.world["locations"]:
                other = place["entrance"]
                if place is not record and not place["spouse_room"] and other.get("confirmed", True) and all(
                        other[key] == entrance[key] for key in ("map", "x", "y")):
                    message = "Another place already uses that entrance tile. Choose a different tile."
                    break
        if not message:
            candidate = deepcopy(self.world)
            candidate["locations"][self.location_index]["entrance"]["confirmed"] = True
            prefix = f"world.locations.{self.location_index}.entrance"
            root = self.window.project_file.parent if self.window.project_file else None
            issue = next((issue for issue in world_issues(candidate, self._live_character(), root)
                          if issue["level"] == "error" and issue["field"].startswith(prefix)), None)
            message = issue["message"] if issue else ""
        if message:
            self.connection_error.setText(message)
            self.connection_error.show()
            return
        entrance["confirmed"] = True
        self.connection_error.hide()
        self.connection_button.setChecked(False)
        self.refresh_location()
        self.changed.emit()

    def select_location(self, index):
        if self.loading:
            return
        self.location_index = index
        self.interior_notice.hide()
        self.connection_error.hide()
        self.connection_button.setChecked(False)
        self.warps_card.hide()
        self.location_panel.setEnabled(index >= 0)
        self.location_list.setVisible(self.place_advanced_toggle.isChecked() and len(self.world["locations"]) > 1)
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
        desired_name = value(self.location_fields["internal_name"])
        if desired_name != record["internal_name"]:
            from pixelheart_core.place_references import rename_place
            before = self._live_character()
            try:
                character, world = rename_place(before, self.world, record["id"], desired_name)
            except ValueError as exc:
                self.interior_notice.setText(str(exc))
                self.interior_notice.show()
                return
            self.world = world
            record = self.world["locations"][self.location_index]
            # A self-referencing legacy entrance is migrated by the helper too.
            # Keep the form from writing its previous map ID over that migration.
            self.entrance_fields["map"].set_value(record["entrance"]["map"])
            self.window.document["character"].update(character)
            if hasattr(self.window, "identity"):
                self.window.identity.load(character)
            for key in ("schedule", "events", "life"):
                if character.get(key) != before.get(key) and hasattr(self.window, key):
                    getattr(self.window, key).load(character if key == "life" else character[key])
            self.interior_notice.hide()
        if self.location_fields["spouse_room"].isChecked() and self._spouse_index(excluding=record["id"]) is not None:
            self.location_fields["spouse_room"].blockSignals(True)
            self.location_fields["spouse_room"].setChecked(False)
            self.location_fields["spouse_room"].blockSignals(False)
        fields = {key: value(widget) for key, widget in self.location_fields.items()}
        inside_changed = any(record[key] != fields[key] for key in ("entry_x", "entry_y", "exit_x", "exit_y"))
        if record.get("interior"):
            inside_changed = False
            for key in ("entry_x", "entry_y", "exit_x", "exit_y"):
                fields.pop(key)
        record.update(fields)
        entrance = {key: value(widget) for key, widget in self.entrance_fields.items()}
        if inside_changed or any(record["entrance"][key] != entry for key, entry in entrance.items()):
            entrance["confirmed"] = False
        record["entrance"].update(entrance)
        self.connection_error.hide()
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
        self.interior_button.setVisible(self.place_advanced_toggle.isChecked() and not record["spouse_room"] and not residence)
        self.place_role.setText("Their spouse room in the farmhouse." if record["spouse_room"] else
                                "Their residence. Changes here apply to the NPC in this project." if residence else
                                "An optional location for their story.")
        self.assign_home_button.setVisible(not record["spouse_room"] and not residence)
        self.assign_home_button.setEnabled(bool(design or record["map"]))
        self.location_fields["spouse_room"].setEnabled(design is None and self._spouse_index(excluding=record["id"]) is None)
        self.refresh_home_actions()
        self.connection_button.setVisible(not record["spouse_room"])
        self.connection_button.setText("Edit entrance…" if record["entrance"].get("confirmed", True) else "Connect entrance…")
        self.warps_card.setVisible(self.connection_button.isChecked() and not record["spouse_room"])
        self.interior_connection_fields.setVisible(design is None)
        self.doorway_button.setVisible(bool(design) and not record["spouse_room"])
        self.doorway_summary.setVisible(bool(design))
        self.loading = True
        for key in ("entry_x", "entry_y", "exit_x", "exit_y"):
            self.location_fields[key].setEnabled(design is None)
            set_value(self.location_fields[key], record[key])
        self.loading = False
        if design:
            from pixelheart_core.interiors import doorway_exit
            entry = design["entry"]
            exit_at = doorway_exit(design) if "doorway" in design else (record["exit_x"], record["exit_y"])
            self.doorway_summary.setText(f"Designer doorway · arrive at {entry[0]}, {entry[1]} · exit at {exit_at[0]}, {exit_at[1]}")
        self.room_card.setVisible(record["spouse_room"])
        entrance = record["entrance"]
        self.connection_hint.setText("Joins the farmhouse after marriage. Keep the standing spot clear." if record["spouse_room"] else
                                     f"Entrance: {entrance['map']} ({entrance['x']}, {entrance['y']}) → inside · return ({entrance['arrival_x']}, {entrance['arrival_y']})." if entrance.get("confirmed", True) else
                                     "Entrance not connected. Choose the outside map and tiles before export.")
        self.map_identity.setText("This map section is placed in FarmHouse; it is not a separate location." if record["spouse_room"] else "Use “" + record["internal_name"] + "” for schedules and story locations. Game map: " + exported_location_id(record, self.window.document["character"]))
        self.map_status.setText(f"{len(design['rooms'])} room(s) · {len(design['furniture'])} furniture item(s)" if design else "Imported map · Open Map tools & dependencies to edit its source." if record["map"] else "Open the designer to shape and furnish their home.")
        from pixelheart_core.world import world_issues
        root = self.window.project_file.parent if self.window.project_file else None
        prefix = f"world.locations.{self.location_index}"
        failures = [("Choose the outside map and tiles, then use this entrance." if item["field"] == prefix + ".entrance" and
                     not record["entrance"].get("confirmed", True) else
                     "Design an interior or import a map to give this place a layout." if item["field"] == prefix + ".map" and
                     not design and not record["map"] else item["message"])
                    for item in world_issues(self.world, self._live_character(), root)
                    if item["level"] == "error" and (item["field"] == prefix or item["field"].startswith(prefix + "."))]
        if design and not design["atlas"]["asset"]:
            failures = ["Connect a game library or add custom tile art to finish this draft."] + [
                message for message in failures if "tilesheet" not in message]
        self.readiness.setText("Draft · " + "\n".join(dict.fromkeys(failures)) if failures else
                               "Ready for export checks · verify the home in the game after installation.")
        self.readiness.setObjectName("notice" if failures else "hint")
        self.readiness.style().unpolish(self.readiness)
        self.readiness.style().polish(self.readiness)
        key = (str(self.window.project_file), record["map"], repr(design))
        if key != self._preview_key:
            self._preview_key = key
            if (design or record["map"]) and self.window.project_file:
                try:
                    from PIL.ImageQt import ImageQt
                    if design:
                        from pixelheart_core.interiors import render_interior
                        preview = render_interior(design, self.window.project_file.parent)
                        rooms = [room for room in design["rooms"] if room["enabled"]]
                        if rooms:
                            left = max(0, min(room["x"] for room in rooms) - 1)
                            top = max(0, min(room["y"] - 3 for room in rooms) - 1)
                            right = min(design["width"], max(room["x"] + room["width"] for room in rooms) + 1)
                            bottom = min(design["height"], max(room["y"] + room["height"] for room in rooms) + 2)
                            cropped = preview.crop((left * 16, top * 16, right * 16, bottom * 16))
                            preview.close()
                            preview = cropped
                    else:
                        preview = render_map_preview(asset_path(record["map"], self.window.project_file.parent), 600)
                    pixmap = QPixmap.fromImage(ImageQt(preview))
                    self.map_preview.setPixmap(pixmap.scaled(560, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                    preview.close()
                except (WorldError, OSError) as exc:
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
            self.dispose_editor()
            self.refresh_location()
            self.changed.emit()
        except (WorldError, OSError) as exc:
            self.window.show_error("Map import needs attention", str(exc))

    def create_map(self):
        self._paint_map()

    def design_interior(self, checked=False, *, allow_rebase=False, doorway=False):
        if self.location_index < 0:
            return False
        identity = self.world["locations"][self.location_index]["id"]
        if not self.window.ensure_saved():
            return False
        self.location_list.setCurrentRow(next(index for index, item in enumerate(self.world["locations"]) if item["id"] == identity))
        from .interior_editor import InteriorEditor
        from PySide6.QtWidgets import QDialog
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
            if doorway:
                dialog.tabs.setCurrentIndex(2)
                dialog.set_tool("entry")
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_design:
                scenes_moved = self._apply_interior_result(record, dialog.result_design, initial_design,
                                                           getattr(dialog, "result_room_translations", None))
                self.select_location(self.location_index)
                self.interior_notice.setText("Interior applied. Choose Save project to keep these changes." +
                                             (" Room moved: review scene blocking and walking routes." if scenes_moved else ""))
                self.interior_notice.show()
                self.dispose_editor()
                self.changed.emit()
                accepted = True
        except (ValueError, OSError) as exc:
            self.window.show_error("Interior needs attention", str(exc))
        finally:
            if dialog is not None:
                dialog.deleteLater()
        return accepted

    def _apply_interior_result(self, record, design, initial, offsets=None):
        from pixelheart_core.interiors import doorway_exit, reachable_tiles
        previous_entry = (record["entry_x"], record["entry_y"])
        entry = tuple(design["entry"])
        floors = reachable_tiles(design)
        if len(floors) < 2 and not record["spouse_room"]:
            raise WorldError("Leave a free exit tile reachable from the interior entry.")
        exit_position = (record["exit_x"], record["exit_y"])
        translations = _room_translations(initial, design,
                                          offsets)
        self._validate_room_points(design, self._protected_room_points(record), translations)
        if "doorway" in design:
            exit_position = doorway_exit(design)
        elif not record["spouse_room"]:
            exit_position = _translate_room_point(*exit_position, translations)
        if not record["spouse_room"] and (exit_position not in floors or exit_position == entry):
            exit_position = min(floors - {entry}, key=lambda p: (abs(p[0]-entry[0])+abs(p[1]-entry[1]), p[1], p[0]))
        scenes_moved = self._move_authored_room_positions(record, translations) if translations and not record["spouse_room"] else False
        record["interior"] = design
        record["map"] = None
        record["room_x"] = record["room_y"] = 0
        record["entry_x"], record["entry_y"] = design["entry"]
        record["exit_x"], record["exit_y"] = exit_position
        if self._is_residence(record):
            self._sync_home(record, _translate_room_point(*previous_entry, translations))
        return scenes_moved

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
            self.dispose_editor()
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
            self.load(self.world, preserve_history=True)
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
        if not self.flush_editor():
            return
        self.home_switch.blockSignals(True)
        self.home_switch.setCurrentIndex(0)
        self.home_switch.blockSignals(False)
        self.home_stack.setCurrentIndex(0)
        parts = field.split(".")
        if parts and parts[0] == "world":
            parts = parts[1:]
        if not parts:
            return
        if parts[0] == "locations" and len(parts) > 1 and parts[1].isdigit():
            index = int(parts[1])
            if 0 <= index < len(self.world["locations"]):
                record = self.world["locations"][index]
                if (record["spouse_room"] or self._is_residence(record)) and ("interior" in parts or
                        (parts[-1] == "map" and record.get("interior"))):
                    self._room_kind = "spouse" if record["spouse_room"] else "residence"
                    self.room_switch.blockSignals(True)
                    self.room_switch.setCurrentIndex(1 if record["spouse_room"] else 0)
                    self.room_switch.blockSignals(False)
                    self.settings_dialog.hide()
                    self.activate_workspace()
                    if self.interior_editor:
                        self.interior_editor.canvas.setFocus()
                    return
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        if parts[0] == "characters":
            self.place_advanced_toggle.setChecked(True)
            self.edit_legacy_characters(index=int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
            return
        if parts[0] == "locations":
            self.tabs.setCurrentIndex(0)
            if len(parts) > 1 and parts[1].isdigit():
                self.location_list.setCurrentRow(int(parts[1]))
            if self.location_index < 0:
                return
            record = self.world["locations"][self.location_index]
            field = parts[-1]
            if "entrance" in parts or field in ("entry_x", "entry_y", "exit_x", "exit_y"):
                self.connection_button.setChecked(True)
                widget = (self.doorway_button if record.get("interior") and field in self.location_fields
                          else self.entrance_fields.get(field, self.entrance_fields["map"]))
            elif field == "interior" or (field == "map" and record.get("interior")):
                self.place_advanced_toggle.setChecked(True)
                widget = self.interior_button
            else:
                if field != "name":
                    self.place_advanced_toggle.setChecked(True)
                widget = self.location_fields.get(field, self.edit_map_button if record["map"] else self.import_map_button)
            self.detail_stacks[self.location_panel].widget(0).ensureWidgetVisible(widget, 0, 12)
            widget.setFocus()
        elif parts[0] == "dependencies":
            self.place_advanced_toggle.setChecked(True)
            self.tabs.setCurrentIndex(1)
            if len(parts) > 1 and parts[1].isdigit():
                self.dependency_list.setCurrentRow(int(parts[1]))
            widget = self.dependency_fields.get(parts[-1])
            if widget:
                widget.setFocus()
