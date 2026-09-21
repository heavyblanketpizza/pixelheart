"""Editable supporting characters and imported places for a portable NPC pack."""
from copy import deepcopy
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QListWidget,
    QSplitter, QComboBox, QCheckBox, QFileDialog, QPlainTextEdit, QScrollArea, QSizePolicy,
    QStackedWidget,
)

from pixelheart_core.world import (
    WorldError, new_world, new_companion, new_location, normalize_world,
    import_map, map_bundle, asset_path, exported_location_id, cast_actor_id, render_map_preview,
)
from pixelheart_core.projects import import_artwork, ProjectError
from pixelheart_core.artwork import inspect_artwork, ArtworkValidationError
from pixelheart_core.story import exported_npc_id
from .editors import line, number, value, set_value, connect_change, RecordsPage, SchedulePage
from .widgets import label, button, card, ArtworkPreview
from .location_picker import MapSelector


def combo(options):
    result = QComboBox()
    for text, data in options:
        result.addItem(text, data)
    return result


def form_rows(layout, rows):
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for title, widget in rows:
        widget.setAccessibleName(title)
        form.addRow(title, widget)
    layout.addLayout(form)
    return form


class WorldPage(QWidget):
    changed = Signal()

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.world = new_world()
        self.loading = False
        self.cast_index = self.location_index = self.dependency_index = -1
        self.detail_stacks = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        intro, layout = card("A life with people and places", "Create real supporting characters, bring in your own maps, and connect every place to the valley.")
        layout.addWidget(label("Supporting cast need their own portrait and sprite sheets. Imported maps keep their tilesheets with the project. All locations and routes still need an in-game playtest.", "hint", True))
        root.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)
        self._build_cast()
        self._build_places()
        self._build_dependencies()

    def _split_page(self, title, add, remove):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 14, 0, 0)
        row = QHBoxLayout()
        row.addWidget(button("+ " + title, add, "primary"))
        row.addWidget(button("Remove", remove, "quiet"))
        row.addStretch()
        root.addLayout(row)
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
            "Add companion": ("A place for someone new", "Add a companion to give your story another familiar face."),
            "Add place": ("Room for your world to grow", "Add a place to bring a workshop, cottage, or hidden corner into the valley."),
            "Add dependency": ("Everything your story needs", "Add a dependency when your mod uses another creator's maps or content."),
        }[title]
        for text, style in ((heading, "sectionTitle"), (hint, "muted")):
            caption = label(text, style, True)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(caption)
        empty_layout.addStretch()
        stack.addWidget(empty)
        self.detail_stacks[panel] = stack
        split.addWidget(stack)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        return page, listing, panel, content

    def _build_cast(self):
        page, self.cast_list, self.cast_panel, layout = self._split_page("Add companion", self.add_companion, self.remove_companion)
        self.cast_fields = {
            "name": line("Who belongs in their world?", 64), "internal_name": line("CompanionID", 64),
            "age": combo([("Adult", "adult"), ("Teen · supporting role", "teen"), ("Child · supporting role", "child")]),
            "gender": combo([(text, text) for text in ("Male", "Female", "Undefined")]),
            "romanceable": QCheckBox("Adult romance is available"),
            "season": combo([(s.title(), s) for s in ("spring", "summer", "fall", "winter")]),
            "day": number(1, 28), "home_map": MapSelector(compact=True), "home_x": number(0, 1000), "home_y": number(0, 1000),
        }
        profile, content = card("Someone with a place in the story")
        form_rows(content, [("Name", self.cast_fields["name"]), ("Character ID", self.cast_fields["internal_name"]),
                            ("Age", self.cast_fields["age"]), ("Game gender", self.cast_fields["gender"]),
                            ("Romance", self.cast_fields["romanceable"]), ("Birthday season", self.cast_fields["season"]),
                            ("Birthday day", self.cast_fields["day"]), ("Home map", self.cast_fields["home_map"]),
                            ("Home tile X", self.cast_fields["home_x"]), ("Home tile Y", self.cast_fields["home_y"])])
        self.cast_identity = label("", "hint", True)
        content.addWidget(self.cast_identity)
        layout.addWidget(profile)
        artwork, content = card("Give them their own artwork")
        row = QHBoxLayout()
        self.cast_previews = {}
        self.cast_asset_labels = {}
        for kind in ("portrait", "sprite"):
            column = QVBoxLayout()
            preview = ArtworkPreview("Import a complete " + kind + " sheet")
            preview.setMinimumSize(130, 130)
            preview.setMaximumHeight(220)
            column.addWidget(preview)
            column.addWidget(button("Import " + kind + " sheet…", lambda checked=False, k=kind: self.import_cast_artwork(k)))
            status = label("No sheet selected", "hint", True)
            column.addWidget(status)
            self.cast_previews[kind] = preview
            self.cast_asset_labels[kind] = status
            row.addLayout(column)
        content.addLayout(row)
        layout.addWidget(artwork)
        self.cast_content_tabs = QTabWidget()
        self.cast_dialogue = RecordsPage("dialogues")
        self.cast_schedule = SchedulePage(compact=True)
        self.cast_content_tabs.addTab(self.cast_dialogue, "Their dialogue")
        self.cast_content_tabs.addTab(self.cast_schedule, "Their daily route")
        self.cast_content_tabs.setMinimumHeight(530)
        self.cast_content_tabs.setMinimumWidth(0)
        layout.addWidget(self.cast_content_tabs)
        gifts, content = card("Their favorite things", "Separate object IDs or known gift names with commas.")
        self.gift_fields = {kind: line("Sunflower, Coffee, (O)66", 5000) for kind in ("love", "like", "dislike", "hate")}
        form_rows(content, [(kind.title(), widget) for kind, widget in self.gift_fields.items()])
        layout.addWidget(gifts)
        layout.addStretch()
        self.cast_list.currentRowChanged.connect(self.select_companion)
        for widget in (*self.cast_fields.values(), *self.gift_fields.values()):
            connect_change(widget, self.edit_companion)
        self.cast_dialogue.changed.connect(self.edit_companion)
        self.cast_schedule.changed.connect(self.edit_companion)
        self.tabs.addTab(page, "Supporting cast")

    def _build_places(self):
        page, self.location_list, self.location_panel, layout = self._split_page("Add place", self.add_location, self.remove_location)
        self.location_fields = {"name": line("Their workshop, a cottage, a hidden garden…", 80),
                                "internal_name": line("StableMapID", 40), "spouse_room": QCheckBox("Use a section as the primary character’s spouse room"),
                                **{key: number(0, 1000) for key in ("room_x", "room_y", "entry_x", "entry_y", "exit_x", "exit_y")}}
        self.entrance_fields = {"map": MapSelector(compact=True), **{key: number(0, 1000) for key in ("x", "y", "arrival_x", "arrival_y")}}
        details, content = card("A place that belongs to them")
        form_rows(content, [("Place name", self.location_fields["name"]), ("Stable map ID", self.location_fields["internal_name"])])
        content.addWidget(button("Import Tiled map…", self.import_location, "primary"))
        content.addWidget(button("Create map from a tilesheet…", self.create_map))
        self.edit_map_button = button("Edit painted map…", self.edit_map)
        content.addWidget(self.edit_map_button)
        self.map_status = label("Import a finite 16×16 tile TMX map with Back, Buildings, and Front layers. Keep its TSX and PNG files in the same folder or subfolders.", "hint", True)
        content.addWidget(self.map_status)
        self.map_identity = label("", "hint", True)
        content.addWidget(self.map_identity)
        self.map_preview = label("Import a map to preview its supplied tiles.", "muted", True)
        self.map_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.map_preview.setMinimumHeight(150)
        self.map_preview.setMaximumHeight(250)
        self._preview_key = None
        content.addWidget(self.map_preview)
        content.addWidget(label("Tile artwork preview · Collision, map actions, and character routes need in-game testing.", "hint", True))
        content.addWidget(self.location_fields["spouse_room"])
        layout.addWidget(details)
        self.warps_card, content = card("Give the player a way in and out", "The entrance and return arrival must be different tiles. Check their walkability in-game.")
        form_rows(content, [("Entrance map", self.entrance_fields["map"]), ("Entrance trigger X", self.entrance_fields["x"]),
                            ("Entrance trigger Y", self.entrance_fields["y"]), ("Arrive inside at X", self.location_fields["entry_x"]),
                            ("Arrive inside at Y", self.location_fields["entry_y"]), ("Exit trigger X", self.location_fields["exit_x"]),
                            ("Exit trigger Y", self.location_fields["exit_y"]), ("Return outside at X", self.entrance_fields["arrival_x"]),
                            ("Return outside at Y", self.entrance_fields["arrival_y"])])
        layout.addWidget(self.warps_card)
        self.room_card, content = card("Their room in the farmhouse", "Choose the top-left tile of a 6×9 section. The game places it in the farmhouse when the player marries your primary character.")
        form_rows(content, [("Room section X", self.location_fields["room_x"]), ("Room section Y", self.location_fields["room_y"])])
        layout.addWidget(self.room_card)
        layout.addStretch()
        self.location_list.currentRowChanged.connect(self.select_location)
        for widget in (*self.location_fields.values(), *self.entrance_fields.values()):
            connect_change(widget, self.edit_location)
        self.tabs.addTab(page, "Places && spouse room")

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
        self.tabs.addTab(page, "Mod dependencies")

    def load(self, world=None):
        selected_ids = {}
        for collection, index in (("characters", self.cast_index), ("locations", self.location_index), ("dependencies", self.dependency_index)):
            if 0 <= index < len(self.world[collection]):
                selected_ids[collection] = self.world[collection][index]["id"]
        self.loading = True
        self.world = normalize_world(world)
        self._refresh_lists()
        for collection, listing in (("characters", self.cast_list), ("locations", self.location_list), ("dependencies", self.dependency_list)):
            index = next((index for index, entry in enumerate(self.world[collection]) if entry["id"] == selected_ids.get(collection)),
                         0 if self.world[collection] else -1)
            listing.setCurrentRow(index)
        self.loading = False
        self.select_companion(self.cast_list.currentRow())
        self.select_location(self.location_list.currentRow())
        self.select_dependency(self.dependency_list.currentRow())

    def dump(self):
        return deepcopy(self.world)

    def _refresh_lists(self):
        for listing, collection, text in ((self.cast_list, "characters", lambda item: item["character"].get("name") or "Unnamed companion"),
                                           (self.location_list, "locations", lambda item: item.get("name") or "Unnamed place"),
                                           (self.dependency_list, "dependencies", lambda item: item.get("id") or "Unnamed dependency")):
            listing.clear()
            listing.addItems([text(item) for item in self.world[collection]])

    def add_companion(self):
        if len(self.world["characters"]) >= 32:
            return
        self.world["characters"].append(new_companion())
        self.cast_list.addItem("New companion")
        self.cast_list.setCurrentRow(len(self.world["characters"]) - 1)
        self.changed.emit()

    def remove_companion(self):
        if self.cast_index >= 0:
            del self.world["characters"][self.cast_index]
            self.load(self.world)
            self.changed.emit()

    def select_companion(self, index):
        if self.loading:
            return
        self.cast_index = index
        self.cast_panel.setEnabled(index >= 0)
        self.detail_stacks[self.cast_panel].setCurrentIndex(0 if index >= 0 else 1)
        if index < 0:
            return
        self.loading = True
        entry = self.world["characters"][index]
        character = entry["character"]
        for key, widget in self.cast_fields.items():
            set_value(widget, character.get(key, False if key == "romanceable" else ""))
        for key, widget in self.gift_fields.items():
            set_value(widget, ", ".join(map(str, character.get("gifts", {}).get(key, []))))
        self.cast_dialogue.load(character.get("dialogues", []))
        self.cast_schedule.load(character.get("schedule", []))
        self.loading = False
        self.refresh_cast_artwork()
        self.cast_identity.setText("Stable story actor: " + cast_actor_id(entry) + "\nYou can also use “" + character.get("internal_name", "") + "”. Keep IDs stable after publishing.")
        self.cast_fields["romanceable"].setEnabled(character.get("age", "adult") == "adult")

    def edit_companion(self):
        if self.loading or self.cast_index < 0:
            return
        entry = self.world["characters"][self.cast_index]
        character = entry["character"]
        character.update({key: value(widget) for key, widget in self.cast_fields.items()})
        if character["age"] != "adult":
            character["romanceable"] = False
            self.cast_fields["romanceable"].blockSignals(True)
            self.cast_fields["romanceable"].setChecked(False)
            self.cast_fields["romanceable"].blockSignals(False)
        self.cast_fields["romanceable"].setEnabled(character["age"] == "adult")
        character["dialogues"] = self.cast_dialogue.dump()
        character["schedule"] = self.cast_schedule.dump()
        character["gifts"] = {key: [part.strip() for part in widget.text().split(",") if part.strip()] for key, widget in self.gift_fields.items()}
        self.cast_list.item(self.cast_index).setText(character["name"] or "Unnamed companion")
        self.cast_identity.setText("Stable story actor: " + cast_actor_id(entry) + "\nYou can also use “" + character.get("internal_name", "") + "”. Keep IDs stable after publishing.")
        self.changed.emit()

    def import_cast_artwork(self, kind):
        if self.cast_index < 0:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import companion " + kind, "", "PNG artwork (*.png)")
        if not path:
            return
        identity = self.world["characters"][self.cast_index]["id"]
        try:
            inspect_artwork(path)
            if not self.window.ensure_saved():
                return
            self.cast_list.setCurrentRow(next(index for index, entry in enumerate(self.world["characters"]) if entry["id"] == identity))
            self.world["characters"][self.cast_index]["artwork"][kind] = import_artwork(path, self.window.project_file, kind)
            self.refresh_cast_artwork()
            self.changed.emit()
        except (ArtworkValidationError, ProjectError, WorldError) as exc:
            self.window.show_error("Companion artwork needs attention", str(exc))

    def refresh_cast_artwork(self):
        entry = self.world["characters"][self.cast_index]
        for kind, preview in self.cast_previews.items():
            reference = entry.get("artwork", {}).get(kind)
            try:
                path = asset_path(reference, self.window.project_file.parent) if reference and self.window.project_file else None
                preview.set_image(path, portrait=kind == "portrait")
                self.cast_asset_labels[kind].setText(reference or "No sheet selected")
            except (WorldError, OSError) as exc:
                preview.set_image()
                self.cast_asset_labels[kind].setText(str(exc))

    def add_location(self):
        if len(self.world["locations"]) >= 32:
            return
        self.world["locations"].append(new_location())
        self.location_list.addItem("New place")
        self.location_list.setCurrentRow(len(self.world["locations"]) - 1)
        self.changed.emit()

    def remove_location(self):
        if self.location_index >= 0:
            del self.world["locations"][self.location_index]
            self.load(self.world)
            self.changed.emit()

    def select_location(self, index):
        if self.loading:
            return
        self.location_index = index
        self.location_panel.setEnabled(index >= 0)
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
        record.update({key: value(widget) for key, widget in self.location_fields.items()})
        record["entrance"].update({key: value(widget) for key, widget in self.entrance_fields.items()})
        self.location_list.item(self.location_index).setText(record["name"] or "Unnamed place")
        self.refresh_location()
        self.changed.emit()

    def refresh_location(self):
        record = self.world["locations"][self.location_index]
        self.edit_map_button.setEnabled(bool(record["map"]))
        self.warps_card.setVisible(not record["spouse_room"])
        self.room_card.setVisible(record["spouse_room"])
        self.map_identity.setText("This map section is placed in FarmHouse; it is not a separate location." if record["spouse_room"] else "Use “" + record["internal_name"] + "” for schedules and story locations. Game map: " + exported_location_id(record, self.window.document["character"]))
        self.map_status.setText(record["map"] or "Import a finite TMX map with local TSX and PNG tilesheets. Layers: Back, Buildings, Front.")
        key = (str(self.window.project_file), record["map"])
        if key != self._preview_key:
            self._preview_key = key
            if record["map"] and self.window.project_file:
                try:
                    from PIL.ImageQt import ImageQt
                    preview = render_map_preview(asset_path(record["map"], self.window.project_file.parent), 600)
                    pixmap = QPixmap.fromImage(ImageQt(preview))
                    self.map_preview.setPixmap(pixmap.scaled(400, 230, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                except WorldError as exc:
                    self.map_preview.setText("Preview unavailable: " + str(exc))
            else:
                self.map_preview.setText("Import a map to preview its supplied tiles.")

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
            self.refresh_location()
            self.changed.emit()
        except (WorldError, OSError) as exc:
            self.window.show_error("Map import needs attention", str(exc))

    def create_map(self):
        self._paint_map()

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
            record["map"] = dialog.result_reference
            if dialog.result_is_spouse_room:
                record.update(spouse_room=True, room_x=0, room_y=0)
            self.select_location(self.location_index)
            self.changed.emit()

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
        controls = {"characters": (0, self.cast_list, self.cast_fields),
                    "locations": (1, self.location_list, {**self.location_fields, **self.entrance_fields}),
                    "dependencies": (2, self.dependency_list, self.dependency_fields)}
        if parts[0] in controls:
            tab, listing, fields = controls[parts[0]]
            self.tabs.setCurrentIndex(tab)
            if len(parts) > 1 and parts[1].isdigit():
                listing.setCurrentRow(int(parts[1]))
            widget = fields.get(parts[-1])
            if widget:
                widget.setFocus()
