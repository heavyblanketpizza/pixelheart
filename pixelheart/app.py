"""The desktop workspace and its project lifecycle."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QSaveFile, QIODevice
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QScrollArea,
    QListWidget, QListWidgetItem, QFrame, QFileDialog, QMessageBox, QDialog,
)

from pixelheart_core.projects import (
    new_project, load_project, save_project, copy_project, project_path,
    resolve_artwork, ProjectError,
)
from pixelheart_core.validation import validate_draft, DraftValidationError, EDITABLE_FIELDS
from pixelheart_core.exporting import validate_character, build_mod_archive, ExportValidationError
from pixelheart_core.artwork import inspect_artwork, ArtworkValidationError
from . import __version__
from .artwork_page import ArtworkPage
from .editors import IdentityPage, RecordsPage, SchedulePage
from .gifts_page import GiftsPage
from .story_page import StoryPage
from .creator_page import CreatorPage
from .life_page import LifePage
from .world_page import WorldPage
from .location_picker import MapSelector
from pixelheart_core.creator import record_export
from .theme import heart_icon
from .navigation_icons import navigation_icon
from .widgets import label, button, card


SECTIONS = [
    ("Identity", "A new face in the valley.", "Give your character a name, a place, and a little personality."),
    ("Dialogue", "Something only they would say.", "Write the small conversations that turn a stranger into someone familiar."),
    ("Schedule", "Find their everyday rhythm.", "From a slow morning to a favorite spot at sunset."),
    ("Gifts", "It's the thought that counts.", "A few favorites, a few pet peeves, and another way to get to know them."),
    ("Story notes", "Every heart has a story.", "Turn small ideas into playable moments and relationships that grow."),
    ("Artwork", "Let their personality show.", "Your portraits and sprites, prepared with care."),
    ("Review & export", "Almost ready to meet the valley.", "Check the details, prepare a starter pack, then bring it into the game."),
    ("Create your mod", "From an idea to a life in the valley.", "Build your first playable chapter, then grow the cast, relationships, and world around it."),
    ("Life & reactions", "Let the story change their everyday life.", "Conversations, routines, and married life that respond to what the player has experienced."),
    ("Cast & locations", "A world for your story to grow into.", "Create supporting characters, import locations, and connect them to the valley."),
]


class ExportPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.issues = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)
        summary, content = card("A thoughtful final check")
        self.summary = label("Check your character before exporting.", "profileName", True)
        content.addWidget(self.summary)
        content.addWidget(label("Checks cover project data and sheet dimensions. Walkable tiles, correct animation frames, and game behavior still need testing in Stardew Valley.", "muted", True))
        actions = QHBoxLayout()
        actions.addWidget(button("Run checks", self.refresh))
        actions.addStretch()
        self.export_button = button("Export Content Patcher ZIP…", window.export_project, "primary")
        actions.addWidget(self.export_button)
        content.addLayout(actions)
        root.addWidget(summary)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMinimumHeight(310)
        self.list.setAccessibleName("Validation results; activate a result to open its editor")
        self.list.itemActivated.connect(self.open_issue)
        root.addWidget(self.list)
        root.addWidget(label("WHAT YOUR PACK INCLUDES", "eyebrow"))
        root.addWidget(label("Character identity · Conversations and reactions · Gift tastes · Conditional routines · Selected PNG artwork · Ready story events · Supporting cast · Imported locations\n\nScenes include their triggers, cast, actions, and choices. Linked chapters develop relationships; authored spouse dialogue continues them after marriage. Unfinished scenes stay in your project backup. Review each enabled feature in-game.", "muted", True))
        root.addWidget(label("Install with SMAPI 4+ and Content Patcher 2.9.0+ for Stardew Valley 1.6. Test the exported pack in-game before sharing it.", "notice", True))
        root.addStretch()

    def refresh(self):
        self.issues = self.window.validate_project()
        self.list.clear()
        counts = {level: sum(issue["level"] == level for issue in self.issues) for level in ("error", "warning", "success")}
        errors, warnings = counts["error"], counts["warning"]
        self.summary.setText(f"{errors} {'thing needs' if errors == 1 else 'things need'} a little attention." if errors else "Ready for an in-game first meeting.")
        for issue in sorted(self.issues, key=lambda item: {"error": 0, "warning": 1, "success": 2}[item["level"]]):
            prefix = {"error": "FIX", "warning": "REVIEW", "success": "READY"}[issue["level"]]
            item = QListWidgetItem(f"{prefix}  ·  {issue['field'].replace('_', ' ').capitalize()}\n{issue['message']}")
            item.setData(Qt.ItemDataRole.UserRole, issue)
            item.setToolTip("Activate to open the related editor")
            self.list.addItem(item)
        self.export_button.setEnabled(errors == 0)
        self.window.statusBar().showMessage(f"Review complete · {errors} blockers · {warnings} things to review", 8000)
        return self.issues

    def open_issue(self, item):
        field = item.data(Qt.ItemDataRole.UserRole)["field"]
        group = field.split(".")[0]
        destination = {"dialogues": 1, "schedule": 2, "gifts": 3, "events": 4, "relationships": 4, "portrait": 5, "sprite": 5, "appearances": 5, "life": 8, "world": 9}.get(group, 0)
        self.window.navigation.setCurrentRow(destination)
        if destination == 4:
            self.window.story.open_issue(field)
        if destination == 8:
            self.window.life.open_issue(field)
        if destination == 9 and hasattr(self.window.world, "open_issue"):
            self.window.world.open_issue(field)
        if destination == 5:
            parts = field.split(".")
            self.window.artwork.select_appearance(parts[1] if group == "appearances" and len(parts) > 1 else None)
        if group in self.window.identity.fields:
            self.window.identity.fields[group].setFocus()
        if group in ("season", "day"):
            self.window.identity.birthday.focus_selected()
        if group == "dialogues" and "." in field:
            try:
                self.window.dialogue.list.setCurrentRow(int(field.split(".")[1]))
            except ValueError:
                pass


class MainWindow(QMainWindow):
    def __init__(self, path=None, *, auto_download_icons=False):
        super().__init__()
        self.auto_download_icons = auto_download_icons
        self._closing_after_download = False
        self.document = new_project()
        self.project_file = None
        self.dirty = False
        self.loading = True
        self.setWindowIcon(heart_icon())
        self.setMinimumSize(1020, 700)
        self.resize(1360, 900)
        self.build_workspace()
        self.build_menus()
        self.load_document(self.document)
        if path:
            self.open_path(path)

    def build_workspace(self):
        central = QWidget()
        central.setObjectName("workspace")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(234)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 16)
        side.setSpacing(8)
        brand = QHBoxLayout()
        brand.setSpacing(9)
        icon = label("")
        icon.setPixmap(heart_icon().pixmap(28, 28))
        icon.setFixedSize(28, 28)
        brand.addWidget(icon)
        brand.addWidget(label("pixelheart", "brand"))
        brand.addStretch()
        side.addLayout(brand)
        side.addSpacing(18)
        new_button = button("+  New character", self.new_character, "sidebarAction")
        new_button.setMinimumHeight(34)
        side.addWidget(new_button)
        open_button = button("Open a project…", self.open_dialog, "sidebarOpen")
        open_button.setMinimumHeight(30)
        side.addWidget(open_button)
        side.addSpacing(8)
        sidebar_separator = QFrame()
        sidebar_separator.setObjectName("sidebarSeparator")
        sidebar_separator.setFixedHeight(1)
        side.addWidget(sidebar_separator)
        side.addSpacing(8)
        side.addWidget(label("YOUR WORKSPACE", "eyebrow"))
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setAccessibleName("Editor sections")
        self.navigation.setSpacing(1)
        self.navigation.setIconSize(QSize(20, 20))
        for index, (title, _, _) in enumerate(SECTIONS):
            item = QListWidgetItem(navigation_icon(index), title)
            item.setSizeHint(QSize(176, 36))
            item.setToolTip(title)
            self.navigation.addItem(item)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        side.addWidget(self.navigation, 1)
        side.addSpacing(4)
        side.addWidget(label("●  Projects stay on your computer", "hint"))
        side.addWidget(label("An unofficial Stardew Valley companion", "hint", True))
        root.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(30, 22, 30, 0)
        main.setSpacing(18)
        root.addLayout(main, 1)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.breadcrumb = label("YOUR CHARACTER  /  IDENTITY", "breadcrumb")
        top.addWidget(self.breadcrumb)
        top.addStretch()
        self.save_state = label("Unsaved project", "saveState")
        self.save_state.setAccessibleName("Project save status")
        top.addWidget(self.save_state)
        self.save_button = button("Save project", self.save, "primary")
        self.save_button.setMinimumHeight(34)
        top.addWidget(self.save_button)
        main.addLayout(top)
        separator = QFrame()
        separator.setObjectName("workspaceSeparator")
        separator.setFixedHeight(1)
        main.addWidget(separator)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        self.title_label = label(SECTIONS[0][1], "title", True)
        self.subtitle = label(SECTIONS[0][2], "muted", True)
        heading.addWidget(self.title_label)
        heading.addWidget(self.subtitle)
        main.addLayout(heading)
        self.stack = QStackedWidget()
        self.identity = IdentityPage()
        self.identity.home_requested.connect(self.open_home_editor)
        self.dialogue = RecordsPage("dialogues")
        self.schedule = SchedulePage()
        self.gifts = GiftsPage(auto_download=self.auto_download_icons)
        self.gifts.download_stopped.connect(self._finish_icon_close)
        self.story = StoryPage(self)
        self.events = self.story.events
        self.relationships = self.story.relationships
        self.artwork = ArtworkPage(self)
        self.export_page = ExportPage(self)
        self.life = LifePage(self)
        self.world = WorldPage(self)
        self.creator = CreatorPage(self)
        for page in (self.identity, self.dialogue, self.schedule, self.gifts, self.story, self.artwork, self.export_page, self.creator, self.life, self.world):
            if page in (self.story, self.creator, self.life, self.world):
                self.stack.addWidget(page)
                continue
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        for page in (self.identity, self.dialogue, self.schedule, self.gifts, self.story, self.life, self.world):
            page.changed.connect(self.content_changed)
        self.artwork.changed.connect(self.artwork_changed)
        main.addWidget(self.stack, 1)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        self.statusBar().addPermanentWidget(label("PIXELHEART  " + __version__, "hint"))

    def build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        actions = [
            ("&New character", QKeySequence.StandardKey.New, self.new_character),
            ("&Open project…", QKeySequence.StandardKey.Open, self.open_dialog),
            ("&Save project", QKeySequence.StandardKey.Save, self.save),
            ("Save project &as…", QKeySequence.StandardKey.SaveAs, self.save_as),
            ("&Review and export…", "Ctrl+Shift+E", lambda: self.navigation.setCurrentRow(6)),
        ]
        for name, shortcut, callback in actions:
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addSeparator()
        close = QAction("&Close", self)
        close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(self.close)
        file_menu.addAction(close)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About Pixelheart", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    def navigate(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        name, title, subtitle = SECTIONS[index]
        self.breadcrumb.setText("YOUR CHARACTER  /  " + name.upper())
        self.title_label.setText(title)
        self.subtitle.setText(subtitle)
        if index == 6 and not self.loading:
            self.export_page.refresh()
        if index == 4 and not self.loading:
            self.story.refresh_context()
            self.events.update_preview()
        if index == 7 and not self.loading:
            self.collect()
            self.creator.refresh()
        if index == 8 and not self.loading and hasattr(self.life, "refresh_context"):
            self.life.refresh_context()

    def collect(self):
        self.document["character"].update(self.identity.dump())
        self.document["character"].update(
            dialogues=self.dialogue.dump(), schedule=self.schedule.dump(), gifts=self.gifts.dump(),
            events=self.events.dump(), relationships=self.relationships.dump(),
            life=self.life.dump(),
        )
        self.document["world"] = self.world.dump()
        snapshot = self.gifts.catalog_snapshot()
        if snapshot is None:
            self.document.pop("item_catalog", None)
        else:
            self.document["item_catalog"] = snapshot

    def content_changed(self):
        if self.loading:
            return
        self.collect()
        self.refresh_locations()
        self.dirty = True
        self.update_title()

    def refresh_locations(self):
        locations = {location.get("internal_name", ""): location.get("name") or "Untitled place"
                     for location in self.document.get("world", {}).get("locations", [])
                     if location.get("internal_name") and not location.get("spouse_room")}
        for selector in self.findChildren(MapSelector):
            if type(selector) is MapSelector:
                selector.set_extra_locations(locations)
        self.world.refresh_residents()

    def open_home_editor(self, companion_id=None, *, location_id=None):
        """Apply one resident's home and its maps as one local authoring edit."""
        from .home_editor import HomeDialog
        self.collect()
        primary = self.document["character"]
        character = primary
        if companion_id is not None:
            entry = next((entry for entry in self.document["world"]["characters"]
                          if entry["id"] == companion_id), None)
            if entry is None:
                return
            character = entry["character"]

        def ensure_home_project():
            return self.project_file if self.ensure_saved() else None

        dialog = HomeDialog(character, world=self.document["world"], primary=primary,
                            project_file=self.project_file, parent=self,
                            ensure_saved=ensure_home_project)
        try:
            if location_id is not None:
                location = next((place for place in self.document["world"]["locations"]
                                 if place["id"] == location_id), None)
                if location and not location["spouse_room"]:
                    dialog.select_home_map(location["internal_name"])
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.apply_home_edit(dialog.result_character, dialog.result_world, companion_id)
        finally:
            dialog.deleteLater()

    def apply_home_edit(self, character, world, companion_id=None):
        if character is None or world is None:
            return
        updated = deepcopy(self.document)
        updated["world"] = deepcopy(world)
        if companion_id is None:
            updated["character"] = deepcopy(character)
        else:
            entry = next((entry for entry in updated["world"]["characters"]
                          if entry["id"] == companion_id), None)
            if entry is None:
                return
            entry["character"] = deepcopy(character)
        if updated == self.document:
            return
        self.loading = True
        try:
            self.document = updated
            self.identity.load(updated["character"])
            self.schedule.load(updated["character"].get("schedule", []))
            self.life.load(updated["character"])
            self.world.load(updated["world"])
            self.refresh_locations()
        finally:
            self.loading = False
        self.dirty = True
        self.update_title()
        self.creator.refresh()
        self.statusBar().showMessage("Home updated. Review the daily route and test the entrance and evening return in-game.", 12000)

    def artwork_changed(self):
        self.content_changed()
        self.update_portrait()

    def update_title(self):
        name = self.document["character"].get("name") or "Untitled character"
        self.setWindowTitle(f"{name}[*] — Pixelheart")
        self.setWindowModified(self.dirty)
        self.save_state.setText("Unsaved changes" if self.dirty else "Saved locally" if self.project_file else "New project")
        self.save_state.setToolTip(str(self.project_file or "Choose Save project to select a portable project folder."))

    def load_document(self, document, path=None):
        self.loading = True
        self.document = deepcopy(document)
        self.project_file = project_path(path) if path else None
        character = self.document["character"]
        self.identity.load(character)
        self.dialogue.load(character["dialogues"])
        self.schedule.load(character["schedule"])
        self.gifts.load_catalog_snapshot(document.get("item_catalog"))
        self.gifts.load(character["gifts"])
        self.story.load(character)
        self.life.load(character)
        self.world.load(self.document.get("world", {}))
        self.refresh_locations()
        self.creator.load(self.document)
        self.artwork.select_appearance()
        self.artwork.refresh()
        self.update_portrait()
        self.dirty = False
        self.loading = False
        self.update_title()
        self.navigation.setCurrentRow(7)
        self.creator.refresh()
        self.statusBar().showMessage("Start in Create your mod, or open any editor to keep developing your project.", 12000)

    def update_portrait(self):
        try:
            path = resolve_artwork(self.document, self.project_file, "portrait") if self.project_file else None
            if path:
                inspect_artwork(path)
            self.identity.portrait.set_image(path, portrait=True)
        except (ProjectError, ArtworkValidationError):
            self.identity.portrait.set_image()

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = QMessageBox.warning(self, "Save your character?", "Your project has unsaved changes.",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def new_character(self):
        if self.confirm_discard():
            self.load_document(new_project())

    def open_dialog(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open a Pixelheart project", str(self.project_file.parent) if self.project_file else "", "Pixelheart project (*.json)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            document = load_project(path)
            self.load_document(document, path)
            return True
        except (ProjectError, OSError) as exc:
            self.show_error("Could not open project", str(exc))
            return False

    def ensure_saved(self):
        return bool(self.project_file) or self.save_as()

    def save(self):
        if not self.project_file:
            return self.save_as()
        return self.save_to(self.project_file)

    def save_as(self):
        initial = str(self.project_file) if self.project_file else str(Path.cwd() / "projects" / self.document["character"]["id"] / "character.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save a portable character project", initial, "Pixelheart project (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        return self.save_to(path)

    def save_to(self, path):
        self.collect()
        document = deepcopy(self.document)
        document["character"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            destination = project_path(path)
            if self.project_file and destination != self.project_file:
                saved = copy_project(document, self.project_file, destination)
                document = load_project(saved)
            else:
                saved = save_project(document, destination)
            self.project_file = saved
            self.document = document
            self.loading = True
            self.world.load(document.get("world", {}))
            self.loading = False
            self.dirty = False
            self.update_title()
            self.artwork.refresh()
            self.statusBar().showMessage(f"Saved · {saved}", 9000)
            return True
        except (ProjectError, OSError) as exc:
            self.show_error("Could not save project", str(exc))
            return False

    def resolved_artwork(self, kind, variant=None, issues=None):
        if not self.project_file:
            return None
        try:
            return resolve_artwork(self.document, self.project_file, kind, variant=variant)
        except ProjectError as exc:
            if issues is None:
                raise
            field = f"appearances.{variant}.{kind}" if variant else kind
            issues.append({"level": "error", "field": field, "message": str(exc)})
            return None

    def artwork_paths(self, issues=None):
        return tuple(self.resolved_artwork(kind, issues=issues) for kind in ("portrait", "sprite"))

    def artwork_appearances(self, issues=None):
        if not self.project_file:
            return {}
        return {
            variant: {kind: self.resolved_artwork(kind, variant=variant, issues=issues)
                      for kind in ("portrait", "sprite")}
            for variant in self.document["artwork"].get("variants", {})
        }

    def validate_project(self):
        self.collect()
        path_issues = []
        portrait, sprite = self.artwork_paths(issues=path_issues)
        appearances = self.artwork_appearances(issues=path_issues)
        issues = validate_character(self.document["character"], portrait, sprite, appearances=appearances,
                                    world=self.document.get("world"), project_root=self.project_file.parent if self.project_file else None)
        if path_issues:
            invalid_paths = {issue["field"] for issue in path_issues}
            issues = [issue for issue in issues if issue["field"] not in invalid_paths and issue["level"] != "success"] + path_issues
        try:
            authored = deepcopy({key: val for key, val in self.document["character"].items() if key in EDITABLE_FIELDS})
            # Portable projects may carry extension metadata. Validate only the
            # editable fields here, while keeping the full records in storage.
            nested_fields = {
                "dialogues": {"id", "trigger", "text"},
                "schedule": {"id", "time", "location", "x", "y", "facing", "activity"},
                "events": {"id", "name", "hearts", "location", "description", "story"},
                "relationships": {"id", "name", "relation", "description", "story"},
            }
            for group, fields in nested_fields.items():
                authored[group] = [{key: val for key, val in entry.items() if key in fields} for entry in authored[group]]
            authored["gifts"] = {key: val for key, val in authored["gifts"].items() if key in {"love", "like", "dislike", "hate"}}
            validate_draft(authored)
        except DraftValidationError as exc:
            existing = {issue["field"] for issue in issues if issue["level"] == "error"}
            issues.extend({"level": "error", "field": field, "message": message} for field, message in exc.errors.items() if field not in existing)
        vanilla_ids = {item["id"] for item in self.gifts.base_catalog["items"]}
        nonvanilla = {self.gifts.key(value) for taste in ("love", "like", "dislike", "hate")
                     for value in self.document["character"]["gifts"].get(taste, [])
                     if self.gifts.key(value) not in vanilla_ids}
        if nonvanilla:
            issues.append({"level": "warning", "field": "gifts", "message": "Some gift references are outside the bundled vanilla catalog. If they come from mods, players need those mods too. The item snapshot does not identify their owning mod; declare required dependencies before sharing the pack."})
        return issues

    def export_project(self):
        self.navigation.setCurrentRow(6)
        issues = self.export_page.refresh()
        if any(issue["level"] == "error" for issue in issues):
            return False
        name = self.document["character"]["internal_name"]
        path, _ = QFileDialog.getSaveFileName(self, "Export Content Patcher starter pack", str((self.project_file.parent if self.project_file else Path.cwd()) / f"[CP] {name}.zip"), "Mod archive (*.zip)")
        if not path:
            return False
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            portrait, sprite = self.artwork_paths()
            payload = build_mod_archive(self.document["character"], portrait, sprite, appearances=self.artwork_appearances(),
                                        world=self.document.get("world"), project_root=self.project_file.parent if self.project_file else None,
                                        project_document=self.document)
            output = QSaveFile(path)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(payload) != len(payload) or not output.commit():
                raise OSError(output.errorString())
            self.document = record_export(self.document, path, payload)
            self.dirty = True
            self.update_title()
            self.statusBar().showMessage(f"Exported · {path}", 15000)
            QMessageBox.information(self, "Your character is ready for a first visit", f"Saved {Path(path).name}.\n\nExtract the pack into your Stardew Valley Mods folder with SMAPI and Content Patcher installed. Test dialogue, gifts, the daily route, and artwork in-game before sharing.")
            return True
        except (ExportValidationError, OSError, ProjectError) as exc:
            self.show_error("Could not export character", str(exc))
            return False

    def show_error(self, title, message):
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, "", QMessageBox.StandardButton.Ok, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.exec()

    def about(self):
        QMessageBox.about(self, "About Pixelheart", f"Pixelheart {__version__}\n\nAn offline authoring companion for adult Stardew Valley NPCs.\n\nStardew Valley was created by ConcernedApe. Pixelheart is unofficial and is not affiliated with or endorsed by ConcernedApe.\n\nFree for personal, noncommercial use under the Pixelheart Source-Available License 1.1. See LICENSE in the project. No game artwork is bundled.")

    def closeEvent(self, event):
        if not self._closing_after_download and not self.confirm_discard():
            event.ignore()
            return
        self.gifts.cancel_wiki_download()
        if self.gifts.is_downloading_icons():
            self._closing_after_download = True
            self.setEnabled(False)
            self.statusBar().showMessage("Finishing the current icon download before closing…")
            event.ignore()
            return
        event.accept()

    def _finish_icon_close(self):
        if self._closing_after_download:
            self.close()
