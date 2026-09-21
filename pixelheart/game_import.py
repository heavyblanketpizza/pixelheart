"""Shared local Content Patcher setup; installation paths stay in OS settings."""

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from pixelheart_core.local_templates import export_commands, resolve_export_folder
from .widgets import button, label


def game_import_settings():
    return QSettings("Pixelheart", "Pixelheart")


GAME_SOURCE_SETTINGS_KEY = "localGame/contentPatcherExportFolder"


def game_source_directory():
    """Return the machine-local preference, never a project asset reference."""
    saved = game_import_settings().value(GAME_SOURCE_SETTINGS_KEY, "")
    return saved if isinstance(saved, str) else ""


def remember_game_source_directory(directory):
    game_import_settings().setValue(GAME_SOURCE_SETTINGS_KEY, str(directory).strip())


def map_game_content_root():
    """Resolve the selected unpacked content or Content Patcher export folder."""
    directory = game_source_directory()
    return resolve_export_folder(directory) if directory else None


class LocalMapSourceWidget(QWidget):
    """Local game art supplies previews without copying it into project packs."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(label("Preview with your game’s tilesheets", "sectionTitle", True))
        root.addWidget(label(
            "Choose unpacked Content or a Content Patcher export folder. Native game tiles are read here for previews; "
            "they are not copied into the project or exported pack. The folder is remembered on this computer only.",
            "hint", True,
        ))
        row = QHBoxLayout()
        self.folder = QLineEdit(game_source_directory())
        self.folder.setAccessibleName("Local game tilesheet folder")
        self.folder.setPlaceholderText("Choose unpacked Content or patch export…")
        self.folder.editingFinished.connect(self.refresh)
        row.addWidget(self.folder, 1)
        self.browse_button = button("Browse…", self.browse)
        row.addWidget(self.browse_button)
        self.refresh_button = button("Refresh preview", self.refresh)
        row.addWidget(self.refresh_button)
        root.addLayout(row)
        self.command_hint = label("With Content Patcher loaded, export this map’s game tilesheets in the SMAPI console:", "hint", True)
        root.addWidget(self.command_hint)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setAccessibleName("Game tilesheet export commands")
        self.commands.setMaximumHeight(80)
        root.addWidget(self.commands)
        self.copy_button = button("Copy export commands", self.copy_commands)
        root.addWidget(self.copy_button)
        self.set_assets([])

    def set_assets(self, assets):
        self.commands.setPlainText("\n".join(f'patch export "{asset}" image' for asset in sorted(set(assets))))
        for widget in (self.command_hint, self.commands, self.copy_button):
            widget.setVisible(bool(assets))

    def refresh(self):
        remember_game_source_directory(self.folder.text())
        self.changed.emit()

    def browse(self):
        selected = QFileDialog.getExistingDirectory(self, "Choose unpacked Content, patch export, or your game folder", self.folder.text().strip())
        if selected:
            self.folder.setText(selected)
            self.refresh()

    def copy_commands(self):
        QApplication.clipboard().setText(self.commands.toPlainText())


class LocalGameSourceWidget(QWidget):
    changed = Signal()
    SETTINGS_KEY = GAME_SOURCE_SETTINGS_KEY

    def __init__(self, template_id, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.settings = game_import_settings()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(label("1. With Content Patcher loaded, run these commands in the SMAPI console.", "muted", True))
        command_row = QHBoxLayout()
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setAccessibleName("Content Patcher export commands")
        self.commands.setMaximumHeight(70 if kind == "artwork" else 48)
        command_row.addWidget(self.commands, 1)
        self.copy_button = button("Copy commands", self.copy_commands)
        command_row.addWidget(self.copy_button)
        root.addLayout(command_row)
        root.addWidget(label("2. Choose the resulting patch export folder, or its game folder.", "muted", True))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setAccessibleName("Local Content Patcher export folder")
        self.folder.setPlaceholderText("Choose a local export folder…")
        saved = self.settings.value(self.SETTINGS_KEY, "")
        self.folder.setText(saved if isinstance(saved, str) else "")
        self.folder.textChanged.connect(lambda *_: self.changed.emit())
        self.folder.editingFinished.connect(self.remember_directory)
        folder_row.addWidget(self.folder, 1)
        self.browse_button = button("Browse…", self.browse)
        folder_row.addWidget(self.browse_button)
        root.addLayout(folder_row)
        root.addWidget(label("Exports include active mods. For vanilla references, run only SMAPI and Content Patcher. This folder is remembered on this computer only.", "hint", True))
        self.set_template(template_id)

    def set_template(self, template_id):
        self.commands.setPlainText(export_commands(template_id, self.kind))

    def directory(self):
        return self.folder.text().strip()

    def remember_directory(self):
        self.settings.setValue(self.SETTINGS_KEY, self.directory())

    def browse(self):
        selected = QFileDialog.getExistingDirectory(self, "Choose Content Patcher exports or your game folder", self.directory())
        if selected:
            self.folder.setText(selected)
            self.remember_directory()

    def copy_commands(self):
        QApplication.clipboard().setText(self.commands.toPlainText())
