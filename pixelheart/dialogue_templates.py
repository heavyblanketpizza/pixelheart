"""Browse and import complete dialogue exported from the user's local game."""

from copy import deepcopy
from html import escape
import re

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from pixelheart_core.dialogue_templates import (
    overwrite_dialogue_examples, dialogue_conflicts, dialogue_has_advanced_commands, dialogue_preview,
)
from pixelheart_core.local_templates import LOCAL_TEMPLATES, CONTENT_PATCHER_EXPORT_URL, load_project_dialogue
from .game_import import ProjectDialogueSourceWidget
from .widgets import button, card, label


class ReadingLabel(QLabel):
    """Keep every wrapped line visible inside the scrollable example panel."""

    def __init__(self, text="", style=None):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        if style:
            self.setObjectName(style)

    def fit_height(self):
        height = self.heightForWidth(self.width())
        if height >= 0 and self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def setText(self, text):
        super().setText(text)
        self.fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()


class DialogueLoad(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, template_id, project_file, parent=None):
        super().__init__(parent)
        self.template_id, self.project_file = template_id, project_file

    def run(self):
        try:
            result = load_project_dialogue(
                self.template_id, self.project_file, cancelled=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class DialogueTemplateDialog(QDialog):
    def __init__(self, records, parent=None, *, project_file=None):
        super().__init__(parent)
        self.records = deepcopy(records)
        self.project_file = project_file
        self.examples = []
        self.conflicts = {}
        self.imported_records = None
        self.worker = None
        self.closing = False
        self.setWindowTitle("Load character dialogue")
        self.resize(980, 780)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(label("A familiar voice, a place to start.", "title", True))
        root.addWidget(label("Load Abigail’s or Elliott’s complete dialogue export from your game, then explore and adapt the conversations.", "muted", True))
        choose = QHBoxLayout()
        choose.addWidget(label("Template character"))
        self.character = QComboBox()
        self.character.setAccessibleName("Dialogue reference character")
        for template_id, template in LOCAL_TEMPLATES.items():
            self.character.addItem(template["name"], template_id)
        choose.addWidget(self.character, 1)
        self.load_button = button("Load full dialogue", self.load_examples, "primary")
        choose.addWidget(self.load_button)
        root.addLayout(choose)
        self.game_source = ProjectDialogueSourceWidget(self.character.currentData(), project_file, self)
        root.addWidget(self.game_source)
        self.status = label("", "muted", True)
        self.status.setAccessibleName("Dialogue template loading status")
        root.addWidget(self.status)

        splitter = QSplitter()
        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search triggers or dialogue…")
        self.search.setAccessibleName("Search the full dialogue template")
        list_layout.addWidget(self.search)
        self.match_count = label("", "hint")
        list_layout.addWidget(self.match_count)
        self.list = QListWidget()
        self.list.setAccessibleName("Full dialogue template entries")
        self.list.setMinimumWidth(235)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_layout.addWidget(self.list, 1)
        splitter.addWidget(list_panel)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.detail = QWidget()
        content = QVBoxLayout(self.detail)
        content.setContentsMargins(12, 0, 4, 0)
        content.setSpacing(12)
        content.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.example_title = ReadingLabel("Choose a character to begin", "sectionTitle")
        content.addWidget(self.example_title)
        self.when = ReadingLabel("", "notice")
        content.addWidget(self.when)
        self.lesson = ReadingLabel("", "muted")
        content.addWidget(self.lesson)
        content.addWidget(label("In the editor", "eyebrow"))
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setAccessibleName("Original dialogue with game commands")
        self.raw_text.setMinimumHeight(100)
        self.raw_text.setMaximumHeight(100)
        content.addWidget(self.raw_text)
        preview_card, preview_layout = card("A first listen")
        self.preview = ReadingLabel("", "profileName")
        self.preview.setStyleSheet("font-size: 19px;")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.preview)
        self.preview_note = label("Text preview · Expressions and dialogue timing need in-game review.", "hint", True)
        preview_layout.addWidget(self.preview_note)
        content.addWidget(preview_card)
        self.commands = ReadingLabel("", "hint")
        content.addWidget(self.commands)

        for wrapped in (self.example_title, self.when, self.lesson, self.preview, self.commands):
            wrapped.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        content.addStretch()
        scroll.setWidget(self.detail)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([280, 610])
        self.pages = QStackedWidget()
        empty_card, empty_layout = card("Start with the whole conversation collection")
        empty_layout.addWidget(label("Choose Abigail or Elliott above and export their dialogue using the instructions. Every entry in that file will be loaded, with its original text and commands.", "muted", True))
        for title, description in (
            ("The complete collection", "Import the full template together, including seasonal lines, friendship variations, and event responses."),
            ("Find a conversation", "Search by trigger or dialogue text and explore the original commands."),
            ("Make it your character’s voice", "Load the collection into Everyday dialogue, then rewrite the lines in your character’s voice."),
        ):
            empty_layout.addWidget(label(title, "sectionTitle"))
            empty_layout.addWidget(label(description, "muted", True))
        empty_layout.addStretch()
        self.pages.addWidget(empty_card)
        self.pages.addWidget(splitter)
        root.addWidget(self.pages, 1)

        self.overwrite_warning = label(
            "⚠ Any matching dialogue will be overwritten when you use this template. Other dialogue will be kept.",
            "notice", True,
        )
        self.overwrite_warning.setAccessibleName("Existing dialogue overwrite warning")
        self.overwrite_warning.setVisible(bool(self.records))
        root.addWidget(self.overwrite_warning)

        self.source = label("", "hint", True)
        self.source.setTextFormat(Qt.TextFormat.RichText)
        self.source.setOpenExternalLinks(True)
        root.addWidget(self.source)
        root.addWidget(label("Review names, conditions, and portrait commands before exporting. Local imports retain their source; importing does not grant permission to redistribute game or mod content.", "hint", True))
        self.summary = label("", "muted", True)
        self.summary.setAccessibleName("Dialogue import summary")
        root.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.use_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.use_button.setText("Use full dialogue")
        self.use_button.setObjectName("primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.character.currentIndexChanged.connect(self.clear_examples)
        self.list.currentRowChanged.connect(self.show_example)
        self.search.textChanged.connect(self.filter_examples)
        self.clear_examples()

    def clear_examples(self):
        self.examples = []
        self.conflicts = {}
        self.imported_records = None
        self.list.clear()
        self.detail.hide()
        self.pages.setCurrentIndex(0)
        self.search.clear()
        self.use_button.setEnabled(False)
        self.use_button.setText("Use full dialogue")
        self.summary.setText("The full template will be added to this project’s Everyday dialogue.")
        self.overwrite_warning.setText("⚠ Any matching dialogue will be overwritten when you use this template. Other dialogue will be kept.")
        self.overwrite_warning.setVisible(bool(self.records))
        self.status.setText("Load the complete template from this project’s dialogue folder.")
        self.game_source.set_template(self.character.currentData())
        self.load_button.setEnabled(self.project_file is not None and self.worker is None)
        self.source.setText(
            'From my game · <a href="' + escape(CONTENT_PATCHER_EXPORT_URL, quote=True)
            + '">Export instructions</a> · '
            '<a href="https://stardewvalleywiki.com/Modding:Dialogue">Dialogue guide</a>'
        )

    def load_examples(self):
        if self.worker is not None or self.project_file is None:
            return
        self.clear_examples()
        self.status.setText("Loading conversations…")
        self.character.setEnabled(False)
        self.load_button.setEnabled(False)
        self.game_source.setEnabled(False)
        self.worker = DialogueLoad(self.character.currentData(), self.project_file, self)
        self.worker.ready.connect(self.receive_examples)
        self.worker.failed.connect(self.show_failure)
        self.worker.finished.connect(self.load_finished)
        self.worker.start()

    def receive_examples(self, result):
        if self.closing:
            return
        self.examples = deepcopy(result["examples"])
        self.conflicts = dialogue_conflicts(self.records, self.examples)
        if result.get("attribution"):
            self.source.setText(escape(result["attribution"]) + ' · <a href="'
                                + escape(CONTENT_PATCHER_EXPORT_URL, quote=True) + '">Export instructions</a>')
        self.list.clear()
        for example in self.examples:
            suffix = " · Will overwrite" if example["trigger"] in self.conflicts else ""
            item = QListWidgetItem(example["title"] + "\n" + example["trigger"] + suffix)
            item.setToolTip(example["when"])
            self.list.addItem(item)
        self.pages.setCurrentIndex(1)
        self.detail.show()
        self.list.setCurrentRow(0)
        self.status.setText(f"Loaded all {len(self.examples)} entries. Ready to use the complete collection.")
        self.filter_examples()
        self.update_summary()

    def filter_examples(self, *_):
        query = self.search.text().strip().casefold()
        visible = 0
        for index, example in enumerate(self.examples):
            matches = not query or query in " ".join((example["trigger"], example["title"], example["text"])).casefold()
            self.list.item(index).setHidden(not matches)
            visible += matches
        self.match_count.setText(f"Showing {visible} of {len(self.examples)} entries")
        current = self.list.currentItem()
        if current is not None and current.isHidden():
            first = next((i for i in range(self.list.count()) if not self.list.item(i).isHidden()), -1)
            self.list.setCurrentRow(first)
        self.detail.setVisible(visible > 0)
        if visible and self.list.currentRow() < 0:
            self.list.setCurrentRow(next(i for i in range(self.list.count()) if not self.list.item(i).isHidden()))

    def show_example(self, index):
        if not 0 <= index < len(self.examples):
            return
        example = self.examples[index]
        trigger, text = example["trigger"], example["text"]
        self.example_title.setText(example["title"] + " · " + trigger)
        self.when.setText(example["when"])
        self.lesson.setText(example["lesson"])
        self.raw_text.setPlainText(text)
        self.preview.setText(dialogue_preview(text))
        advanced = dialogue_has_advanced_commands(text)
        self.preview_note.setText(
            "Script preview · Choices, conditions, and other advanced commands are shown as written."
            if advanced else "Text preview · Expressions and dialogue timing need in-game review."
        )
        commands = [("@", "the farmer’s name"), ("$h", "happy portrait"), ("$s", "sad portrait"),
                    ("$l", "love portrait"), ("$a", "angry portrait"), ("$u", "unique portrait"),
                    ("#$b#", "click to continue in a new dialogue box"),
                    ("#$e#", "talk again for the next part")]
        explanations = [token + " = " + meaning for token, meaning in commands if token in text]
        for frame in sorted(set(re.findall(r"\$(\d+)(?=$|#)", text))):
            explanations.append(f"${frame} = portrait frame {frame}, counting from zero; use a frame in your character’s sheet")
        if advanced:
            explanations.append("Keep linked response keys together when adapting choices. Some conditions and event references belong to the original character.")
        self.commands.setText("\n".join(explanations) or "This line uses plain text, with the neutral portrait.")

    def update_summary(self, *_):
        self.use_button.setEnabled(False)
        if not self.examples:
            self.summary.setText("Load a template to use its complete dialogue collection.")
            return
        try:
            overwrite_dialogue_examples(self.records, self.examples)
        except ValueError as exc:
            self.summary.setText(str(exc))
            return
        overwritten = sum(len(rows) for rows in self.conflicts.values())
        added = len(self.examples) - len(self.conflicts)
        kept = len(self.records) - overwritten
        self.overwrite_warning.setText(
            f"⚠ This will overwrite {overwritten} matching dialogue "
            f"{'entry' if overwritten == 1 else 'entries'}. Other dialogue will be kept."
        )
        self.overwrite_warning.setVisible(overwritten > 0)
        self.summary.setText(f"{added} to add · {overwritten} to overwrite · {kept} other entries kept")
        self.use_button.setEnabled(self.worker is None and not self.closing)

    def accept(self):
        self.update_summary()
        if not self.use_button.isEnabled():
            return
        self.imported_records = overwrite_dialogue_examples(self.records, self.examples)
        super().accept()

    def show_failure(self, message):
        if not self.closing:
            self.status.setText("Couldn’t load this dialogue. " + message + " Try again, or close this browser to keep writing.")

    def load_finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self.closing:
            super().reject()
            return
        self.character.setEnabled(True)
        self.load_button.setEnabled(True)
        self.game_source.setEnabled(True)
        self.update_summary()

    def reject(self):
        if self.worker is not None:
            self.closing = True
            self.worker.requestInterruption()
            self.buttons.setEnabled(False)
            self.status.setText("Stopping local import…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
