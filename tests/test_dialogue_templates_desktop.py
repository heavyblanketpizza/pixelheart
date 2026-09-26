"""Full dialogue imports preserve writing, raw commands, and project identity."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton
from shiboken6 import isValid

from pixelheart.app import MainWindow
from pixelheart.dialogue_templates import DialogueTemplateDialog
from pixelheart.editors import DialoguePage
from pixelheart_core.projects import load_project, save_project
from pixelheart_core.local_templates import load_project_dialogue
from tests.qt_support import QtTestCase


def example_payload():
    return {"examples": [
        {"trigger": "Introduction", "text": "Hello, @.$h#$b#Nice to meet you.",
         "title": "First meeting", "when": "The first introduction.", "lesson": "Use @ for the farmer."},
        {"trigger": "Mon", "text": "Another Monday.$s", "title": "Weekday greeting",
         "when": "On Mondays.", "lesson": "$s changes the portrait."},
        {"trigger": "summer_Mon", "text": "Summer is here.#$e#Bring some water.",
         "title": "Seasonal greeting", "when": "Mondays in summer.", "lesson": "A season narrows the trigger."},
        {"trigger": "Mon2", "text": "I'm glad we are friends.$h", "title": "Growing friendship",
         "when": "Monday with at least two hearts.", "lesson": "A number changes the friendship threshold."},
    ]}


def full_dialogue_payload(count):
    """An archive-sized synthetic fixture; contains no copied game dialogue."""
    rows = example_payload()["examples"]
    for index in range(count - len(rows)):
        trigger = ("AcceptGift_(O)74", "divorced", "danceRejection", "Resort_Bar")[index] if index < 4 else f"Archive_Key_{index:03}"
        rows.append({
            "trigger": trigger,
            "text": f"Synthetic entry {index}: @, choose.$q 17/18 test_question#Pick one?#$r 17 0 test_yes#Yes.$h#$r 18 0 test_no#No.$s#$e#A second part.$d test_flag#First|Second",
            "title": trigger, "when": "An additional archive condition.",
            "lesson": "Preserve the raw trigger and command syntax while editing.",
        })
    return {"examples": rows}


class DialogueTemplateDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-dialogue-examples-")
        self.root = Path(self.temporary.name)
        self.project_file = self.root / "character.json"
        self.widgets = []
        self.cache_patch = patch("pixelheart.game_import.game_import_settings", side_effect=lambda: QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat))
        self.cache_patch.start()
        self.load_patch = patch("pixelheart.dialogue_templates.load_project_dialogue", return_value=example_payload())
        self.read_template = self.load_patch.start()
        self.records = [{"id": "my-introduction", "trigger": " Introduction ", "text": "My own introduction.",
                         "extension": {"notes": ["Keep this metadata."]}}]

    def tearDown(self):
        for widget in reversed(self.widgets):
            if not isValid(widget):
                continue
            if isinstance(widget, MainWindow):
                widget.dirty = False
            if isinstance(widget, DialogueTemplateDialog) and widget.worker is not None:
                widget.reject()
                self.assertTrue(widget.worker.wait(2000), "Dialogue worker did not stop during cleanup")
                self.app.processEvents()
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.load_patch.stop()
        self.cache_patch.stop()
        self.temporary.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def dialog(self, records=None):
        return self.keep(DialogueTemplateDialog(self.records if records is None else records,
                                                project_file=self.project_file))

    def wait_until(self, predicate, message="Qt work did not finish"):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), message)

    def load(self, dialog, count=4):
        dialog.load_button.click()
        self.wait_until(lambda: dialog.worker is None)
        self.assertEqual(len(dialog.examples), count)

    def visit_page_dialog(self, page, action, payload=None):
        # Exercise the real browser and commit boundary without a nested modal loop.
        def interact(dialog):
            self.assertEqual(dialog.project_file, page.project_file)
            dialog.receive_examples(payload if payload is not None else example_payload())
            action(dialog)
            return dialog.result()
        with patch.object(DialogueTemplateDialog, "exec", interact):
            page.open_examples()

    def test_open_and_browse_are_read_only_and_never_load_until_requested(self):
        original = deepcopy(self.records)
        dialog = self.dialog()
        self.read_template.assert_not_called()
        self.assertEqual(dialog.examples, [])
        self.assertFalse(dialog.use_button.isEnabled())
        self.load(dialog)
        self.read_template.assert_called_once()
        self.assertEqual(self.read_template.call_args.args, (dialog.character.currentData(), self.project_file))
        self.assertEqual(dialog.preview.text(), "Hello, Farmer.\nNice to meet you.")
        self.assertEqual(dialog.raw_text.toPlainText(), example_payload()["examples"][0]["text"])
        self.assertTrue(all(dialog.list.item(i).data(Qt.ItemDataRole.CheckStateRole) is None for i in range(4)))
        self.assertEqual(dialog.use_button.text(), "Use full dialogue")
        dialog.list.setCurrentRow(2)
        self.assertEqual(dialog.preview.text(), "Summer is here.\n\nBring some water.")
        self.assertEqual(dialog.when.text(), example_payload()["examples"][2]["when"])
        self.assertEqual(dialog.lesson.text(), example_payload()["examples"][2]["lesson"])
        self.assertIsNone(dialog.imported_records)
        self.assertEqual(dialog.records, original)
        self.assertEqual(self.records, original)
        dialog.reject()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_warning_replaces_matching_lines_without_conflict_choices_or_second_modal(self):
        records = deepcopy(self.records) + [{"id": "custom", "trigger": "MyCustomKey", "text": "Keep my writing."}]
        dialog = self.dialog(records)
        self.assertFalse(dialog.overwrite_warning.isHidden())
        self.assertIn("matching", dialog.overwrite_warning.text())
        dialog.receive_examples(example_payload())
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertFalse(dialog.overwrite_warning.isHidden())
        self.assertIn("1 matching dialogue entry", dialog.overwrite_warning.text())
        self.assertIn("3 to add", dialog.summary.text())
        self.assertIn("1 to overwrite", dialog.summary.text())
        self.assertIn("1 other entries kept", dialog.summary.text())
        self.assertFalse(hasattr(dialog, "conflict_choice"))
        self.assertFalse(hasattr(dialog, "bulk_conflict_choice"))
        self.assertFalse(hasattr(dialog, "select_none_button"))
        with patch("PySide6.QtWidgets.QMessageBox.question") as question, patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
            dialog.accept()
        question.assert_not_called()
        warning.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        imported = dialog.imported_records
        self.assertEqual(len(imported), 5)
        self.assertEqual(imported[0]["id"], records[0]["id"])
        self.assertEqual(imported[0]["extension"], records[0]["extension"])
        self.assertEqual(imported[0]["text"], example_payload()["examples"][0]["text"])
        self.assertEqual(imported[1], records[1])
        self.assertEqual([row["trigger"] for row in imported[2:]], ["Mon", "summer_Mon", "Mon2"])
        self.assertEqual(len({row["id"] for row in imported}), 5)
        self.assertEqual(dialog.records, records)
        self.assertEqual(self.records[0]["text"], "My own introduction.")

    def test_warning_is_hidden_when_loaded_template_has_no_matches(self):
        dialog = self.dialog([{"id": "custom", "trigger": "MyCustomKey", "text": "My own writing."}])
        self.assertFalse(dialog.overwrite_warning.isHidden())
        dialog.receive_examples(example_payload())
        self.assertTrue(dialog.overwrite_warning.isHidden())
        self.assertIn("4 to add", dialog.summary.text())
        self.assertIn("0 to overwrite", dialog.summary.text())
        self.assertIn("1 other entries kept", dialog.summary.text())
        self.assertTrue(dialog.use_button.isEnabled())
        empty = self.dialog([])
        self.assertTrue(empty.overwrite_warning.isHidden())
        empty.receive_examples(example_payload())
        self.assertTrue(empty.overwrite_warning.isHidden())

    def test_duplicate_matching_rows_are_overwritten_without_losing_identity_or_metadata(self):
        records = deepcopy(self.records) + [
            {"id": "second-intro", "trigger": "Introduction", "text": "Second draft.", "extension": {"draft": 2}},
            {"id": "custom", "trigger": "Custom", "text": "My own unrelated line."},
        ]
        dialog = self.dialog(records)
        dialog.receive_examples(example_payload())
        self.assertIn("2 matching dialogue entries", dialog.overwrite_warning.text())
        self.assertIn("3 to add", dialog.summary.text())
        self.assertIn("2 to overwrite", dialog.summary.text())
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.accept()
        imported = dialog.imported_records
        self.assertEqual(len(imported), 6)
        for index in (0, 1):
            self.assertEqual(imported[index]["id"], records[index]["id"])
            self.assertEqual(imported[index]["extension"], records[index]["extension"])
            self.assertEqual(imported[index]["text"], example_payload()["examples"][0]["text"])
        self.assertEqual(imported[2], records[2])

    def test_switching_character_clears_loaded_data_and_updates_project_instructions(self):
        dialog = self.dialog()
        self.load(dialog)
        dialog.search.setText("Monday")
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.character.setCurrentIndex(1)
        self.assertEqual(dialog.examples, [])
        self.assertEqual(dialog.list.count(), 0)
        self.assertEqual(dialog.conflicts, {})
        self.assertEqual(dialog.search.text(), "")
        self.assertIsNone(dialog.imported_records)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertTrue(dialog.detail.isHidden())
        self.assertIn("Characters/Dialogue/Elliott", dialog.game_source.commands.toPlainText())
        self.assertIn("Characters_Dialogue_Elliott.json", dialog.game_source.instructions.text())
        self.assertEqual(dialog.game_source.folder.text(), str(self.root / "dialogue"))
        self.assertEqual(self.read_template.call_count, 1)
        self.load(dialog)
        self.assertEqual(self.read_template.call_args.args, (dialog.character.currentData(), self.project_file))
        self.assertTrue(dialog.use_button.isEnabled())

    def test_full_exports_import_every_raw_entry_without_individual_selection(self):
        for character_index, count in enumerate((318, 274)):
            with self.subTest(count=count):
                payload = full_dialogue_payload(count)
                self.read_template.return_value = payload
                dialog = self.dialog([])
                dialog.character.setCurrentIndex(character_index)
                self.load(dialog, count)
                self.assertEqual(dialog.list.count(), count)
                self.assertTrue(all(dialog.list.item(i).data(Qt.ItemDataRole.CheckStateRole) is None for i in range(count)))
                self.assertEqual(dialog.use_button.text(), "Use full dialogue")
                self.assertTrue(dialog.use_button.isEnabled())
                dialog.list.setCurrentRow(4)
                self.assertEqual(dialog.raw_text.toPlainText(), payload["examples"][4]["text"])
                self.assertEqual(dialog.preview.text(), payload["examples"][4]["text"])
                self.assertIn("Script preview", dialog.preview_note.text())
                dialog.accept()
                imported = dialog.imported_records
                self.assertEqual(len(imported), count)
                self.assertEqual([(row["trigger"], row["text"]) for row in imported],
                                 [(row["trigger"], row["text"]) for row in payload["examples"]])
                self.assertEqual(len({row["id"] for row in imported}), count)
                self.assertTrue(all(set(row) == {"id", "trigger", "text"} for row in imported))

    def test_search_filters_preview_without_removing_entries_from_import(self):
        payload = full_dialogue_payload(118)
        dialog = self.dialog([])
        dialog.receive_examples(payload)
        dialog.search.setText("Archive_Key_020")
        visible = [i for i in range(dialog.list.count()) if not dialog.list.item(i).isHidden()]
        self.assertEqual(len(visible), 1)
        self.assertEqual(dialog.examples[visible[0]]["trigger"], "Archive_Key_020")
        self.assertIn("Showing 1 of 118", dialog.match_count.text())
        dialog.search.setText("No possible matching entry")
        self.assertTrue(dialog.detail.isHidden())
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertEqual(dialog.use_button.text(), "Use full dialogue")
        dialog.accept()
        self.assertEqual(len(dialog.imported_records), 118)
        self.assertEqual(dialog.imported_records[0]["trigger"], "Introduction")

    def test_failed_local_load_can_retry_without_leaving_stale_records_or_worker(self):
        self.read_template.side_effect = [OSError("Export file unavailable."), example_payload()]
        dialog = self.dialog()
        dialog.load_button.click()
        self.wait_until(lambda: dialog.worker is None)
        self.assertIn("Export file unavailable", dialog.status.text())
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertTrue(dialog.character.isEnabled())
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertEqual(dialog.examples, [])
        self.load(dialog)
        self.assertEqual(self.read_template.call_count, 2)
        self.assertNotIn("unavailable", dialog.status.text())
        self.assertEqual(dialog.records, self.records)

    def test_cancel_running_local_load_waits_for_worker_and_discards_late_result(self):
        started = threading.Event()

        def cooperative_load(template_id, project_file, cancelled):
            started.set()
            deadline = time.monotonic() + 2
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(0.002)
            return example_payload()

        self.read_template.side_effect = cooperative_load
        dialog = self.dialog()
        rejected = QSignalSpy(dialog.rejected)
        dialog.load_button.click()
        self.wait_until(started.is_set)
        dialog.reject()
        self.assertTrue(dialog.closing)
        self.assertFalse(dialog.buttons.isEnabled())
        self.wait_until(lambda: dialog.worker is None)
        self.assertEqual(rejected.count(), 1)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.examples, [])
        self.assertIsNone(dialog.imported_records)
        self.assertEqual(dialog.records, self.records)

    def test_capacity_counts_retained_rows_and_allows_matching_replacements_at_limit(self):
        records = deepcopy(self.records) + [
            {"id": f"line-{i}", "trigger": f"Tue{i}", "text": "An existing line."}
            for i in range(1999)
        ]
        dialog = self.dialog(records)
        dialog.receive_examples(example_payload())
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("2000", dialog.summary.text())
        dialog.accept()
        self.assertIsNone(dialog.imported_records)
        self.assertEqual(dialog.records, records)
        replacement = self.dialog(records)
        replacement.receive_examples({"examples": example_payload()["examples"][:1]})
        self.assertTrue(replacement.use_button.isEnabled())
        replacement.accept()
        self.assertEqual(len(replacement.imported_records), 2000)
        self.assertEqual(replacement.imported_records[0]["id"], records[0]["id"])
        self.assertEqual(replacement.imported_records[0]["text"], example_payload()["examples"][0]["text"])
        self.assertEqual(replacement.imported_records[1:], records[1:])

    def test_records_page_only_emits_changed_when_an_import_changes_records(self):
        page = self.keep(DialoguePage())
        page.set_project_file(self.project_file)
        page.load(self.records)
        changed = QSignalSpy(page.changed)
        self.visit_page_dialog(page, lambda dialog: dialog.reject())
        self.assertEqual(changed.count(), 0)
        unchanged = example_payload()
        unchanged["examples"] = unchanged["examples"][:1]
        unchanged["examples"][0]["text"] = self.records[0]["text"]
        self.visit_page_dialog(page, lambda dialog: dialog.accept(), unchanged)
        self.assertEqual(changed.count(), 0)
        self.assertEqual(page.dump(), self.records)
        self.visit_page_dialog(page, lambda dialog: dialog.accept())
        self.assertEqual(changed.count(), 1)
        self.assertEqual(page.dump()[0]["id"], self.records[0]["id"])
        self.assertEqual(page.current, 0)
        self.assertEqual(page.preview.text(), "Hello, Farmer.\nNice to meet you.")
        self.assertTrue(page.example_prompt.isHidden())

    def test_project_source_ignores_remembered_artwork_folder_and_has_no_folder_picker(self):
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        remembered = self.root / "old artwork exports"
        settings.setValue("localGame/contentPatcherExportFolder", str(remembered))
        settings.sync()
        with patch("pixelheart.game_import.QFileDialog.getExistingDirectory") as picker, patch("pixelheart.game_import.game_import_settings") as read_settings:
            dialog = self.dialog()
            self.assertIsInstance(dialog.game_source.folder, QLabel)
            self.assertEqual(dialog.game_source.findChildren(QLineEdit), [])
            self.assertFalse(hasattr(dialog.game_source, "browse_button"))
            self.assertEqual(dialog.game_source.folder.text(), str(self.root / "dialogue"))
            self.assertTrue((self.root / "dialogue").is_dir())
            with patch("pixelheart.game_import.QDesktopServices.openUrl", return_value=True) as open_url:
                dialog.game_source.open_button.click()
            open_url.assert_called_once()
            self.assertEqual(open_url.call_args.args[0].toLocalFile(), str(self.root / "dialogue"))
            picker.assert_not_called()
            read_settings.assert_not_called()
        self.assertEqual(settings.value("localGame/contentPatcherExportFolder"), str(remembered))
        self.assertEqual([button.text() for button in dialog.game_source.findChildren(QPushButton)],
                         ["Copy command", "Open dialogue folder"])

    def test_unsaved_dialog_disables_load_and_folder_open(self):
        dialog = self.keep(DialogueTemplateDialog(self.records))
        self.assertFalse(dialog.load_button.isEnabled())
        self.assertFalse(dialog.game_source.open_button.isEnabled())
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("Save", dialog.game_source.folder.text())
        dialog.load_examples()
        self.read_template.assert_not_called()
        self.assertIsNone(dialog.worker)
        self.assertFalse((self.root / "dialogue").exists())

    def test_dialogue_folder_is_created_on_save_open_and_project_switch(self):
        window = self.keep(MainWindow())
        first = self.root / "first NPC" / "character.json"
        self.assertTrue(window.save_to(first))
        self.assertTrue((first.parent / "dialogue").is_dir())
        self.assertEqual(window.dialogue.project_file, first)
        self.assertIn(str(first.parent / "dialogue"), window.dialogue.project_location.text())
        (first.parent / "dialogue").rmdir()
        reopened = self.keep(MainWindow())
        self.assertTrue(reopened.open_path(first))
        self.assertTrue((first.parent / "dialogue").is_dir())
        second = self.root / "second NPC" / "character.json"
        save_project(deepcopy(reopened.document), second)
        self.assertFalse((second.parent / "dialogue").exists())
        self.assertTrue(reopened.open_path(second))
        self.assertTrue((second.parent / "dialogue").is_dir())
        self.assertEqual(reopened.dialogue.project_file, second)
        self.assertIn(str(second.parent / "dialogue"), reopened.dialogue.project_location.text())
        self.assertNotIn(str(first.parent), reopened.dialogue.project_location.text())
        reopened.load_document(deepcopy(reopened.document))
        self.assertIsNone(reopened.dialogue.project_file)
        self.assertIn("Save", reopened.dialogue.project_location.text())

    def test_cancelled_initial_save_does_not_open_template_or_change_dialogue(self):
        window = self.keep(MainWindow())
        document = deepcopy(window.document)
        document["character"]["dialogues"] = deepcopy(self.records)
        window.load_document(document)
        original = deepcopy(window.document)
        changed = QSignalSpy(window.dialogue.changed)
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=("", "")) as save_dialog, patch.object(DialogueTemplateDialog, "exec") as browse:
            window.dialogue.open_examples()
        save_dialog.assert_called_once()
        browse.assert_not_called()
        self.read_template.assert_not_called()
        self.assertEqual(window.document, original)
        self.assertEqual(window.dialogue.dump(), self.records)
        self.assertIsNone(window.project_file)
        self.assertFalse(window.dirty)
        self.assertEqual(changed.count(), 0)

    def test_real_project_full_dialogue_import_and_save_preserve_source_without_folder_path(self):
        dialog = self.dialog([])
        exports = self.root / "dialogue"
        data = {row["trigger"]: row["text"] for row in full_dialogue_payload(301)["examples"]}
        (exports / "Characters_Dialogue_Abigail.json").write_text(json.dumps(data), encoding="utf-8")
        self.read_template.side_effect = load_project_dialogue
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Project import must not access the network")):
            self.load(dialog, 301)
            dialog.accept()
        imported = dialog.imported_records
        self.assertEqual({row["trigger"]: row["text"] for row in imported}, data)
        self.assertTrue(all(row["source"]["asset"] == "Characters/Dialogue/Abigail" for row in imported))
        self.assertNotIn(str(self.root), json.dumps(imported))
        window = self.keep(MainWindow())
        window.document["character"]["dialogues"] = deepcopy(imported)
        window.load_document(window.document)
        self.assertTrue(window.save_to(self.project_file))
        self.assertEqual(load_project(self.project_file)["character"]["dialogues"], imported)
        self.assertNotIn(str(self.root), self.project_file.read_text(encoding="utf-8"))

    def test_missing_project_template_does_not_fall_back_to_remembered_folder(self):
        dialog = self.dialog([])
        remembered = self.root / "patch export"
        remembered.mkdir()
        (remembered / "Characters_Dialogue_Abigail.json").write_text('{"Mon": "An unrelated project reference."}', encoding="utf-8")
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("localGame/contentPatcherExportFolder", str(remembered))
        settings.sync()
        self.read_template.side_effect = load_project_dialogue
        dialog.load_button.click()
        self.wait_until(lambda: dialog.worker is None)
        self.assertEqual(dialog.examples, [])
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("Characters_Dialogue_Abigail.json", dialog.status.text())
        self.assertIn("dialogue folder", dialog.status.text())
        self.assertNotIn("choose", dialog.status.text().lower())

    def test_main_window_import_dirties_once_and_survives_save_reopen_with_metadata(self):
        window = self.keep(MainWindow())
        document = deepcopy(window.document)
        document["character"]["dialogues"] = deepcopy(self.records) + [
            {"id": "custom-line", "trigger": "MyCustomKey", "text": "My unrelated writing."},
        ]
        window.load_document(document)
        self.assertTrue(window.save_to(self.project_file))
        changed = QSignalSpy(window.dialogue.changed)
        self.visit_page_dialog(window.dialogue, lambda dialog: dialog.reject())
        self.assertFalse(window.dirty)
        self.assertEqual(changed.count(), 0)
        full_payload = full_dialogue_payload(118)
        self.visit_page_dialog(window.dialogue, lambda dialog: dialog.accept(), full_payload)
        self.assertTrue(window.dirty)
        self.assertEqual(changed.count(), 1)
        imported = window.dialogue.dump()
        self.assertEqual(len(imported), 119)
        expected = {row["trigger"]: row["text"] for row in full_payload["examples"]}
        expected["MyCustomKey"] = "My unrelated writing."
        self.assertEqual({row["trigger"].strip(): row["text"] for row in imported}, expected)
        self.assertEqual(window.document["character"]["dialogues"], imported)
        self.assertEqual(imported[0]["extension"], self.records[0]["extension"])
        self.assertTrue(window.save_to(self.project_file))
        self.assertFalse(window.dirty)
        self.assertEqual(load_project(self.project_file)["character"]["dialogues"], imported)
        reopened = self.keep(MainWindow())
        self.assertTrue(reopened.open_path(self.project_file))
        self.assertEqual(reopened.dialogue.dump(), imported)
        self.assertFalse(reopened.dirty)


if __name__ == "__main__":
    unittest.main()
