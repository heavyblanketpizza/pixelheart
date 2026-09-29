"""The autosave controller waits for a pause, then asks the window to save."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QObject, QSettings, Qt
from PySide6.QtWidgets import QApplication

from pixelheart.autosave import AutosaveController, autosave_enabled, set_autosave_enabled
from tests.qt_support import QtTestCase


class FakeHistory:
    gesture_open = False


class FakeWindow(QObject):
    def __init__(self):
        super().__init__()
        self.project_file = Path("character.json")
        self.loading = False
        self.dirty = True
        self.project_history = FakeHistory()
        self.saves = 0
        self.result = True

    def quiet_save(self):
        self.saves += 1
        self.dirty = not self.result
        return self.result


class AutosaveControllerTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.window = FakeWindow()
        self.autosave = AutosaveController(self.window)
        self.addCleanup(self.window.deleteLater)

    def test_enabled_by_default_and_the_choice_persists(self):
        self.assertTrue(autosave_enabled())
        set_autosave_enabled(False)
        self.assertFalse(autosave_enabled())
        self.assertIn(self.settings.value("project/autosave"), (False, "false"))

    def test_nothing_is_scheduled_without_a_project_file(self):
        self.window.project_file = None
        self.autosave.schedule()
        self.assertFalse(self.autosave.pending())

    def test_nothing_is_scheduled_while_a_project_loads(self):
        self.window.loading = True
        self.autosave.schedule()
        self.assertFalse(self.autosave.pending())

    def test_a_burst_of_changes_saves_once_after_the_pause(self):
        for _ in range(3):
            self.autosave.schedule()
        self.assertTrue(self.autosave.pending())
        self.assertEqual(self.autosave.timer.interval(), AutosaveController.DELAY_MS)
        self.autosave.timer.timeout.emit()
        self.assertEqual(self.window.saves, 1)

    def test_waits_while_a_mouse_button_is_held(self):
        with patch("pixelheart.autosave.QApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton):
            self.autosave.timer.timeout.emit()
        self.assertEqual(self.window.saves, 0)
        self.assertTrue(self.autosave.pending())
        self.assertEqual(self.autosave.timer.interval(), AutosaveController.RETRY_MS)
        self.autosave.timer.timeout.emit()
        self.assertEqual(self.window.saves, 1)

    def test_waits_while_a_history_gesture_is_open(self):
        self.window.project_history.gesture_open = True
        self.autosave.timer.timeout.emit()
        self.assertEqual(self.window.saves, 0)
        self.assertTrue(self.autosave.pending())

    def test_flush_saves_only_when_something_changed(self):
        self.window.dirty = False
        self.assertTrue(self.autosave.flush())
        self.assertEqual(self.window.saves, 0)
        self.assertTrue(self.autosave.flush(force=True))
        self.assertEqual(self.window.saves, 1)

    def test_flush_reports_a_failed_save(self):
        self.window.result = False
        self.assertFalse(self.autosave.flush())
        self.assertEqual(self.window.saves, 1)

    def test_turned_off_it_never_saves_and_flush_reports_the_dirty_state(self):
        self.autosave.schedule()
        self.autosave.set_enabled(False)
        self.assertFalse(self.autosave.pending())
        self.assertFalse(self.autosave.flush(force=True))
        self.window.dirty = False
        self.assertTrue(self.autosave.flush())
        self.assertEqual(self.window.saves, 0)

    def test_turning_it_back_on_saves_pending_changes_soon(self):
        set_autosave_enabled(False)
        self.autosave.set_enabled(True)
        self.assertTrue(self.autosave.pending())
