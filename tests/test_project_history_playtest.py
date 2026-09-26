"""Playtest edits share project history while release evidence survives undo."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart_core.playtesting import (
    content_fingerprint, export_is_current, playtest_cases, record_export, record_install,
)
from pixelheart_core.projects import ProjectError, load_project
from tests.qt_support import QtTestCase


class ProjectHistoryPlaytestTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-playtest-history-")
        self.root = Path(self.temporary.name)
        self.project_file = self.root / "character" / "character.json"
        self.error_patch = patch.object(MainWindow, "show_error")
        self.errors = self.error_patch.start()
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.error_patch.stop()
        self.temporary.cleanup()

    def history(self):
        return self.window.project_history.history

    def records(self):
        return self.window.document.get("creator", {}).get("tests", {})

    def installed_project(self):
        self.window.collect()
        document = record_export(self.window.document, self.root / "character.zip", b"exported pack")
        document = record_install(document, self.root / "Mods" / "Character", b"exported pack")
        self.window.load_document(document)
        page = self.window.playtest
        page.tests.setCurrentRow(0)
        self.assertTrue(page.pass_button.isEnabled())
        return page

    def test_observations_before_install_support_undo_and_redo_without_export_evidence(self):
        page = self.window.playtest
        page.tests.setCurrentRow(0)
        self.assertFalse(page.pass_button.isEnabled())
        page.test_notes.setPlainText("Ask someone to check the arrival dialogue.")
        self.assertEqual(self.records()["load"]["status"], "untested")
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.history().undo_count, 1)

        self.assertTrue(self.window.project_history.undo())
        self.assertNotIn("load", self.records())
        self.assertEqual(page.test_notes.toPlainText(), "")
        self.assertFalse(self.window.dirty)
        self.assertTrue(self.window.project_history.redo())
        self.assertEqual(page.test_notes.toPlainText(), "Ask someone to check the arrival dialogue.")
        self.assertEqual(self.records()["load"]["status"], "untested")
        self.assertFalse(page.pass_button.isEnabled())
        self.errors.assert_not_called()

    def test_result_and_notes_are_separate_steps_and_keep_install_evidence(self):
        page = self.installed_project()
        evidence = deepcopy(self.window.document["creator"])
        page.test_notes.setPlainText("Pack loads without red errors.")
        observation = deepcopy(self.records()["load"])
        page.save_test("passed")
        result = deepcopy(self.records()["load"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(self.history().undo_count, 2)

        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(self.records()["load"], observation)
        self.assertEqual(page.test_notes.toPlainText(), observation["notes"])
        self.assertTrue(self.window.project_history.undo())
        self.assertNotIn("load", self.records())
        self.assertFalse(self.window.dirty)
        self.assertEqual(page.test_notes.toPlainText(), "")
        for key, value in evidence.items():
            self.assertEqual(self.window.document["creator"][key], value)
        self.assertTrue(self.window.project_history.redo())
        self.assertTrue(self.window.project_history.redo())
        self.assertEqual(self.records()["load"], result)
        self.assertTrue(page.pass_button.isEnabled())
        self.errors.assert_not_called()

    def test_observations_for_different_cases_restore_independently(self):
        page = self.window.playtest
        page.tests.setCurrentRow(0)
        page.test_notes.setPlainText("Watch the SMAPI log.")
        page.tests.setCurrentRow(1)
        page.test_notes.setPlainText("Find the character by the river.")
        self.assertEqual(self.history().undo_count, 2)

        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(self.records()["load"]["notes"], "Watch the SMAPI log.")
        self.assertNotIn("meet", self.records())
        self.assertTrue(self.window.project_history.undo())
        self.assertFalse(self.records())
        self.assertTrue(self.window.project_history.redo())
        self.assertTrue(self.window.project_history.redo())
        page.tests.setCurrentRow(1)
        self.assertEqual(page.test_notes.toPlainText(), "Find the character by the river.")
        page.tests.setCurrentRow(0)
        self.assertEqual(page.test_notes.toPlainText(), "Watch the SMAPI log.")
        self.errors.assert_not_called()

    def test_undoing_content_change_restores_current_test_result_without_losing_evidence(self):
        page = self.installed_project()
        page.test_notes.setPlainText("The game loads correctly.")
        page.save_test("passed")
        evidence = deepcopy(self.window.document["creator"])
        original_fingerprint = content_fingerprint(self.window.document)
        self.window.identity.fields["name"].setText("A new name")
        page.refresh_testing()
        self.assertFalse(page.pass_button.isEnabled())
        self.assertFalse(export_is_current(self.window.document))
        self.assertTrue(next(case for case in playtest_cases(self.window.document)
                             if case["id"] == "load")["stale"])

        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(content_fingerprint(self.window.document), original_fingerprint)
        self.assertEqual(self.window.document["creator"], evidence)
        self.assertTrue(export_is_current(self.window.document))
        self.assertTrue(page.pass_button.isEnabled())
        case = next(case for case in playtest_cases(self.window.document) if case["id"] == "load")
        self.assertEqual(case["status"], "passed")
        self.assertFalse(case["stale"])
        self.errors.assert_not_called()

    def test_successful_save_keeps_observations_and_evidence_but_resets_history(self):
        page = self.installed_project()
        page.test_notes.setPlainText("Startup works; check the first meeting next.")
        page.save_test("passed")
        self.assertTrue(self.window.project_history.undo())
        evidence = deepcopy(self.window.document["creator"])
        self.assertTrue(self.history().can_undo)
        self.assertTrue(self.history().can_redo)

        self.assertTrue(self.window.save_to(self.project_file))
        self.assertFalse(self.history().can_undo)
        self.assertFalse(self.history().can_redo)
        self.assertFalse(self.window.dirty)
        persisted = load_project(self.project_file)
        self.assertEqual(persisted["creator"], evidence)
        self.assertEqual(self.window.document["creator"], evidence)
        page.test_notes.setPlainText("A second observation after saving.")
        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(page.test_notes.toPlainText(), "Startup works; check the first meeting next.")
        self.assertFalse(self.window.dirty)
        self.assertEqual(self.window.document["creator"], evidence)
        self.errors.assert_not_called()

    def test_failed_save_preserves_result_history_and_release_evidence(self):
        page = self.installed_project()
        page.test_notes.setPlainText("The portrait is incorrect.")
        page.save_test("failed")
        expected = deepcopy(self.window.document["creator"])
        before = (self.history().undo_count, self.history().redo_count)
        with patch("pixelheart.app.save_project", side_effect=ProjectError("disk unavailable")):
            self.assertFalse(self.window.save_to(self.project_file))
        self.assertEqual((self.history().undo_count, self.history().redo_count), before)
        self.assertEqual(self.window.document["creator"], expected)
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(self.records()["load"]["status"], "untested")
        self.assertTrue(self.window.project_history.redo())
        self.assertEqual(self.window.document["creator"], expected)
        self.errors.assert_called_once()

    def test_new_export_and_install_records_survive_undo_and_remain_dirty_until_saved(self):
        self.assertTrue(self.window.save_to(self.project_file))
        original_name = self.window.document["character"]["name"]
        self.window.identity.fields["name"].setText("Recorded version")
        self.assertEqual(self.history().undo_count, 1)
        payload = b"exported authored revision"
        self.window.document = record_export(self.window.document, self.root / "new.zip", payload)
        self.window.external_project_changed()
        self.window.document = record_install(self.window.document, self.root / "Mods" / "NPC", payload)
        self.window.external_project_changed()
        evidence = deepcopy(self.window.document["creator"])
        self.assertEqual(self.history().undo_count, 1)
        self.assertTrue(export_is_current(self.window.document))

        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(self.window.document["character"]["name"], original_name)
        self.assertEqual(self.window.document["creator"], evidence)
        self.assertFalse(export_is_current(self.window.document))
        self.assertFalse(self.history().is_dirty)
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.project_history.redo())
        self.assertEqual(self.window.document["creator"], evidence)
        self.assertTrue(export_is_current(self.window.document))
        self.assertTrue(self.window.project_history.undo())

        self.assertTrue(self.window.save())
        self.assertEqual(load_project(self.project_file)["creator"], evidence)
        self.assertFalse(self.window.dirty)
        self.assertFalse(self.history().can_undo)
        self.assertFalse(self.history().can_redo)
        self.errors.assert_not_called()


if __name__ == "__main__":
    unittest.main()
