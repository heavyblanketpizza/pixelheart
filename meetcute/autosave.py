"""Save the open project quietly a moment after each change."""

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication

from . import game_import

AUTOSAVE_KEY = "project/autosave"


def autosave_enabled():
    value = game_import.game_import_settings().value(AUTOSAVE_KEY, True)
    return value if isinstance(value, bool) else str(value).strip().lower() not in ("false", "0", "no", "off")


def set_autosave_enabled(enabled):
    settings = game_import.game_import_settings()
    settings.setValue(AUTOSAVE_KEY, bool(enabled))
    settings.sync()


class AutosaveController(QObject):
    """Debounce saves; the window decides what to write.

    The window supplies ``project_file``, ``loading``, ``dirty``,
    ``project_history.gesture_open`` and ``quiet_save() -> bool``.
    """

    DELAY_MS = 1500
    RETRY_MS = 250

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)

    @property
    def enabled(self):
        return autosave_enabled()

    def set_enabled(self, enabled):
        set_autosave_enabled(enabled)
        if not enabled:
            self.timer.stop()
        elif self.window.dirty:
            self.schedule()

    def pending(self):
        return self.timer.isActive()

    def schedule(self):
        if self.enabled and self.window.project_file and not self.window.loading:
            self.timer.start(self.DELAY_MS)

    def _busy(self):
        history = getattr(self.window, "project_history", None)
        return (QApplication.mouseButtons() != Qt.MouseButton.NoButton
                or bool(getattr(history, "gesture_open", False)))

    def _timeout(self):
        if self._busy():
            self.timer.start(self.RETRY_MS)
            return
        self.flush()

    def flush(self, *, force=False):
        """Save now when autosave owns saving. False only when changes remain unsaved."""
        self.timer.stop()
        if not self.enabled or not self.window.project_file:
            return not self.window.dirty
        if not force and not self.window.dirty:
            return True
        return self.window.quiet_save()
