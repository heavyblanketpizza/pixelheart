"""The desktop workspace and its project lifecycle."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QSaveFile, QIODevice, QTimer
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QScrollArea,
    QListWidget, QListWidgetItem, QFrame, QFileDialog, QMessageBox, QTabWidget,
    QApplication, QLineEdit, QPlainTextEdit, QTextEdit, QAbstractScrollArea, QDialog,
    QStyledItemDelegate,
)
from shiboken6 import isValid

from pixelheart_core.projects import (
    new_project, load_project, save_project, copy_project, project_path,
    resolve_artwork, ProjectError,
)
from pixelheart_core.validation import validate_draft, DraftValidationError, EDITABLE_FIELDS
from pixelheart_core.exporting import validate_character, build_mod_archive, ExportValidationError
from pixelheart_core.artwork import inspect_artwork, ArtworkValidationError
from pixelheart_core.interior_runtime import INTERIORS_MOD_ID, INTERIORS_MIN_GAME_VERSION, INTERIORS_MIN_SMAPI_VERSION
from pixelheart_core.romance import new_romance_events
from . import __version__
from .artwork_page import ArtworkPage
from .editors import IdentityPage, DialoguePage, SchedulePage
from .gifts_page import GiftsPage
from .romance_page import RomancePage
from .playtest_page import PlaytestPage
from .life_page import LifePage
from .world_page import WorldPage
from .location_picker import MapSelector
from pixelheart_core.playtesting import record_export
from .theme import apply_theme, heart_icon
from .game_connection import game_connection, open_find_game
from .skin import easy_read_enabled, set_easy_read
from .welcome_page import WelcomePage
from .overview_page import OverviewPage
from .new_character import NewCharacterDialog
from . import game_import
from pixelheart_core.library import default_library_root, internal_name_for, new_project_path, remember_recent
from pixelheart_core.progress import project_progress
from pixelheart_core.route_maps import load_route_map
from .navigation_icons import navigation_icon
from .widgets import label, button, card
from .project_history import ProjectHistoryController
from .autosave import AutosaveController


# Stable keys keep issue links and editor actions independent of sidebar order.
SECTIONS = [
    ("overview", "Overview", "Your character at a glance.", "See what's done and what to try next."),
    ("identity", "About them", "A new face in the valley.", "Their name, birthday, personality, and where they live."),
    ("dialogue", "Conversations", "Something only they would say.", "Everyday chats, reactions to your story, and married life."),
    ("schedule", "Daily routine", "Find their everyday rhythm.", "Where they go each day, and how that changes with seasons, weather, and marriage."),
    ("gifts", "Gifts", "It's the thought that counts.", "The gifts they love, like, dislike, and hate."),
    ("story", "Heart events", "Every heart brings you closer.", "Write and stage the scenes at each friendship level."),
    ("artwork", "Portraits & sprites", "Let their personality show.", "Their portraits, walking sprites, and seasonal outfits."),
    ("home", "Home", "Home", "Decorate their home before marriage and their spouse room after."),
    ("export", "Play in Stardew", "Bring them into Stardew Valley.", "Check your character, put them in your game, and playtest their story."),
]
SECTION_INDEX = {section[0]: index for index, section in enumerate(SECTIONS)}
# Sidebar rows carry their step's progress: True, False, or None (no heart).
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 1
# Pages that draw their own heading instead of the shared title.
UNTITLED_SECTIONS = {"home", "overview"}

# These describe actions already performed outside the editor. Undoing authored
# content must not claim that an older pack is still installed or exported.
EXTERNAL_HISTORY_FIELDS = {"exports", "installation", "installations", "last_export", "last_install", "updated_at"}


def scroll_page(page):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    return scroll


def _export_completion_message(filename, manifest):
    """Describe installation requirements from the archive actually written."""
    message = (f"Saved {filename}.\n\n"
               "Next, open Put in game & playtest to install it into your Mods folder. "
               "You'll need SMAPI and Content Patcher installed too.")
    required = [entry for entry in manifest.get("Dependencies", []) if entry.get("IsRequired", True)]
    if required:
        requirements = [entry["UniqueID"] + (f" {entry['MinimumVersion']}+" if entry.get("MinimumVersion") else "")
                        for entry in required]
        message += "\n\nInstall these required mods separately:\n" + "\n".join("• " + name for name in requirements)
    if any(entry["UniqueID"].casefold() == INTERIORS_MOD_ID.casefold() for entry in required):
        message += ("\n\nPixelheart Interiors is the required companion for designed homes and spouse rooms. "
                    "Its DLL is not included in this ZIP. Follow the companion build and installation steps in WORLD_BUILDING.md. "
                    f"Designed interiors need Stardew Valley {INTERIORS_MIN_GAME_VERSION}+ and SMAPI {INTERIORS_MIN_SMAPI_VERSION}+.")
    return message + "\n\nStart Stardew Valley through SMAPI and say hello in a test save before sharing!"



class ExportPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.issues = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs)
        checks = QWidget()
        root = QVBoxLayout(checks)
        root.setContentsMargins(0, 14, 10, 10)
        root.setSpacing(20)
        summary, content = card("Before they move to the valley")
        self.summary = label("Check your character before exporting.", "profileName", True)
        content.addWidget(self.summary)
        content.addWidget(label("These checks cover your project and artwork sizes. Walking routes and how scenes feel still need a look in the game.", "muted", True))
        actions = QHBoxLayout()
        actions.addWidget(button("Check again", self.refresh))
        actions.addStretch()
        self.export_button = button("Export for Stardew…", window.export_project, "primary")
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
        root.addWidget(label("WHAT GOES INTO YOUR GAME", "eyebrow"))
        root.addWidget(label("Everything you wrote that's marked ready: who they are, what they say, their gifts, routines, portraits and sprites, heart events, and home. Unfinished drafts stay safely in your project.", "muted", True))
        root.addWidget(label("You'll need SMAPI 4+ and Content Patcher 2.9+ for Stardew Valley 1.6. Try your character in a test save before sharing.", "notice", True))
        root.addStretch()
        self.tabs.addTab(scroll_page(checks), "Check && export")
        self.playtest = PlaytestPage(window)
        self.tabs.addTab(scroll_page(self.playtest), "Put in game && playtest")
        self.tabs.currentChanged.connect(self.refresh_tab)

    def refresh_tab(self, index):
        if not self.window.loading:
            if index == 0:
                self.refresh()
            else:
                self.window.collect()
                self.playtest.refresh()

    def refresh(self):
        self.issues = self.window.validate_project()
        self.list.clear()
        counts = {level: sum(issue["level"] == level for issue in self.issues) for level in ("error", "warning", "success")}
        errors, warnings = counts["error"], counts["warning"]
        self.summary.setText(f"{errors} {'thing' if errors == 1 else 'things'} to fix before they can move in." if errors else "Ready to meet you in Stardew Valley.")
        for issue in sorted(self.issues, key=lambda item: {"error": 0, "warning": 1, "success": 2}[item["level"]]):
            prefix = {"error": "Fix", "warning": "Check", "success": "Ready"}[issue["level"]]
            item = QListWidgetItem(f"{prefix}  ·  {issue['field'].replace('_', ' ').capitalize()}\n{issue['message']}")
            item.setData(Qt.ItemDataRole.UserRole, issue)
            item.setToolTip("Activate to open the related editor")
            self.list.addItem(item)
        self.export_button.setEnabled(errors == 0)
        self.window.statusBar().showMessage(f"Checked · {errors} to fix · {warnings} to look at", 8000)
        return self.issues

    def open_issue(self, item):
        field = item.data(Qt.ItemDataRole.UserRole)["field"]
        group = field.split(".")[0]
        destination = {"dialogues": "dialogue", "schedule": "schedule", "gifts": "gifts", "events": "story", "relationships": "story", "storyline": "story", "portrait": "artwork", "sprite": "artwork", "appearances": "artwork", "life": "dialogue", "world": "home"}.get(group, "identity")
        self.window.open_section(destination)
        if destination == "story":
            self.window.story.open_issue(field)
        if group == "life":
            self.window.life.open_issue(field)
        elif group == "dialogues":
            self.window.dialogue_tabs.setCurrentIndex(0)
        elif group == "schedule":
            self.window.schedule_tabs.setCurrentIndex(0)
        if destination == "home":
            self.window.world.open_issue(field)
        if destination == "artwork":
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


class NavigationDelegate(QStyledItemDelegate):
    """Paint a small friendship heart on sidebar rows that track progress."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        done = index.data(PROGRESS_ROLE)
        if done is None:
            return
        from . import skin
        piece = skin.current_pieces().get("heart_full" if done else "heart_empty")
        if piece is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(option.rect.right() - 24, option.rect.center().y() - 6, 14, 12, QPixmap(str(piece.path)))
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, path=None, *, auto_download_icons=False, preload_story=True, show_welcome=False):
        super().__init__()
        self.auto_download_icons = auto_download_icons
        self.preload_story = preload_story
        self._closing_after_download = False
        self.document = new_project()
        if preload_story and not path:
            self.document["character"]["events"] = new_romance_events(self.document["character"])
        self.project_file = None
        self.dirty = False
        self._external_dirty = False
        self.loading = True
        self.autosave_error = None
        self.room_save_issue = None
        self._last_saved_at = None
        self.autosave = AutosaveController(self)
        self.project_history = ProjectHistoryController(self)
        self.setWindowIcon(heart_icon())
        self.setMinimumSize(1020, 700)
        self.resize(1360, 900)
        self.build_workspace()
        self.build_menus()
        self._workspace = self.takeCentralWidget()
        self.welcome = WelcomePage(self)
        self.root_stack = QStackedWidget()
        self.root_stack.addWidget(self.welcome)
        self.root_stack.addWidget(self._workspace)
        self.setCentralWidget(self.root_stack)
        self.root_stack.setCurrentWidget(self._workspace)
        game_connection().changed.connect(self._game_changed)
        # Notice a game drive plugged in (or removed) while Pixelheart was in the background.
        QApplication.instance().applicationStateChanged.connect(self._application_state_changed)
        self.load_document(self.document)
        if path:
            self.open_path(path)
        elif show_welcome:
            self.root_stack.setCurrentWidget(self.welcome)

    def build_workspace(self):
        central = QWidget()
        central.setObjectName("workspace")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(272)
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
        new_button = button("+  New character", self.start_new_character, "sidebarAction")
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
        self.navigation.setItemDelegate(NavigationDelegate(self.navigation))
        for key, title, _, _ in SECTIONS:
            item = QListWidgetItem(navigation_icon(key), title)
            item.setSizeHint(QSize(176, 36))
            item.setToolTip(title)
            self.navigation.addItem(item)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        side.addWidget(self.navigation, 1)
        side.addSpacing(4)
        side.addWidget(label("Your characters stay on your computer. An unofficial fan tool for Stardew Valley.", "hint", True))
        root.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(20, 22, 20, 0)
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
        self.save_button = button("Save", self.save, "primary")
        self.save_button.setMinimumHeight(34)
        top.addWidget(self.save_button)
        main.addLayout(top)
        separator = QFrame()
        separator.setObjectName("workspaceSeparator")
        separator.setFixedHeight(1)
        main.addWidget(separator)
        heading = QVBoxLayout()
        heading.setSpacing(6)
        self.title_label = label(SECTIONS[0][2], "title", True)
        self.subtitle = label(SECTIONS[0][3], "muted", True)
        heading.addWidget(self.title_label)
        heading.addWidget(self.subtitle)
        main.addLayout(heading)
        self.stack = QStackedWidget()
        self.overview = OverviewPage(self)
        self.identity = IdentityPage()
        self.dialogue = DialoguePage(self)
        self.schedule = SchedulePage()
        self.gifts = GiftsPage(auto_download=self.auto_download_icons)
        self.gifts.download_stopped.connect(self._finish_icon_close)
        self.story = RomancePage(self)
        self.events = self.story.events
        self.events.actors.set_project_history(self.project_history)
        self.artwork = ArtworkPage(self)
        self.life = LifePage(self)
        self.world = WorldPage(self)
        self.world.settings_dialog.setProperty("projectHistoryLive", True)
        self.world.draft_changed.connect(self.interior_draft_changed)
        self.export_page = ExportPage(self)
        self.playtest = self.export_page.playtest
        self.dialogue_tabs = QTabWidget()
        self.dialogue_tabs.setDocumentMode(True)
        self.dialogue_tabs.addTab(scroll_page(self.dialogue), "Everyday dialogue")
        self.dialogue_tabs.addTab(self.life.editors["dialogues"], "Story reactions")
        self.dialogue_tabs.addTab(self.life.editors["spouse_dialogue"], "Marriage dialogue")
        self.schedule_tabs = QTabWidget()
        self.schedule_tabs.setDocumentMode(True)
        self.schedule_tabs.addTab(scroll_page(self.schedule), "Daily route")
        self.schedule_tabs.addTab(self.life.editors["routines"], "Conditional routines")
        for tabs in (self.dialogue_tabs, self.schedule_tabs):
            tabs.currentChanged.connect(lambda *_: self.life.refresh_context() if not self.loading else None)
        self.section_pages = {
            "overview": scroll_page(self.overview), "identity": scroll_page(self.identity), "dialogue": self.dialogue_tabs,
            "schedule": self.schedule_tabs, "gifts": scroll_page(self.gifts),
            "story": self.story, "artwork": scroll_page(self.artwork),
            "home": self.world, "export": self.export_page,
        }
        for key, *_ in SECTIONS:
            self.stack.addWidget(self.section_pages[key])
        for page in (self.identity, self.dialogue, self.schedule, self.gifts, self.story, self.life, self.world):
            page.changed.connect(self.content_changed)
        self.life.changed.connect(self.story.refresh_context)
        self.artwork.changed.connect(self.artwork_changed)
        main.addWidget(self.stack, 1)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)
        self.statusBar().addPermanentWidget(label("PIXELHEART  " + __version__, "hint"))
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(250)
        self._progress_timer.timeout.connect(self.refresh_progress)
        for tabs in self.findChildren(QTabWidget):
            tabs.currentChanged.connect(self.project_history.close_group)
        for editor in self.history_record_editors():
            editor.list.currentRowChanged.connect(self.project_history.close_group)
        self.playtest.tests.currentRowChanged.connect(self.project_history.close_group)
        for page in self.route_pages():
            page.set_map_source(self.route_map)
            page.map_panel.gestureStarted.connect(lambda page=page: self.project_history.begin_gesture(("route", id(page))))
            page.map_panel.gestureFinished.connect(self.project_history.end_gesture)
        self.schedule.table.currentCellChanged.connect(self.project_history.close_group)
        self.life.editors["routines"].schedule.table.currentCellChanged.connect(self.project_history.close_group)
        self.events.actors.table.currentCellChanged.connect(self.project_history.close_group)
        self.world.location_list.currentRowChanged.connect(self.project_history.close_group)
        self.world.dependency_list.currentRowChanged.connect(self.project_history.close_group)

    def build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        actions = [
            ("&Home", "Ctrl+Shift+H", self.show_home),
            ("&New character", QKeySequence.StandardKey.New, self.start_new_character),
            ("&Open…", QKeySequence.StandardKey.Open, self.open_dialog),
            ("&Save", QKeySequence.StandardKey.Save, self.save),
            ("Save a &copy…", "Ctrl+Shift+S", self.save_as),
            ("&Play in Stardew…", "Ctrl+Shift+E", self.open_play),
        ]
        for name, shortcut, callback in actions:
            action = QAction(name, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
            if callback == self.save:
                self.autosave_action = QAction("Save &automatically", self)
                self.autosave_action.setCheckable(True)
                self.autosave_action.setChecked(self.autosave.enabled)
                self.autosave_action.toggled.connect(self.toggle_autosave)
                file_menu.addAction(self.autosave_action)
        file_menu.addSeparator()
        close = QAction("&Close", self)
        close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(self.close)
        file_menu.addAction(close)
        self.project_history.install_actions(self.menuBar().addMenu("&Edit"))
        view_menu = self.menuBar().addMenu("&View")
        find = QAction("&Find Stardew Valley…", self)
        find.triggered.connect(self.find_game)
        view_menu.addAction(find)
        self.easy_read_action = QAction("&Easier-to-read text", self)
        self.easy_read_action.setCheckable(True)
        self.easy_read_action.setChecked(easy_read_enabled())
        self.easy_read_action.toggled.connect(self.toggle_easy_read)
        view_menu.addAction(self.easy_read_action)
        help_menu = self.menuBar().addMenu("&Help")
        guide = QAction("&Getting started", self)
        guide.triggered.connect(self.getting_started)
        help_menu.addAction(guide)
        about = QAction("About Pixelheart", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    def open_play(self):
        self.show_workspace()
        self.open_section("export")

    def open_section(self, key):
        self.navigation.setCurrentRow(SECTION_INDEX[key])

    def open_life_editor(self, kind):
        if kind == "routines":
            self.open_section("schedule")
            self.schedule_tabs.setCurrentIndex(1)
        else:
            self.open_section("dialogue")
            self.dialogue_tabs.setCurrentIndex(2 if kind == "spouse_dialogue" else 1)
        self.life.refresh_context()

    def navigate(self, index):
        if index < 0:
            return
        self.project_history.close_group()
        if (not self.loading and self.stack.currentIndex() == SECTION_INDEX["home"]
                and index != SECTION_INDEX["home"] and not self.world.flush_editor()):
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(SECTION_INDEX["home"])
            self.navigation.blockSignals(False)
            return
        self.stack.setCurrentIndex(index)
        key, name, title, subtitle = SECTIONS[index]
        character_name = self.document["character"].get("name") or "Your character"
        self.breadcrumb.setText(character_name.upper() + "  /  " + name.upper())
        self.title_label.setText(title)
        self.subtitle.setText(subtitle)
        self.title_label.setVisible(key not in UNTITLED_SECTIONS)
        self.subtitle.setVisible(key not in UNTITLED_SECTIONS)
        if self.loading:
            return
        if key == "overview":
            self.overview.refresh(project_progress(self.document), blockers=self.validate_project())
        elif key == "export":
            self.export_page.refresh_tab(self.export_page.tabs.currentIndex())
        elif key == "story":
            self.story.refresh_context()
            self.events.update_preview()
        elif key in ("dialogue", "schedule"):
            self.life.refresh_context()
        elif key == "home":
            self.world.activate_workspace()

    def interior_draft_changed(self):
        if not self.loading:
            self.project_history.record_current()

    def collect(self):
        self.document["character"].update(self.identity.dump())
        self.document["character"].update(
            dialogues=self.dialogue.dump(), schedule=self.schedule.dump(), gifts=self.gifts.dump(),
            life=self.life.dump(),
        )
        self.document["character"].update(self.story.dump())
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
        self.world.refresh_home_actions()
        self.project_history.record_current()
        self._progress_timer.start()

    def history_record_editors(self):
        return (self.dialogue, self.events, *self.life.editors.values(), self.events.beats)

    def project_history_context(self):
        selected = []
        for editor in self.history_record_editors():
            index = editor.current
            selected.append(editor.records[index].get("id", index) if 0 <= index < len(editor.records) else None)
        return (self.stack.currentIndex(), self.dialogue_tabs.currentIndex(), self.schedule_tabs.currentIndex(),
                self.events.phases.currentIndex(), *selected, self.schedule.table.currentRow(),
                self.schedule.table.currentColumn(), self.playtest.current_test)

    def project_snapshot(self):
        """Read all editors, including the uncommitted Home draft, without closing it."""
        self.collect()
        return self._history_form(self.world.history_snapshot(self.document))

    @staticmethod
    def _history_form(document):
        """A document as undo history compares it, without external bookkeeping."""
        snapshot = deepcopy(document)
        creator = snapshot.get("creator", {})
        for key in EXTERNAL_HISTORY_FIELDS:
            creator.pop(key, None)
        if not creator:
            snapshot.pop("creator", None)
        return snapshot

    def project_edited(self):
        """Called by project history after each recorded change, undo or redo."""
        self.autosave.schedule()
        self.update_title()

    def refresh_progress(self):
        """Update sidebar hearts and the Overview from the current document."""
        steps = project_progress(self.document)
        for step in steps:
            item = self.navigation.item(SECTION_INDEX[step["key"]])
            item.setData(PROGRESS_ROLE, step["done"])
            item.setToolTip(f"{step['title']} · {step['status']}")
        self.overview.refresh(steps)
        return steps

    def external_project_changed(self):
        self._external_dirty = True
        self._progress_timer.start()
        self.project_history.close_group()
        self.project_history.sync()
        self.project_edited()

    def restore_project_snapshot(self, snapshot):
        """Restore authored content in place, retaining the user's workspace."""
        selections = []
        for editor in self.history_record_editors():
            index = editor.current
            identity = editor.records[index].get("id") if 0 <= index < len(editor.records) else None
            selections.append((editor, identity, index))
        tabs = [(widget, widget.currentIndex()) for widget in self.findChildren(QTabWidget)]
        scrolls = [(widget, widget.horizontalScrollBar().value(), widget.verticalScrollBar().value())
                   for widget in self.findChildren(QAbstractScrollArea)]
        schedules = []
        for editor in (self.schedule, self.life.editors["routines"].schedule):
            row, column = editor.table.currentRow(), editor.table.currentColumn()
            identity = editor.records[row].get("id") if 0 <= row < len(editor.records) else None
            schedules.append((editor, row, column, identity))
        story_filters = [(self.events, self.events.search.text(), self.events.filter.currentIndex())]
        actor_cell = (self.events.actors.table.currentRow(), self.events.actors.table.currentColumn())
        focus = QApplication.focusWidget()
        if focus not in self.findChildren(QWidget):
            focus = None
        focus_owner = focus
        while focus_owner is not None and not focus_owner.accessibleName():
            focus_owner = focus_owner.parentWidget()
        focus_name = focus_owner.accessibleName() if focus_owner else ""
        cursor = focus.cursorPosition() if isinstance(focus, QLineEdit) else None
        text_position = focus.textCursor().position() if isinstance(focus, (QPlainTextEdit, QTextEdit)) else None
        external = {key: deepcopy(value) for key, value in self.document.get("creator", {}).items()
                    if key in EXTERNAL_HISTORY_FIELDS}
        self.loading = True
        try:
            self.document = deepcopy(snapshot)
            if external:
                self.document.setdefault("creator", {}).update(external)
            character = self.document["character"]
            self.identity.load(character)
            self.dialogue.load(character["dialogues"])
            self.schedule.load(character["schedule"])
            self.gifts.load_catalog_snapshot(self.document.get("item_catalog"))
            self.gifts.load(character["gifts"])
            self.story.load(character)
            self.life.load(character)
            self.story.refresh_context()
            self.world.load(self.document.get("world", {}), from_history=True)
            self.refresh_locations()
            for editor, identity, index in selections:
                # Romance resolves missing/restored scenes within the active
                # heart milestone during load. A flat-index fallback would
                # jump to another milestone or clear a newly restored scene.
                fallback = editor.current if editor is self.events and getattr(editor, "romance", False) else min(index, len(editor.records) - 1)
                selected = next((row for row, record in enumerate(editor.records)
                                 if identity is not None and record.get("id") == identity),
                                fallback)
                editor.list.setCurrentRow(selected)
            for editor, query, index in story_filters:
                editor.search.blockSignals(True)
                editor.filter.blockSignals(True)
                editor.search.setText(query)
                editor.filter.setCurrentIndex(index)
                editor.search.blockSignals(False)
                editor.filter.blockSignals(False)
                editor.loading = True
                try:
                    editor.apply_filter()
                    # Keep the record being edited visible even if its restored
                    # name no longer matches the search, as ordinary typing does.
                    if editor.list.currentItem() is not None:
                        editor.list.currentItem().setHidden(False)
                        if hasattr(editor, "no_matches"):
                            editor.no_matches.hide()
                finally:
                    editor.loading = False
            cells = [(self.events.actors.table, actor_cell)]
            for editor, row, column, identity in schedules:
                if identity is not None:
                    row = next((index for index, record in enumerate(editor.records)
                                if record.get("id") == identity), row)
                cells.append((editor.table, (row, column)))
            for table, cell in cells:
                if cell[0] >= 0 and table.rowCount():
                    table.setCurrentCell(min(cell[0], table.rowCount() - 1), max(0, cell[1]))
            for widget, index in tabs:
                if isValid(widget):
                    widget.setCurrentIndex(index)
            self.artwork.refresh()
            self.update_portrait()
            self.playtest.refresh()
            for dialog in self.findChildren(QDialog):
                refresh = getattr(dialog, "refresh_project_history", None)
                if dialog.isVisible() and callable(refresh):
                    refresh()
        finally:
            self.loading = False
        if self.stack.currentIndex() == SECTION_INDEX["home"]:
            self.world.activate_workspace()
        elif self.stack.currentIndex() == SECTION_INDEX["export"] and self.export_page.tabs.currentIndex() == 0:
            self.export_page.refresh()
        for widget, horizontal, vertical in scrolls:
            if isValid(widget):
                widget.horizontalScrollBar().setValue(horizontal)
                widget.verticalScrollBar().setValue(vertical)
        if focus is not None:
            target = focus if isValid(focus) and focus in self.findChildren(QWidget) and focus.isVisible() else None
            if target is None and focus_name:
                owner = next((widget for widget in self.findChildren(QWidget)
                              if widget.accessibleName() == focus_name and widget.isVisible()), None)
                if owner is not None:
                    target = owner if isinstance(owner, type(focus)) else owner.findChild(type(focus))
            if target is not None:
                target.setFocus(Qt.FocusReason.OtherFocusReason)
                if cursor is not None and isinstance(target, QLineEdit):
                    target.setCursorPosition(min(cursor, len(target.text())))
                elif text_position is not None and isinstance(target, (QPlainTextEdit, QTextEdit)):
                    current = target.textCursor()
                    current.setPosition(min(text_position, target.document().characterCount() - 1))
                    target.setTextCursor(current)

    def clear_text_history(self):
        for widget in self.findChildren(QWidget):
            if isinstance(widget, QLineEdit):
                cursor = widget.cursorPosition()
                selection = widget.selectionStart()
                selected_length = len(widget.selectedText())
                blocked = widget.blockSignals(True)
                widget.setText(widget.text())
                if selection >= 0:
                    anchor = selection + selected_length if cursor == selection else selection
                    widget.setSelection(anchor, cursor - anchor)
                else:
                    widget.setCursorPosition(cursor)
                widget.blockSignals(blocked)
            elif isinstance(widget, (QPlainTextEdit, QTextEdit)):
                widget.document().clearUndoRedoStacks()

    def refresh_locations(self):
        locations = {location.get("internal_name", ""): location.get("name") or "Untitled place"
                     for location in self.document.get("world", {}).get("locations", [])
                     if location.get("internal_name") and not location.get("spouse_room")}
        for selector in self.findChildren(MapSelector):
            if type(selector) is MapSelector:
                selector.set_extra_locations(locations)

    def artwork_changed(self):
        self.content_changed()
        self.update_portrait()
        self.story.refresh_context()

    def update_title(self):
        name = self.document["character"].get("name") or "Untitled character"
        self.setWindowTitle(f"{name}[*] — Pixelheart")
        section = SECTIONS[max(0, self.navigation.currentRow())][1]
        self.breadcrumb.setText(name.upper() + "  /  " + section.upper())
        self.setWindowModified(self.dirty)
        text, tip, action = self._save_status()
        self.save_state.setText(text)
        self.save_state.setToolTip(tip)
        self.save_button.setText(action or "Save")
        self.save_button.setVisible(action is not None)

    def _save_status(self):
        """Status text, its tooltip, and the save button's text (None hides the button)."""
        if not self.project_file:
            return ("Unsaved changes" if self.dirty else "New project"), "Choose Save to pick a portable project folder.", "Save"
        where = str(self.project_file)
        if not self.autosave.enabled:
            return ("Unsaved changes" if self.dirty else "Saved locally"), where, "Save"
        if self.autosave_error:
            return "Couldn't save your changes", self.autosave_error, "Try again"
        if self.dirty and self.room_save_issue and not self.autosave.pending():
            return ("Saved, except the room you're editing",
                    f"The room you're editing in Home isn't saved yet: {self.room_save_issue}", None)
        if self.dirty:
            return "Saving…", where, None
        if self._last_saved_at is not None:
            moment = self._last_saved_at
            where += f"\nLast saved at {moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
        return "All changes saved", where, None

    def toggle_autosave(self, enabled):
        self.autosave.set_enabled(enabled)
        self.update_title()

    def quiet_save(self):
        """Write the project without finalizing Home or starting a fresh undo history.

        An open Home room is saved as it is. If the project refuses it, the
        rest is saved with the room's last applied version, and the room's
        changes stay in the editor and in Undo.
        """
        if not self.project_file:
            return False
        self.collect()
        problem = self.world.copy_history_assets(self.project_file)
        if problem:
            return self._autosave_failed(problem)
        projected, room_issue = self.world.quiet_save_document(self.document, self.project_file)
        candidates = [projected]
        if room_issue is None and self.world.room_draft_open():
            candidates.append(deepcopy(self.document))
        stamp = datetime.now(timezone.utc).isoformat()
        error = None
        for index, candidate in enumerate(candidates):
            candidate["character"]["updated_at"] = stamp
            try:
                save_project(candidate, self.project_file)
            except (ValueError, OSError) as exc:
                error = str(exc)
                continue
            if index:
                room_issue = error
            break
        else:
            return self._autosave_failed(error)
        self._external_dirty = False
        self.autosave_error = None
        self.room_save_issue = room_issue
        self._last_saved_at = datetime.now()
        # Compare later edits with what was written: the whole live project, or
        # everything except the room that could not be saved yet.
        saved = self.project_snapshot() if room_issue is None else self._history_form(self.document)
        self.project_history.mark_clean(saved)
        return room_issue is None

    def _autosave_failed(self, message):
        self.autosave_error = message or "The project could not be written."
        self.update_title()
        return False

    def load_document(self, document, path=None):
        self.loading = True
        self.autosave.timer.stop()
        self.autosave_error = self.room_save_issue = self._last_saved_at = None
        self.world.reset_workspace()
        self.document = deepcopy(document)
        self.project_file = project_path(path) if path else None
        self.dialogue.set_project_file(self.project_file)
        character = self.document["character"]
        self.identity.load(character)
        self.dialogue.load(character["dialogues"])
        self.schedule.load(character["schedule"])
        self.gifts.load_catalog_snapshot(document.get("item_catalog"))
        self.gifts.load(character["gifts"])
        self.story.load(character)
        self.life.load(character)
        self.story.refresh_context()
        self.world.load(self.document.get("world", {}))
        self.refresh_locations()
        self.artwork.select_appearance()
        self.artwork.refresh()
        self.update_portrait()
        self.dirty = False
        self.loading = False
        self.update_title()
        self.dialogue_tabs.setCurrentIndex(0)
        self.schedule_tabs.setCurrentIndex(0)
        self.export_page.tabs.setCurrentIndex(0)
        self.refresh_progress()
        self.open_section("overview")
        self.navigate(SECTION_INDEX["overview"])
        self.playtest.refresh()
        self._external_dirty = False
        self.project_history.reset(self.project_snapshot())
        self.clear_text_history()
        if self.project_file:
            name = self.document["character"].get("name") or "your character"
            self.statusBar().showMessage(f"Welcome back to {name}'s story.", 12000)
        else:
            self.statusBar().clearMessage()

    def route_pages(self):
        return (self.schedule, self.life.editors["routines"].schedule)

    def route_map(self, location):
        """The picture of a routine stop's place, from the project or the connected game."""
        connection = game_connection()
        game_root = connection.install().root if connection.state() == "connected" else None
        return load_route_map(self.document, self.project_file, location, game_root=game_root)

    def update_portrait(self):
        try:
            path = resolve_artwork(self.document, self.project_file, "portrait") if self.project_file else None
            if path:
                inspect_artwork(path)
            self.identity.portrait.set_image(path, portrait=True)
        except (ProjectError, ArtworkValidationError):
            path = None
            self.identity.portrait.set_image()
        # Dialogue shows the character's own expressions on its feeling buttons and box.
        self.dialogue.set_portrait(path)
        for editor in self.life.editors.values():
            editor.set_portrait(path)
        # Routine maps show the character standing at each stop.
        try:
            sprite = resolve_artwork(self.document, self.project_file, "sprite") if self.project_file else None
        except ProjectError:
            sprite = None
        image = QImage(str(sprite)) if sprite else QImage()
        for page in self.route_pages():
            page.set_sprite(image)

    def confirm_discard(self):
        autosaving = bool(self.project_file) and self.autosave.enabled
        if self.dirty and autosaving:
            self.autosave.flush()
        if not self.dirty:
            return True
        message = "Your project has unsaved changes."
        if autosaving and self.room_save_issue:
            message = f"The room you're editing in Home isn't saved yet: {self.room_save_issue}"
        elif autosaving and self.autosave_error:
            message = f"Pixelheart couldn't save your changes: {self.autosave_error}"
        answer = QMessageBox.warning(self, "Save your character?", message,
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    RECENT_KEY = "projects/recent"

    def recent_projects(self):
        value = game_import.game_import_settings().value(self.RECENT_KEY, [])
        rows = value if isinstance(value, list) else [value] if isinstance(value, str) and value else []
        return [row for row in rows if isinstance(row, str) and row]

    def remember_project(self, path):
        settings = game_import.game_import_settings()
        settings.setValue(self.RECENT_KEY, remember_recent(self.recent_projects(), path))
        settings.sync()

    def forget_project(self, path):
        settings = game_import.game_import_settings()
        settings.setValue(self.RECENT_KEY, [row for row in self.recent_projects() if row != str(Path(path))])
        settings.sync()

    def start_new_character(self):
        """Ask only for a name, then save straight into the character library."""
        if not self.confirm_discard():
            return False
        dialog = NewCharacterDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            name, romanceable = dialog.values()
        finally:
            dialog.deleteLater()
        document = new_project()
        character = document["character"]
        character.update(name=name, internal_name=internal_name_for(name), romanceable=romanceable)
        if self.preload_story:
            character["events"] = new_romance_events(character)
        self.load_document(document)
        try:
            destination = new_project_path(default_library_root(), name)
        except (OSError, ValueError):
            destination = None
        if destination is None or not self.save_to(destination):
            self.save_as()
        self.show_workspace()
        self.open_section("overview")
        return True

    def show_workspace(self):
        self.root_stack.setCurrentWidget(self._workspace)

    def show_home(self):
        """Return to the welcome screen, offering to save first."""
        if not self.confirm_discard():
            return False
        self.dirty = False
        self.update_title()
        self.welcome.refresh()
        self.root_stack.setCurrentWidget(self.welcome)
        return True

    def _application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            game_connection().refresh()
        elif self.dirty:
            # Leaving Pixelheart is a natural moment to save.
            self.autosave.flush()

    def _game_changed(self):
        apply_theme(QApplication.instance())
        self.welcome.refresh_game()
        self.dialogue.set_project_file(self.project_file)
        for page in self.route_pages():
            page.set_map_source(self.route_map)
        if not self.loading:
            self.story.refresh_context()

    def toggle_easy_read(self, enabled):
        set_easy_read(enabled)
        apply_theme(QApplication.instance())

    def find_game(self):
        open_find_game(self)

    def getting_started(self):
        QMessageBox.information(self, "Getting started", (
            "1. Pixelheart finds Stardew Valley for you. If it can't, choose View → Find Stardew Valley…\n"
            "2. Choose New character and give them a name.\n"
            "3. Follow the hearts on Overview: conversations, routine, gifts, heart events, and artwork.\n"
            "4. Open Play in Stardew to put them in your game. You'll need SMAPI and Content Patcher.\n"
            "5. Start Stardew Valley through SMAPI and go say hello!"))

    def new_character(self):
        if self.confirm_discard():
            document = new_project()
            if self.preload_story:
                document["character"]["events"] = new_romance_events(document["character"])
            self.load_document(document)

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
            self.remember_project(self.project_file)
            self.show_workspace()
            return True
        except (ProjectError, OSError) as exc:
            self.show_error("Could not open project", str(exc))
            return False

    def ensure_saved(self):
        return bool(self.project_file) or self.save_as()

    def save(self):
        if not self.project_file:
            return self.save_as()
        if self.autosave.enabled:
            saved = self.autosave.flush(force=True)
            if not saved and self.autosave_error:
                # Saving on request reports failures as it always has; automatic saves stay quiet.
                self.show_error("Could not save project", self.autosave_error)
            return saved
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
        self.project_history.close_group()
        if not self.world.flush_editor():
            return False
        if not self.world.publish_history_assets():
            if self.navigation.currentRow() == SECTION_INDEX["home"]:
                self.world.activate_workspace()
            return False
        self.collect()
        document = deepcopy(self.document)
        document["character"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            destination = project_path(path)
            source = self.project_file or self.world.draft_project_file
            if source and destination != source:
                saved = copy_project(document, source, destination)
                document = load_project(saved)
            else:
                saved = save_project(document, destination)
            self.project_file = saved
            self.dialogue.set_project_file(saved)
            self.document = document
            self.loading = True
            self.world.load(document.get("world", {}))
            self.world.reset_history_resources()
            self.loading = False
            self._external_dirty = False
            self.autosave.timer.stop()
            self.autosave_error = self.room_save_issue = None
            self._last_saved_at = datetime.now()
            self.project_history.reset(self.project_snapshot())
            self.clear_text_history()
            self.artwork.refresh()
            if self.navigation.currentRow() == SECTION_INDEX["home"]:
                self.world.activate_workspace()
            self.remember_project(saved)
            self.statusBar().showMessage(f"Saved · {saved}", 9000)
            return True
        except (ProjectError, OSError) as exc:
            self.show_error("Could not save project", str(exc))
            if self.navigation.currentRow() == SECTION_INDEX["home"]:
                self.world.activate_workspace()
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
        if not self.world.flush_editor():
            return [{"level": "error", "field": "world", "message": "Finish the current interior edit in Home before reviewing the project."}]
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
        self.open_section("export")
        self.export_page.tabs.setCurrentIndex(0)
        issues = self.export_page.refresh()
        if any(issue["level"] == "error" for issue in issues):
            return False
        name = self.document["character"]["internal_name"]
        path, _ = QFileDialog.getSaveFileName(self, "Export NPC Content Patcher pack", str((self.project_file.parent if self.project_file else Path.cwd()) / f"[CP] {name}.zip"), "Mod archive (*.zip)")
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
            self.playtest.refresh()
            self.external_project_changed()
            self.statusBar().showMessage(f"Exported · {path}", 15000)
            import io
            import json
            import zipfile
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                manifest = json.loads(archive.read(f"[CP] {name}/manifest.json"))
            QMessageBox.information(self, "Exported for playtesting", _export_completion_message(Path(path).name, manifest))
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
        QMessageBox.about(self, "About Pixelheart", f"Pixelheart {__version__}\n\nA fan-made character studio for Stardew Valley.\n\nStardew Valley is by ConcernedApe. Pixelheart is unofficial and not affiliated with or endorsed by ConcernedApe.\n\nFree for personal, noncommercial use under the Pixelheart Source-Available License 1.1. No game artwork is bundled; the in-game look is read from your own copy of the game.")

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
        self.world.reset_workspace()

    def _finish_icon_close(self):
        if self._closing_after_download:
            self.close()
