"""The first thing a player sees: their game, their characters, and a way to start."""
from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QMenu, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from pixelheart_core.library import (
    TRASH_NAME, character_folder, default_library_root, edited_ago, library_projects, project_card,
    workspace_projects_root,
)
from .game_connection import game_connection, open_find_game
from .pixel_widgets import HeartMeter, PixelTitle, PortraitFrame
from .widgets import button, card, label

MAX_CARDS = 12


class CharacterCard(QFrame):
    """One character: portrait, name, hearts earned, and when they were last edited."""

    def __init__(self, summary, window, place=""):
        super().__init__()
        self.setObjectName("panel")
        self.path, self.error = summary["path"], summary["error"]
        name = summary["name"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(12)
        self.portrait = PortraitFrame(64)
        self.portrait.set_portrait(summary.get("portrait"))
        layout.addWidget(self.portrait)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.name = label(name, "sectionTitle")
        text.addWidget(self.name)
        self.meter = HeartMeter(summary["total"], summary["hearts"])
        text.addWidget(self.meter)
        detail = self.error or (edited_ago(summary["modified"]) if summary.get("modified") else "")
        self.detail = label(" · ".join(part for part in (place, detail) if part), "muted", True)
        self.detail.setToolTip(str(self.path))
        text.addWidget(self.detail)
        text.addStretch()
        layout.addLayout(text, 1)
        actions = QVBoxLayout()
        self.open_button = button("Open", lambda: window.open_path(str(self.path)), "primary")
        self.open_button.setAccessibleName(f"Open {name}")
        self.open_button.setVisible(not self.error)
        self.remove_button = button("Remove from list", lambda: window.welcome.remove(self.path), "quiet")
        self.remove_button.setVisible(bool(self.error))
        self.menu = QMenu(self)
        self.remove_action = self.menu.addAction("Remove from list", lambda: window.welcome.remove(self.path))
        self.trash_action = None
        if character_folder(self.path) is not None:
            self.trash_action = self.menu.addAction(f"Move to {TRASH_NAME}…", lambda: window.welcome.trash(self.path, name))
        self.more_button = button("⋯", self.show_menu, "quiet")
        self.more_button.setAccessibleName(f"More for {name}")
        self.more_button.setToolTip(f"Remove from list or move to the {TRASH_NAME}")
        actions.addWidget(self.open_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignRight)
        actions.addStretch()
        layout.addLayout(actions)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: self.menu.popup(self.mapToGlobal(point)))

    def show_menu(self):
        self.menu.popup(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))


class WelcomePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("welcome")
        self.window = window
        self.cards = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(48, 36, 48, 30)
        root.setSpacing(16)
        self.title = PixelTitle("Pixelheart", 3)
        root.addWidget(self.title)
        root.addWidget(label("Make a new friend for Stardew Valley.", "muted", True))

        status, content = card()
        self.game_status = label("", "sectionTitle", True)
        self.tools_status = label("", "muted", True)
        content.addWidget(self.game_status)
        content.addWidget(self.tools_status)
        self.find_button = button("Find Stardew Valley…", self.find_game)
        content.addWidget(self.find_button, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(status)

        heading = QHBoxLayout()
        heading.addWidget(label("Your characters", "title"))
        heading.addStretch()
        self.open_button = button("Open another…", window.open_dialog, "quiet")
        self.new_button = button("New character", window.start_new_character, "primary")
        heading.addWidget(self.open_button)
        heading.addWidget(self.new_button)
        root.addLayout(heading)
        self.empty = label("No characters yet. Choose New character to meet your first one.", "muted", True)
        root.addWidget(self.empty)
        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        root.addLayout(self.grid)
        root.addStretch()
        root.addWidget(label("Your characters stay on your computer · An unofficial fan tool for Stardew Valley", "hint", True))
        game_connection().changed.connect(self.refresh_game)
        self.refresh()

    def find_game(self):
        open_find_game(self.window)
        self.refresh_game()

    def refresh_game(self):
        connection = game_connection()
        self.game_status.setText(connection.message())
        self.tools_status.setText(connection.tools_message())
        self.find_button.setText("Change…" if connection.state() == "connected" else "Find Stardew Valley…")

    def remove(self, path):
        self.window.hide_project(path)
        self.refresh()

    def confirm_trash(self, name):
        box = QMessageBox(QMessageBox.Icon.Warning, f"Move to the {TRASH_NAME}?",
                          f"Move {name} to the {TRASH_NAME}?", QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText(f"Their folder, including conversations, artwork and maps, goes to the {TRASH_NAME}. "
                               "You can put it back from there.")
        trash = box.addButton(f"Move to {TRASH_NAME}", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is trash

    def trash(self, path, name):
        if self.confirm_trash(name) and self.window.trash_project(path):
            self.refresh()

    def paths(self):
        seen, paths, hidden = set(), [], self.window.hidden_projects()
        for path in [*self.window.recent_projects(), *map(str, library_projects(default_library_root())),
                     *map(str, library_projects(workspace_projects_root()))]:
            normalized = str(Path(path))
            if normalized not in seen and normalized not in hidden:
                seen.add(normalized)
                paths.append(Path(normalized))
        return paths[:MAX_CARDS]

    def refresh(self):
        self.refresh_game()
        for old in self.cards:
            self.grid.removeWidget(old)
            old.deleteLater()
        summaries = [project_card(path) for path in self.paths()]
        names = Counter(summary["name"].casefold() for summary in summaries)
        self.cards = [CharacterCard(summary, self.window,
                                    summary["path"].parent.name if names[summary["name"].casefold()] > 1 else "")
                      for summary in summaries]
        for index, character_card in enumerate(self.cards):
            self.grid.addWidget(character_card, index // 2, index % 2)
        self.empty.setVisible(not self.cards)
