"""Connect a story scene to authored daily life without inventing runtime effects."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget, QListWidgetItem,
    QPlainTextEdit, QComboBox, QScrollArea, QSizePolicy, QLabel,
)

from pixelheart_core.life import DAY_NAMES, MOMENTS, life_issues, new_life_record
from pixelheart_core.story import new_planned_effect, event_references
from .widgets import button, card, label


KINDS = {"dialogues": "Story reaction", "routines": "Conditional routine", "spouse_dialogue": "Marriage dialogue"}


class EventAftermath(QWidget):
    """A view over existing Life records plus local-only event planning notes.

    ``load`` and ``refresh_context`` never change source records. ``changed`` asks
    the event editor to call ``capture``; Life edits use that editor's own signal
    so the application's existing history and persistence stay authoritative.
    """

    changed = Signal()

    def __init__(self, workshop):
        super().__init__()
        self.workshop = workshop
        self.loading = False
        self._event = {}
        self.effects = []
        self.current_effect = -1
        self._character = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(label("What remains after this scene?", "sectionTitle", True))
        self.notes = QPlainTextEdit()
        self.notes.setAccessibleName("Aftermath notes")
        self.notes.setPlaceholderText("What will the player notice later? A quiet memory with no mechanical change is valid too.")
        self.notes.setMinimumHeight(70)
        self.notes.setMaximumHeight(100)
        root.addWidget(self.notes)
        self.notes.textChanged.connect(self._notes_changed)

        available, layout = card("Available now · daily life")
        layout.addWidget(label(
            "Enabled rules are checked each morning after the scene is complete. They apply only when all their conditions match; later matching rules take priority. Special game dialogue can still take precedence.",
            "muted", True))
        self.linked = QListWidget()
        self.linked.setAccessibleName("Daily life linked to this event")
        self.linked.setWordWrap(True)
        self.linked.setMinimumHeight(70)
        self.linked.setMaximumHeight(115)
        self.linked.currentItemChanged.connect(self._show_linked)
        self.linked.itemActivated.connect(lambda *_: self.open_linked())
        layout.addWidget(self.linked)
        self.linked_detail = QPlainTextEdit()
        self.linked_detail.setReadOnly(True)
        self.linked_detail.setAccessibleName("Linked daily life behavior")
        self.linked_detail.setMinimumHeight(80)
        self.linked_detail.setMaximumHeight(130)
        layout.addWidget(self.linked_detail)
        self.open_button = button("Open selected rule", self.open_linked)
        layout.addWidget(self.open_button)
        actions = QVBoxLayout()
        self.create_buttons = {}
        for kind, caption in KINDS.items():
            widget = button("+ " + caption, lambda checked=False, kind=kind: self.create_rule(kind))
            self.create_buttons[kind] = widget
            actions.addWidget(widget)
        layout.insertLayout(2, actions)
        existing = QVBoxLayout()
        self.existing = QComboBox()
        self.existing.setAccessibleName("Existing daily life rule to link")
        self.existing.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.existing.setMinimumContentsLength(16)
        self.existing.setMinimumWidth(0)
        existing.addWidget(self.existing)
        self.link_button = button("Link as draft", self.link_existing)
        existing.addWidget(self.link_button)
        layout.insertLayout(3, existing)
        self.existing.currentIndexChanged.connect(self._update_link_button)
        self.notice = label("", "notice", True)
        layout.addWidget(self.notice)
        root.addWidget(available)

        planned, layout = card("Planning only · future effects")
        layout.addWidget(label(
            "Mail, elapsed-day gates, remembered choices, item rewards, and world edits are not compiled here. Keep an effect pending while developing it, or explicitly omit it from this playable version. Pending effects prevent marking the scene ready.",
            "muted", True))
        self.effect_list = QListWidget()
        self.effect_list.setAccessibleName("Planned event effects")
        self.effect_list.setWordWrap(True)
        self.effect_list.setMinimumHeight(70)
        self.effect_list.setMaximumHeight(145)
        self.effect_list.currentRowChanged.connect(self.select_effect)
        layout.addWidget(self.effect_list)
        actions = QHBoxLayout()
        self.add_effect_button = button("+ Planned effect", self.add_effect)
        self.remove_effect_button = button("Remove effect", self.remove_effect, "quiet")
        actions.addWidget(self.add_effect_button)
        actions.addWidget(self.remove_effect_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.effect_description = QPlainTextEdit()
        self.effect_description.setAccessibleName("Planned effect description")
        self.effect_description.setPlaceholderText("Describe the intended change. This note does not implement it.")
        self.effect_description.setMinimumHeight(70)
        self.effect_description.setMaximumHeight(110)
        self.effect_resolution = QComboBox()
        self.effect_resolution.addItem("Pending — needs a decision", "pending")
        self.effect_resolution.addItem("Omitted from this playable version", "omitted")
        self.effect_resolution.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.effect_resolution.setMinimumContentsLength(16)
        self.effect_resolution.setMinimumWidth(0)
        self.effect_resolution.setAccessibleName("Planned effect decision")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Intended effect", self.effect_description)
        form.addRow("Decision", self.effect_resolution)
        layout.addLayout(form)
        self.effect_description.textChanged.connect(self.edit_effect)
        self.effect_resolution.currentIndexChanged.connect(self.edit_effect)
        root.addWidget(planned)

        references, layout = card("Used by")
        self.references = label("No incoming references.", "muted", True)
        self.references.setTextFormat(Qt.TextFormat.PlainText)
        self.references.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.references)
        root.addWidget(references)
        root.addStretch()
        # Native detail panes are often narrower than the whole application.
        # Text wraps without making the parent scroll sideways; combos keep a
        # bounded size hint even when a saved rule has a long title.
        for heading in self.findChildren(QLabel):
            heading.setWordWrap(True)
            heading.setMinimumWidth(0)
            heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.references.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._render_effects()

    def load(self, event, character=None):
        self.loading = True
        self._event = deepcopy(event or {})
        story = self._event.get("story", {})
        self.notes.setPlainText(story.get("aftermath_notes", ""))
        self.effects = deepcopy(story.get("planned_effects", []))
        self._render_effects()
        self.loading = False
        self.refresh_context(character)

    def capture(self, event):
        if event.get("id") != self._event.get("id"):
            return
        event.setdefault("story", {}).update(
            aftermath_notes=self.notes.toPlainText(), planned_effects=deepcopy(self.effects))
        self._event = deepcopy(event)

    def focus_field(self, field):
        """Open the exact planning control named by an export issue."""
        parts = field.split(".")
        target = self.notes
        if "planned_effects" in parts:
            offset = parts.index("planned_effects") + 1
            if len(parts) > offset and parts[offset].isdigit():
                index = int(parts[offset])
                if 0 <= index < len(self.effects):
                    self.effect_list.setCurrentRow(index)
                    target = self.effect_resolution if parts[-1] == "resolution" else self.effect_description
                else:
                    target = self.effect_list
            else:
                target = self.effect_list
        target.setFocus()
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(target)
                break
            parent = parent.parentWidget()
        return target

    def _notes_changed(self):
        if not self.loading:
            self.changed.emit()

    def _editors(self):
        return getattr(getattr(self.workshop.window, "life", None), "editors", {})

    def _context(self, character=None):
        result = deepcopy(character if character is not None else self.workshop.character())
        life = getattr(self.workshop.window, "life", None)
        if life is not None:
            result["life"] = life.dump()
        return result

    def _current_event(self):
        return next((row for row in self._character.get("events", [])
                     if isinstance(row, dict) and row.get("id") == self._event.get("id")), self._event)

    def _can_link(self, kind):
        event = self._current_event()
        return (bool(event.get("id")) and event.get("story", {}).get("repeat", "once") == "once"
                and kind in self._editors()
                and not (kind == "spouse_dialogue" and self._character.get("romanceable") is False))

    def refresh_context(self, character=None):
        """Refresh links and prerequisites, keeping unsaved planning prose intact."""
        self._character = self._context(character)
        event_id = self._event.get("id")
        selected = self.linked.currentItem().data(Qt.ItemDataRole.UserRole) if self.linked.currentItem() else None
        self.linked.clear()
        previous = self.existing.currentData()
        self.existing.blockSignals(True)
        self.existing.clear()
        self.existing.addItem("Choose an existing rule with no event prerequisite", None)
        for kind, editor in self._editors().items():
            can_link = self._can_link(kind)
            for row in editor.records:
                identity = (kind, row.get("id"))
                required = row.get("conditions", {}).get("after_event_id")
                title = f"{'Included in mod' if row.get('enabled') else 'Draft'} · {KINDS[kind]} · {row.get('name') or 'Untitled'}"
                if event_id and required == event_id:
                    item = QListWidgetItem(title)
                    item.setData(Qt.ItemDataRole.UserRole, identity)
                    self.linked.addItem(item)
                    if identity == selected:
                        self.linked.setCurrentItem(item)
                elif not required and can_link:
                    self.existing.addItem(title, identity)
            self.create_buttons[kind].setEnabled(can_link and len(editor.records) < 100)
            if len(editor.records) >= 100:
                tooltip = "This collection already has its maximum of 100 rules."
            elif kind == "spouse_dialogue" and self._character.get("romanceable") is False:
                tooltip = "Enable romance in the character's identity to create marriage dialogue here."
            elif not can_link:
                tooltip = "Select a one-time event to unlock lasting daily life."
            else:
                tooltip = "The new rule is a draft until reviewed in its editor."
            self.create_buttons[kind].setToolTip(tooltip)
        if previous is not None:
            index = self.existing.findData(previous)
            self.existing.setCurrentIndex(max(0, index))
        self.existing.blockSignals(False)
        self._update_link_button()
        if self.linked.count() and self.linked.currentRow() < 0:
            self.linked.setCurrentRow(0)
        self._show_linked()
        event = self._current_event()
        if event.get("story", {}).get("repeat", "once") != "once":
            self.notice.setText("Daily scenes forget completion. Use a one-time scene to unlock lasting dialogue or routines.")
        elif not event_id:
            self.notice.setText("Choose a saved event before linking daily life.")
        else:
            self.notice.setText("New and newly linked rules remain drafts. Open a rule to write it and review its conditions before including it in the mod. Linking an included rule returns it to draft.")
        references = event_references(self._character, event_id) if event_id else []
        self.references.setText("\n".join(references) if references else "No incoming references.")

    def _find_rule(self, identity):
        if not identity:
            return None, None, None
        kind, record_id = identity
        editor = self._editors().get(kind)
        if editor is not None:
            for index, row in enumerate(editor.records):
                if row.get("id") == record_id:
                    return editor, index, row
        return None, None, None

    def _show_linked(self, *_):
        item = self.linked.currentItem()
        editor, index, row = self._find_rule(item.data(Qt.ItemDataRole.UserRole) if item else None)
        self.open_button.setEnabled(row is not None)
        if row is None:
            self.linked_detail.setPlainText("No daily-life rule is linked to this event yet.")
            return
        conditions = row.get("conditions", {})
        gates = ["After this event is complete"]
        for key in ("season", "weather", "weekday", "relationship"):
            selected = conditions.get(key, "any")
            if selected != "any":
                gates.append(f"{key.title()}: {DAY_NAMES.get(selected, selected)}")
        if editor.kind == "routines" and conditions.get("relationship", "any") == "any":
            gates.append("Before marriage (all unmarried stages)")
        if conditions.get("min_hearts"):
            gates.append(f"At least {conditions['min_hearts']} hearts")
        if conditions.get("min_house_upgrade"):
            gates.append(f"Farmhouse upgrade {conditions['min_house_upgrade']} or higher")
        if editor.kind == "spouse_dialogue":
            if conditions.get("relationship", "any") == "any":
                gates.append("Married to this character")
            gates.append(MOMENTS.get(row.get("moment"), "Morning at home"))
        status = "Included in export; evaluated each morning." if row.get("enabled") else "Draft: this rule has no effect in the game."
        if row.get("enabled"):
            prefix = f"life.{editor.kind}.{index}"
            errors = [issue["message"] for issue in life_issues(self._character)
                      if issue.get("level") == "error" and
                      (issue["field"] == prefix or issue["field"].startswith(prefix + "."))]
            if errors:
                status = "Export blocked: " + " ".join(errors)
        if editor.kind == "routines":
            content = "\n".join(f"{stop.get('time', '')} · {stop.get('location', '')} ({stop.get('x', 0)}, {stop.get('y', 0)}) · {stop.get('activity', '')}"
                                for stop in row.get("stops", [])) or "No stops written yet."
        else:
            content = row.get("text") or "No dialogue written yet."
        self.linked_detail.setPlainText(status + "\n" + "; ".join(gates) + "\n\n" + content)

    def _update_link_button(self, *_):
        identity = self.existing.currentData()
        self.link_button.setEnabled(bool(identity) and self._can_link(identity[0]))

    def _open_rule(self, kind, index):
        self.workshop.window.open_life_editor(kind)
        self._editors()[kind].list.setCurrentRow(index)

    def open_linked(self):
        item = self.linked.currentItem()
        editor, index, row = self._find_rule(item.data(Qt.ItemDataRole.UserRole) if item else None)
        if row is not None:
            self._open_rule(editor.kind, index)

    def create_rule(self, kind):
        self._character = self._context()
        editor = self._editors().get(kind)
        if not self._can_link(kind) or editor is None or len(editor.records) >= 100:
            self.refresh_context()
            return None
        row = new_life_record(kind, self._character)
        title = self._current_event().get("name") or "this scene"
        row["name"] = f"After {title}"[:100]
        row["conditions"]["after_event_id"] = self._event["id"]
        editor.records.append(row)
        index = len(editor.records) - 1
        editor.refresh(index)
        editor.changed.emit()
        self.refresh_context()
        self._open_rule(kind, index)
        return row

    def link_existing(self):
        identity = self.existing.currentData()
        self._character = self._context()
        editor, index, row = self._find_rule(identity)
        if row is None or not self._can_link(identity[0]) or row.get("conditions", {}).get("after_event_id"):
            self.refresh_context()
            return
        row["conditions"]["after_event_id"] = self._event["id"]
        row["enabled"] = False
        editor.refresh(index)
        editor.changed.emit()
        self.refresh_context()
        self._open_rule(identity[0], index)

    @staticmethod
    def _effect_caption(effect):
        status = "Omitted" if effect.get("resolution") == "omitted" else "Pending"
        description = " ".join(effect.get("description", "").split()) or "Describe the intended effect"
        return f"{status} · {description[:140]}"

    def _render_effects(self, selected=0):
        blocked = self.effect_list.blockSignals(True)
        self.effect_list.clear()
        self.effect_list.addItems([self._effect_caption(effect) for effect in self.effects])
        self.effect_list.blockSignals(blocked)
        self.add_effect_button.setEnabled(len(self.effects) < 100)
        if self.effects:
            self.effect_list.setCurrentRow(max(0, min(selected, len(self.effects) - 1)))
        else:
            self.select_effect(-1)

    def select_effect(self, index):
        was_loading = self.loading
        self.loading = True
        self.current_effect = index
        valid = 0 <= index < len(self.effects)
        self.effect_description.setEnabled(valid)
        self.effect_resolution.setEnabled(valid)
        self.remove_effect_button.setEnabled(valid)
        effect = self.effects[index] if valid else {}
        self.effect_description.setPlainText(effect.get("description", ""))
        self.effect_resolution.setCurrentIndex(max(0, self.effect_resolution.findData(effect.get("resolution", "pending"))))
        self.loading = was_loading

    def add_effect(self):
        if len(self.effects) >= 100:
            return
        self.effects.append(new_planned_effect())
        self._render_effects(len(self.effects) - 1)
        self.changed.emit()
        self.effect_description.setFocus()

    def edit_effect(self, *_):
        if self.loading or not 0 <= self.current_effect < len(self.effects):
            return
        effect = self.effects[self.current_effect]
        effect.update(description=self.effect_description.toPlainText(), resolution=self.effect_resolution.currentData())
        self.effect_list.item(self.current_effect).setText(self._effect_caption(effect))
        self.changed.emit()

    def remove_effect(self):
        if 0 <= self.current_effect < len(self.effects):
            index = self.current_effect
            self.effects.pop(index)
            self._render_effects(index)
            self.changed.emit()
