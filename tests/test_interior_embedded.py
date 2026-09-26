"""Embedded room editing keeps drafts live through validation and project saves."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QShortcut
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from pixelheart.interior_editor import InteriorEditor
from tests.qt_support import QtTestCase


class EmbeddedInteriorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.project = self.root / "project" / "character.json"
        self.sheet = self.root / "sheet.png"
        with Image.new("RGBA", (32, 16), "#aabbcc") as image:
            image.save(self.sheet)
        self.host = QWidget()
        self.host.resize(1000, 760)
        layout = QVBoxLayout(self.host)
        self.editor = InteriorEditor(self.project, parent=self.host, embedded=True)
        layout.addWidget(self.editor)
        self.host.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.dispose()
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_embedded_chrome_and_shortcuts_belong_to_the_workspace(self):
        self.assertFalse(self.editor.isWindow())
        self.assertLess(self.editor.minimumWidth(), 1000)
        self.assertLess(self.editor.minimumHeight(), 700)
        margins = self.editor.layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (0, 0, 0, 0))
        buttons = [item.text() for item in self.editor.findChildren(QPushButton)
                   if item.parent() is self.editor]
        self.assertNotIn("Cancel", buttons)
        self.assertNotIn("Apply home", buttons)
        editor_shortcuts = [item for item in self.editor.findChildren(QShortcut)
                            if item.parent() is self.editor]
        self.assertTrue(editor_shortcuts)
        self.assertTrue(all(item.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
                            for item in editor_shortcuts))

    def test_prepare_publishes_to_selected_project_and_keeps_editor_and_stage_open(self):
        self.editor.load_atlas(self.sheet)
        self.editor.apply_tile(1)
        destination = self.root / "saved_elsewhere" / "character.json"
        finished = QSignalSpy(self.editor.finished)
        self.assertTrue(self.editor.prepare_design(destination))
        design = self.editor.result_design
        self.assertEqual(design, self.editor.draft.snapshot())
        self.assertEqual((destination.parent / design["atlas"]["asset"]).read_bytes(), self.sheet.read_bytes())
        self.assertFalse(self.project.parent.exists())
        self.assertEqual(self.editor.project_file, destination)
        self.assertTrue(self.editor.isVisible())
        self.assertTrue(self.editor.stage_root.is_dir())
        self.assertEqual(finished.count(), 0)
        self.editor.apply_tile(0)
        self.assertNotEqual(self.editor.draft.snapshot(), design)
        self.assertTrue(self.editor.prepare_design())
        self.assertEqual(self.editor.result_design["style"]["floor"], 0)

    def test_draft_changed_reports_edits_and_undo_but_not_refresh_or_preview(self):
        changes = QSignalSpy(self.editor.draft_changed)
        self.editor.refresh()
        self.assertEqual(changes.count(), 0)
        self.editor.load_atlas(self.sheet)
        self.assertEqual(changes.count(), 1)
        self.editor.apply_tile(1)
        self.assertEqual(changes.count(), 2)
        self.editor.undo()
        self.assertEqual(changes.count(), 3)
        self.editor.redo()
        self.assertEqual(changes.count(), 4)
        self.editor.preview_time.setCurrentIndex(2)
        self.editor.preview_lights.setCurrentIndex(1)
        self.editor.grid.setChecked(True)
        self.editor.refresh()
        self.assertEqual(changes.count(), 4)

    def test_failed_preparation_keeps_the_draft_available_for_repair(self):
        self.editor.load_atlas(self.sheet)
        reference = self.editor.draft.data["atlas"]["asset"]
        (self.editor.stage_root / reference).unlink()
        finished = QSignalSpy(self.editor.finished)
        before = self.editor.draft.snapshot()
        self.assertFalse(self.editor.prepare_design())
        self.assertEqual(self.editor.draft.snapshot(), before)
        self.assertIsNone(self.editor.result_design)
        self.assertIn("texture is missing", self.editor.status.text())
        self.assertFalse(self.project.parent.exists())
        self.assertTrue(self.editor.isVisible())
        self.assertTrue(self.editor.stage_root.is_dir())
        self.assertEqual(finished.count(), 0)

    def test_dispose_stops_timers_and_releases_staged_assets_once(self):
        self.editor.load_atlas(self.sheet)
        stage = self.editor.stage_root
        self.editor.timer.start()
        self.editor._fit_timer.start(100)
        with patch.object(self.editor._temporary, "cleanup", wraps=self.editor._temporary.cleanup) as cleanup:
            self.editor.dispose()
            self.editor.dispose()
            self.editor.schedule_fit()
            cleanup.assert_called_once()
        self.assertFalse(self.editor.timer.isActive())
        self.assertFalse(self.editor._fit_timer.isActive())
        self.assertFalse(stage.exists())


if __name__ == "__main__":
    unittest.main()
