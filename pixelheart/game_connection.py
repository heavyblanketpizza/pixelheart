"""The one place Pixelheart remembers and checks the player's Stardew Valley."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout,
)

from pixelheart_core.game_install import GameInstallError, find_games, inspect_game
from . import game_import
from .widgets import button, card, label

SETTINGS_KEY = "game/folder"
LEGACY_KEYS = ("localGame/installationFolder",)
SMAPI_URL = "https://smapi.io/"
CONTENT_PATCHER_URL = "https://www.nexusmods.com/stardewvalley/mods/1915"


def _where(root):
    parts = Path(root).parts
    return f" on {parts[2]}" if len(parts) > 2 and parts[1] == "Volumes" else ""


class GameConnection(QObject):
    """Remembered game folder, its current state, and a friendly description."""
    changed = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else game_import.game_import_settings()
        self._state, self._install, self._message = "not_set", None, ""
        self._migrate()
        self._inspect()

    def _saved(self):
        value = self.settings.value(SETTINGS_KEY, "")
        return value.strip() if isinstance(value, str) else ""

    def _migrate(self):
        if self._saved():
            return
        for key in LEGACY_KEYS:
            value = self.settings.value(key, "")
            if isinstance(value, str) and value.strip():
                self.settings.setValue(SETTINGS_KEY, value.strip())
                return

    def _inspect(self):
        before = (self._state, self._install)
        folder = self._saved()
        if not folder:
            self._state, self._install = "not_set", None
            self._message = "Pixelheart hasn't found Stardew Valley yet."
        elif not Path(folder).expanduser().exists():
            self._state, self._install = "unplugged", None
            self._message = "Your game drive isn't connected right now. Plug it in, then choose Search again."
        else:
            try:
                self._install = inspect_game(folder)
                self._state = "connected"
                version = f" {self._install.version}" if self._install.version else ""
                self._message = f"Stardew Valley{version} found{_where(self._install.root)}."
            except GameInstallError as exc:
                self._state, self._install, self._message = "invalid", None, str(exc)
        return before != (self._state, self._install)

    def folder(self):
        return self._saved()

    def state(self):
        return self._state

    def install(self):
        return self._install

    def content_root(self):
        return self._install.content if self._install else None

    def message(self):
        return self._message

    def tools_message(self):
        install = self._install
        if install is None:
            return "Once your game is connected, Pixelheart checks for SMAPI and Content Patcher too."
        smapi = ("SMAPI is installed." if install.smapi
                 else "SMAPI isn't installed yet. You'll need it to play your character.")
        patcher = (f"Content Patcher {install.content_patcher} is installed." if install.content_patcher
                   else "Content Patcher isn't installed yet. You'll need it too.")
        return smapi + "\n" + patcher

    def refresh(self):
        if self._inspect():
            self.changed.emit()
            return True
        return False

    def set_folder(self, folder):
        install = inspect_game(folder)
        self.settings.setValue(SETTINGS_KEY, str(install.root))
        self.settings.sync()
        self.refresh()
        return install

    def auto_detect(self):
        """Look in the usual places once, only when no game has ever been chosen."""
        if self._saved():
            return False
        found = find_games(limit=1)
        if not found:
            return False
        self.set_folder(found[0].root)
        return True


_CONNECTION = None


def game_connection():
    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = GameConnection()
    return _CONNECTION


def reset_game_connection():
    global _CONNECTION
    _CONNECTION = None


class FindGameDialog(QDialog):
    """Show what Pixelheart knows about the game, and let the player fix it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.connection = game_connection()
        self.setWindowTitle("Find Stardew Valley")
        self.resize(600, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)
        root.addWidget(label("Find Stardew Valley", "title"))
        panel, content = card()
        self.status = label("", "sectionTitle", True)
        self.folder_label = label("", "hint", True)
        self.tools = label("", "muted", True)
        content.addWidget(self.status)
        content.addWidget(self.folder_label)
        content.addWidget(self.tools)
        links = QHBoxLayout()
        links.addWidget(button("Get SMAPI", lambda: QDesktopServices.openUrl(QUrl(SMAPI_URL)), "quiet"))
        links.addWidget(button("Get Content Patcher", lambda: QDesktopServices.openUrl(QUrl(CONTENT_PATCHER_URL)), "quiet"))
        links.addStretch()
        content.addLayout(links)
        root.addWidget(panel)
        self.results = QListWidget()
        self.results.setAccessibleName("Games found on this computer")
        self.results.hide()
        root.addWidget(self.results, 1)
        actions = QHBoxLayout()
        self.search_button = button("Search again", self.search)
        self.choose_button = button("Choose folder…", self.choose_folder)
        self.use_button = button("Use this game", self.use_selected, "primary")
        self.use_button.hide()
        actions.addWidget(self.search_button)
        actions.addWidget(self.choose_button)
        actions.addStretch()
        actions.addWidget(self.use_button)
        actions.addWidget(button("Done", self.accept))
        root.addLayout(actions)
        root.addStretch()
        self.render()

    def render(self, message=None):
        self.status.setText(message or self.connection.message())
        install = self.connection.install()
        self.folder_label.setText(str(install.root) if install else "")
        self.folder_label.setVisible(install is not None)
        self.tools.setText(self.connection.tools_message())

    def search(self):
        self.connection.refresh()
        found = find_games()
        self.results.clear()
        for install in found:
            version = f" {install.version}" if install.version else ""
            item = QListWidgetItem(f"Stardew Valley{version}{_where(install.root)}\n{install.root}")
            item.setData(Qt.ItemDataRole.UserRole, str(install.root))
            self.results.addItem(item)
        self.results.setVisible(bool(found))
        self.use_button.setVisible(bool(found))
        if found:
            self.results.setCurrentRow(0)
        self.render(None if found else "No Stardew Valley found in the usual places. Choose its folder instead.")

    def use_selected(self):
        item = self.results.currentItem()
        if item is not None:
            self._connect(item.data(Qt.ItemDataRole.UserRole))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose your Stardew Valley folder", self.connection.folder())
        if folder:
            self._connect(folder)

    def _connect(self, folder):
        try:
            self.connection.set_folder(folder)
            self.render()
        except GameInstallError as exc:
            self.render(str(exc))


def open_find_game(parent=None):
    """Show the Find Stardew Valley window; True when the game connection changed."""
    connection = game_connection()
    before = (connection.state(), connection.install())
    dialog = FindGameDialog(parent)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
    return before != (connection.state(), connection.install())
