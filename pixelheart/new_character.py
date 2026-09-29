"""A tiny, friendly first step: what should the valley call them?"""
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from .widgets import button, label


class NewCharacterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New character")
        self.resize(480, 250)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(12)
        root.addWidget(label("A new face in the valley", "title", True))
        root.addWidget(label("Just a name for now. You can change everything later.", "muted", True))
        self.name_field = QLineEdit()
        self.name_field.setMaxLength(64)
        self.name_field.setPlaceholderText("What should the valley call them?")
        self.name_field.setAccessibleName("Character name")
        root.addWidget(self.name_field)
        self.romance = QCheckBox("Open to romance")
        self.romance.setChecked(True)
        root.addWidget(self.romance)
        root.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Cancel", self.reject))
        self.create_button = button("Create", self.accept, "primary")
        self.create_button.setDefault(True)
        actions.addWidget(self.create_button)
        root.addLayout(actions)
        self.name_field.textChanged.connect(self._validate)
        self._validate()

    def _validate(self):
        self.create_button.setEnabled(bool(self.name_field.text().strip()))

    def values(self):
        return self.name_field.text().strip(), self.romance.isChecked()

    def accept(self):
        if self.create_button.isEnabled():
            super().accept()
