"""Name-first actor selection with an explicit custom-ID option."""
from PySide6.QtCore import Qt
from pixelheart_core.birthdays import birthdays_on
from .location_picker import MapSelector


def vanilla_actors():
    return sorted({name for season in ("spring", "summer", "fall", "winter") for day in range(1, 29) for name in birthdays_on(season, day)})


class ActorSelector(MapSelector):
    def __init__(self, default="$npc", *, parent=None):
        super().__init__(default, compact=True, parent=parent)
        self.setAccessibleName("Scene character")
        self.combo.setAccessibleName("Choose a scene character")
        self.combo.lineEdit().setPlaceholderText("Search character names")
        self.custom.setAccessibleName("Custom character internal name")
        self.custom.setPlaceholderText("Exact internal name from another mod")
        self.custom.setMaxLength(192)
        self.set_options([("Your character", "$npc"), ("Farmer", "farmer"), *[(name, name) for name in vanilla_actors()]])

    def maxLength(self):
        return self.custom.maxLength()

    def set_options(self, options):
        options = list(options)
        if options == getattr(self, "_options", None):
            return
        self._options = options
        before = self._value
        self._loading = True
        self.combo.blockSignals(True)
        self.combo.clear()
        seen = set()
        for caption, identity in options:
            if identity not in seen:
                self.combo.addItem(caption, identity)
                seen.add(identity)
        self.combo.addItem("Other mod character…", None)
        self.custom_index = self.combo.count() - 1
        self.combo.blockSignals(False)
        self._loading = False
        self.set_value(before)

    def set_value(self, identity):
        self._loading = True
        self._value = str(identity)
        index = self.combo.findData(identity)
        if index < 0:
            self.custom.setText(str(identity))
        self.combo.setCurrentIndex(index if index >= 0 else self.custom_index)
        self._restore_label()
        self._render()
        self._loading = False

    def _render(self):
        self.custom.setVisible(self.combo.currentIndex() == self.custom_index)
        self.hint.hide()
        self.combo.setToolTip("Choose a character by name. They must also be included in the scene cast.")
        self.custom.setToolTip("Use the exact internal character name supplied by the other mod, and declare that mod as a dependency.")
