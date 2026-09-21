"""Shared local Content Patcher setup; installation paths stay in OS settings."""

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from pixelheart_core.local_templates import export_commands
from .widgets import button, label


def game_import_settings():
    return QSettings("Pixelheart", "Pixelheart")


class LocalGameSourceWidget(QWidget):
    changed = Signal()
    SETTINGS_KEY = "localGame/contentPatcherExportFolder"

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
