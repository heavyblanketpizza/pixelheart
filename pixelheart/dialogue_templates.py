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
    apply_dialogue_examples, dialogue_conflicts, dialogue_has_advanced_commands, dialogue_preview,
)
from pixelheart_core.local_templates import LOCAL_TEMPLATES, CONTENT_PATCHER_EXPORT_URL, load_local_dialogue
from .game_import import LocalGameSourceWidget
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

    def __init__(self, template_id, export_root, parent=None):
        super().__init__(parent)
        self.template_id, self.export_root = template_id, export_root

    def run(self):
        try:
            result = load_local_dialogue(
                self.template_id, self.export_root, cancelled=self.isInterruptionRequested,
            )
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class DialogueTemplateDialog(QDialog):
    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records = deepcopy(records)
        self.examples = []
        self.decisions = {}
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
        self.game_source = LocalGameSourceWidget(self.character.currentData(), "dialogue", self)
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
        select_row = QHBoxLayout()
        self.select_all_button = button("Select all", lambda: self.select_all(True))
        self.select_all_button.setToolTip("Select every entry, including entries hidden by search.")
        self.select_none_button = button("Clear selection", lambda: self.select_all(False))
        select_row.addWidget(self.select_all_button)
        select_row.addWidget(self.select_none_button)
        list_layout.addLayout(select_row)
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

        self.conflict_card, conflict_layout = card("This trigger is already in your character")
        conflict_layout.addWidget(label("Your current dialogue", "muted"))
        self.existing_text = QPlainTextEdit()
        self.existing_text.setReadOnly(True)
        self.existing_text.setAccessibleName("Existing dialogue for this trigger")
        self.existing_text.setMaximumHeight(100)
        conflict_layout.addWidget(self.existing_text)
        self.conflict_choice = QComboBox()
        self.conflict_choice.setAccessibleName("Choose which dialogue to keep")
        self.conflict_help = ReadingLabel("", "hint")
        conflict_layout.addWidget(self.conflict_help)
        content.addWidget(self.conflict_card)
        for wrapped in (self.example_title, self.when, self.lesson, self.preview, self.commands, self.conflict_help):
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
            ("All entries selected", "Import the full template together, including seasonal lines, friendship variations, and event responses."),
            ("Find a conversation", "Search by trigger or dialogue text and explore the original commands."),
            ("Make it your character’s voice", "Keep or replace matching dialogue, then rewrite the imported lines in the editor."),
        ):
            empty_layout.addWidget(label(title, "sectionTitle"))
            empty_layout.addWidget(label(description, "muted", True))
        empty_layout.addStretch()
        self.pages.addWidget(empty_card)
        self.pages.addWidget(splitter)
        root.addWidget(self.pages, 1)

        self.bulk_conflict_bar = QWidget()
        bulk_row = QHBoxLayout(self.bulk_conflict_bar)
        bulk_row.setContentsMargins(0, 0, 0, 0)
        bulk_row.addWidget(label("Matching triggers", "muted"))
        self.bulk_conflict_choice = QComboBox()
        self.bulk_conflict_choice.setAccessibleName("Resolve all matching dialogue triggers")
        self.bulk_conflict_choice.addItem("Choose for each matching line…", "")
        self.bulk_conflict_choice.addItem("Keep all my matching dialogue", "keep")
        self.bulk_conflict_choice.addItem("Replace all matching dialogue with template", "replace")
        bulk_row.addWidget(self.bulk_conflict_choice, 1)
        root.addWidget(self.bulk_conflict_bar)

        self.conflict_bar = QWidget()
        conflict_row = QHBoxLayout(self.conflict_bar)
        conflict_row.setContentsMargins(0, 0, 0, 0)
        self.conflict_label = label("", "muted", True)
        conflict_row.addWidget(self.conflict_label, 1)
        conflict_row.addWidget(self.conflict_choice, 1)
        root.addWidget(self.conflict_bar)

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
        self.game_source.changed.connect(self.clear_examples)
        self.list.currentRowChanged.connect(self.show_example)
        self.list.itemChanged.connect(self.update_summary)
        self.conflict_choice.currentIndexChanged.connect(self.choose_conflict)
        self.bulk_conflict_choice.currentIndexChanged.connect(self.choose_all_conflicts)
        self.search.textChanged.connect(self.filter_examples)
        self.clear_examples()

    def clear_examples(self):
        self.examples = []
        self.decisions = {}
        self.conflicts = {}
        self.imported_records = None
        self.list.clear()
        self.detail.hide()
        self.pages.setCurrentIndex(0)
        self.conflict_bar.hide()
        self.bulk_conflict_bar.hide()
        self.bulk_conflict_choice.blockSignals(True)
        self.bulk_conflict_choice.setCurrentIndex(0)
        self.bulk_conflict_choice.blockSignals(False)
        self.search.clear()
        self.use_button.setEnabled(False)
        self.use_button.setText("Use full dialogue")
        self.summary.setText("The full dialogue file will be selected for import.")
        self.status.setText("Load the complete local dialogue export. No template download is needed.")
        self.game_source.set_template(self.character.currentData())
        self.source.setText(
            'From my game · <a href="' + escape(CONTENT_PATCHER_EXPORT_URL, quote=True)
            + '">Export instructions</a> · '
            '<a href="https://stardewvalleywiki.com/Modding:Dialogue">Dialogue guide</a>'
        )

    def load_examples(self):
        if self.worker is not None:
            return
        self.clear_examples()
        self.status.setText("Loading conversations…")
        self.character.setEnabled(False)
        self.load_button.setEnabled(False)
        self.game_source.setEnabled(False)
        self.game_source.remember_directory()
        self.worker = DialogueLoad(self.character.currentData(), self.game_source.directory(), self)
        self.worker.ready.connect(self.receive_examples)
        self.worker.failed.connect(self.show_failure)
        self.worker.finished.connect(self.load_finished)
        self.worker.start()

    def receive_examples(self, result):
        if self.closing:
            return
        self.examples = deepcopy(result["examples"])
        if result.get("attribution"):
            self.source.setText(escape(result["attribution"]) + ' · <a href="'
                                + escape(CONTENT_PATCHER_EXPORT_URL, quote=True) + '">Export instructions</a>')
        self.conflicts = dialogue_conflicts(self.records, self.examples)
        self.list.blockSignals(True)
        for example in self.examples:
            trigger = example["trigger"]
            suffix = " · Exists" if trigger in self.conflicts else ""
            item = QListWidgetItem(example["title"] + "\n" + trigger + suffix)
            item.setToolTip(example["when"] + (" This trigger is already in your character." if trigger in self.conflicts else ""))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
            if len(self.conflicts.get(trigger, [])) > 1:
                self.decisions[trigger] = "keep"
        self.list.blockSignals(False)
        self.pages.setCurrentIndex(1)
        self.bulk_conflict_bar.setVisible(bool(self.conflicts))
        self.detail.show()
        self.list.setCurrentRow(0)
        self.status.setText(f"Loaded all {len(self.examples)} entries. The full template is selected for import.")
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
        if not visible:
            self.conflict_bar.hide()
        elif self.list.currentRow() < 0:
            self.list.setCurrentRow(next(i for i in range(self.list.count()) if not self.list.item(i).isHidden()))

    def select_all(self, selected):
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self.update_summary()

    def choose_all_conflicts(self):
        decision = self.bulk_conflict_choice.currentData()
        if not decision:
            return
        selected = {example["trigger"] for example in self.selected_examples()}
        for trigger, existing in self.conflicts.items():
            if trigger in selected:
                self.decisions[trigger] = decision if len(existing) == 1 else "keep"
        self.show_example(self.list.currentRow())
        self.update_summary()

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
        existing = self.conflicts.get(trigger, [])
        self.conflict_card.setVisible(bool(existing))
        self.conflict_bar.setVisible(bool(existing))
        self.conflict_label.setText(trigger + " already exists. Choose which dialogue to keep:")
        self.conflict_choice.blockSignals(True)
        self.conflict_choice.clear()
        if existing:
            self.existing_text.setPlainText("\n\n".join(row["text"] for row in existing))
            if len(existing) == 1:
                self.conflict_choice.addItem("Choose a version…", "")
            self.conflict_choice.addItem("Keep my dialogue", "keep")
            if len(existing) == 1:
                self.conflict_choice.addItem("Replace with this template line", "replace")
            self.conflict_choice.setCurrentIndex(max(0, self.conflict_choice.findData(self.decisions.get(trigger, ""))))
            self.conflict_help.setText(
                "This trigger appears more than once. Fix the duplicate keys in Dialogue before replacing it."
                if len(existing) > 1 else "Choose which text to keep. This choice applies when this entry is selected."
            )
        self.conflict_choice.blockSignals(False)

    def choose_conflict(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.examples):
            trigger = self.examples[index]["trigger"]
            decision = self.conflict_choice.currentData()
            if decision:
                self.decisions[trigger] = decision
            else:
                self.decisions.pop(trigger, None)
            self.bulk_conflict_choice.blockSignals(True)
            self.bulk_conflict_choice.setCurrentIndex(0)
            self.bulk_conflict_choice.blockSignals(False)
            self.update_summary()

    def selected_examples(self):
        return [example for index, example in enumerate(self.examples)
                if self.list.item(index).checkState() == Qt.CheckState.Checked]

    def update_summary(self, *_):
        self.use_button.setEnabled(False)
        selected = self.selected_examples()
        self.use_button.setText("Use full dialogue" if len(selected) == len(self.examples) else "Use selected dialogue")
        if not selected:
            self.summary.setText("Select all to import the complete template, or check individual entries.")
            return
        unresolved = [row["trigger"] for row in selected
                      if row["trigger"] in self.conflicts and not self.decisions.get(row["trigger"])]
        if unresolved:
            names = ", ".join(unresolved[:3]) + (f" (+{len(unresolved) - 3} more)" if len(unresolved) > 3 else "")
            self.summary.setText(f"{len(selected)} of {len(self.examples)} selected. Resolve matching triggers: {names}. Choose above for all, or compare each line.")
            return
        try:
            apply_dialogue_examples(self.records, selected, self.decisions)
        except ValueError as exc:
            self.summary.setText(str(exc))
            return
        added = sum(row["trigger"] not in self.conflicts for row in selected)
        replaced = sum(row["trigger"] in self.conflicts and self.decisions.get(row["trigger"]) == "replace" for row in selected)
        self.summary.setText(f"{len(selected)} of {len(self.examples)} selected · {added} to add · {replaced} to replace · {len(selected) - added - replaced} existing to keep")
        self.use_button.setEnabled(self.worker is None and not self.closing)

    def accept(self):
        self.update_summary()
        if not self.use_button.isEnabled():
            return
        self.imported_records = apply_dialogue_examples(self.records, self.selected_examples(), self.decisions)
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
