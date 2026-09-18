"""Choose known location IDs, with an explicit escape hatch for custom maps."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QCompleter, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from pixelheart_core.locations import LOCATIONS_BY_ID, VANILLA_LOCATIONS, location_note
from .widgets import label


class MapSelector(QWidget):
    """A searchable choice which never saves unfinished search text as a map ID.

    ``set_value`` loads silently; ``setText`` is a legacy field-compatible setter
    which emits ``changed`` when the value changes. ``compact`` omits the inline
    explanation for use in a schedule table; the same text stays in tooltips.
    """

    changed = Signal()

    def __init__(self, default="Town", *, compact=False, parent=None):
        super().__init__(parent)
        self._loading = False
        self._value = ""
        self._compact = compact
        self._extra_locations = {}
        self.setAccessibleName("Map location")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4 if compact else 6)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(10)
        self.combo.setAccessibleName("Choose a map location")
        self.combo.setMaxVisibleItems(14)
        for location in VANILLA_LOCATIONS:
            self.combo.addItem(f"{location.name} · {location.id}", location.id)
            self.combo.setItemData(self.combo.count() - 1, location_note(location.id), Qt.ItemDataRole.ToolTipRole)
        self.combo.addItem("Custom / mod location…", None)
        self.custom_index = self.combo.count() - 1
        self.combo.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.combo.lineEdit().setPlaceholderText("Search by place or location ID")
        self.combo.lineEdit().installEventFilter(self)
        layout.addWidget(self.combo)
        self.custom = QLineEdit()
        # Keep loaded legacy IDs exact; project validation owns the length rule.
        self.custom.setMaxLength(8000)
        self.custom.setPlaceholderText("Existing location ID, e.g. Author.Mod_Cottage")
        self.custom.setAccessibleName("Custom or mod location internal name")
        self.custom.setToolTip("Use the location's exact internal name, not a Maps/ asset path. The map must already be supplied by the game or a mod.")
        layout.addWidget(self.custom)
        self.hint = label("", "hint", True)
        self.hint.setVisible(not compact)
        layout.addWidget(self.hint)
        self.setFocusProxy(self.combo)
        self.combo.currentIndexChanged.connect(self._selected)
        self.combo.lineEdit().editingFinished.connect(self._restore_label)
        self.custom.textChanged.connect(self._custom_changed)
        self.set_value(default)

    def value(self):
        return self._value

    def text(self):
        return self.value()

    def set_value(self, location_id):
        if not isinstance(location_id, str):
            raise TypeError("Location IDs must be text.")
        self._loading = True
        self._value = location_id
        known = self.combo.findData(location_id) >= 0
        if not known:
            self.custom.setText(location_id)
        self.combo.setCurrentIndex(self.combo.findData(location_id) if known else self.custom_index)
        self._restore_label()
        self._render()
        self._loading = False

    def setText(self, location_id):
        before = self._value
        self.set_value(location_id)
        if self._value != before:
            self.changed.emit()

    def set_extra_locations(self, locations):
        """Offer this project's actual places without changing saved references."""
        extras = dict(locations)
        if extras == self._extra_locations:
            return
        before = self._value
        self._loading = True
        self.combo.blockSignals(True)
        for identity in self._extra_locations:
            index = self.combo.findData(identity)
            if index >= 0 and identity not in LOCATIONS_BY_ID:
                self.combo.removeItem(index)
        self._extra_locations = extras
        self.custom_index = self.combo.findData(None)
        for identity, name in extras.items():
            if self.combo.findData(identity) < 0:
                self.combo.insertItem(self.custom_index, name + " · your places", identity)
                self.custom_index += 1
        self.combo.blockSignals(False)
        self._loading = False
        self.set_value(before)

    def _selected(self, index):
        if self._loading:
            return
        chosen = self.custom.text() if index == self.custom_index else self.combo.itemData(index)
        if chosen is None:
            return
        before, self._value = self._value, chosen
        self._render()
        if self._value != before:
            self.changed.emit()

    def _custom_changed(self, content):
        if self._loading or self.combo.currentIndex() != self.custom_index:
            return
        before, self._value = self._value, content
        self._render()
        if self._value != before:
            self.changed.emit()

    def _render(self):
        is_custom = self.combo.currentIndex() == self.custom_index
        self.custom.setVisible(is_custom)
        note = "A location supplied by this project. Check its entrance, exit, and walkable tiles in-game." if self._value in self._extra_locations else location_note(self._value)
        self.hint.setText(note)
        self.combo.setToolTip(note + "\nType to search, then choose a matching location. Search text is not saved.")
        self.setToolTip(note)

    def _restore_label(self):
        # QComboBox's editable line is a search surface, never an ID editor.
        self.combo.setEditText(self.combo.itemText(self.combo.currentIndex()))

    def eventFilter(self, watched, event):
        if watched is self.combo.lineEdit() and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._restore_label()
            return True
        return super().eventFilter(watched, event)
