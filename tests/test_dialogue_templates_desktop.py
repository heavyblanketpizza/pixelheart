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
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

from pixelheart.app import MainWindow
from pixelheart.dialogue_templates import DialogueTemplateDialog
from pixelheart.editors import DialoguePage
from pixelheart_core.projects import load_project
from pixelheart_core.local_templates import load_local_dialogue


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


class DialogueTemplateDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-dialogue-examples-")
        self.root = Path(self.temporary.name)
        self.widgets = []
        self.cache_patch = patch("pixelheart.game_import.game_import_settings", side_effect=lambda: QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat))
        self.cache_patch.start()
        self.download_patch = patch("pixelheart.dialogue_templates.load_local_dialogue", return_value=example_payload())
        self.download = self.download_patch.start()
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
        self.download_patch.stop()
        self.cache_patch.stop()
        self.temporary.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def dialog(self, records=None):
        return self.keep(DialogueTemplateDialog(self.records if records is None else records))

    def wait_until(self, predicate, message="Qt work did not finish"):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), message)

    def load(self, dialog, count=4):
        dialog.load_button.click()
        self.wait_until(lambda: dialog.worker is None)
        self.assertEqual(len(dialog.examples), count)

    @staticmethod
    def check(dialog, index, checked=True):
        dialog.list.item(index).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    @staticmethod
    def choose(dialog, index, decision):
        dialog.list.setCurrentRow(index)
        dialog.conflict_choice.setCurrentIndex(dialog.conflict_choice.findData(decision))

    @staticmethod
    def select_only(dialog, *indices):
        dialog.select_none_button.click()
        for index in indices:
            dialog.list.item(index).setCheckState(Qt.CheckState.Checked)

    @staticmethod
    def choose_bulk(dialog, decision):
        dialog.bulk_conflict_choice.setCurrentIndex(dialog.bulk_conflict_choice.findData(decision))

    def visit_page_dialog(self, page, action, payload=None):
        # Exercise the real browser and page commit boundary without a nested,
        # user-blocking modal loop. Async downloads are tested separately below.
        def interact(dialog):
            dialog.receive_examples(payload or example_payload())
            action(dialog)
            return dialog.result()
        with patch.object(DialogueTemplateDialog, "exec", interact):
            page.open_examples()

    def test_open_and_browse_are_read_only_and_never_fetch_until_requested(self):
        original = deepcopy(self.records)
        dialog = self.dialog()
        self.download.assert_not_called()
        self.assertEqual(dialog.examples, [])
        self.assertFalse(dialog.use_button.isEnabled())
        self.load(dialog)
        self.download.assert_called_once()
        self.assertEqual(dialog.preview.text(), "Hello, Farmer.\nNice to meet you.")
        self.assertEqual(dialog.raw_text.toPlainText(), example_payload()["examples"][0]["text"])
        self.assertTrue(all(dialog.list.item(i).checkState() == Qt.CheckState.Checked for i in range(4)))
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

    def test_selected_conflict_requires_choice_and_unselected_conflict_does_not_block(self):
        dialog = self.dialog()
        dialog.receive_examples(example_payload())
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("Introduction", dialog.summary.text())
        dialog.accept()
        self.assertIsNone(dialog.imported_records)
        self.select_only(dialog, 2)
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertEqual(dialog.use_button.text(), "Use selected dialogue")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.imported_records[0], self.records[0])
        self.assertEqual([row["trigger"] for row in dialog.imported_records], [" Introduction ", "summer_Mon"])
        self.assertEqual(set(dialog.imported_records[1]), {"id", "trigger", "text"})

    def test_explicit_keep_and_replace_preserve_entry_identity_and_unknown_metadata(self):
        for decision in ("keep", "replace"):
            with self.subTest(decision=decision):
                dialog = self.dialog()
                dialog.receive_examples(example_payload())
                self.select_only(dialog, 0, 1)
                self.choose(dialog, 0, decision)
                self.assertTrue(dialog.use_button.isEnabled())
                dialog.accept()
                imported = dialog.imported_records
                self.assertEqual(len(imported), 2)
                expected = deepcopy(self.records[0])
                if decision == "replace":
                    expected["text"] = example_payload()["examples"][0]["text"]
                self.assertEqual(imported[0], expected)
                self.assertNotEqual(imported[1]["id"], self.records[0]["id"])
                self.assertEqual(imported[1]["trigger"], "Mon")
        self.assertEqual(self.records[0]["text"], "My own introduction.")

    def test_duplicate_existing_trigger_is_preserved_and_cannot_be_replaced(self):
        records = self.records + [{"id": "second-intro", "trigger": "Introduction", "text": "Second draft."}]
        dialog = self.dialog(records)
        dialog.receive_examples(example_payload())
        self.select_only(dialog, 0)
        self.assertEqual(dialog.conflict_choice.findData("replace"), -1)
        self.assertIn("more than once", dialog.conflict_help.text())
        dialog.accept()
        self.assertEqual(dialog.imported_records, records)

    def test_switching_character_clears_loaded_examples_selections_and_decisions(self):
        dialog = self.dialog()
        self.load(dialog)
        self.choose_bulk(dialog, "replace")
        dialog.search.setText("Monday")
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.character.setCurrentIndex(1)
        self.assertEqual(dialog.examples, [])
        self.assertEqual(dialog.list.count(), 0)
        self.assertEqual(dialog.decisions, {})
        self.assertEqual(dialog.conflicts, {})
        self.assertEqual(dialog.search.text(), "")
        self.assertEqual(dialog.bulk_conflict_choice.currentData(), "")
        self.assertIsNone(dialog.imported_records)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertTrue(dialog.detail.isHidden())
        self.assertEqual(self.download.call_count, 1)
        self.load(dialog)
        self.assertEqual(self.download.call_args.args[0], dialog.character.currentData())
        self.assertEqual(dialog.selected_examples(), example_payload()["examples"])
        self.assertFalse(dialog.use_button.isEnabled())

    def test_full_exports_are_checked_by_default_and_import_every_raw_entry(self):
        for character_index, count in enumerate((318, 274)):
            with self.subTest(count=count):
                payload = full_dialogue_payload(count)
                self.download.return_value = payload
                dialog = self.dialog([])
                dialog.character.setCurrentIndex(character_index)
                self.load(dialog, count)
                self.assertEqual(dialog.list.count(), count)
                self.assertEqual(dialog.selected_examples(), payload["examples"])
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

    def test_search_hides_rows_without_deselecting_and_select_all_none_cover_whole_archive(self):
        payload = full_dialogue_payload(118)
        dialog = self.dialog([])
        dialog.receive_examples(payload)
        dialog.search.setText("Archive_Key_020")
        visible = [i for i in range(dialog.list.count()) if not dialog.list.item(i).isHidden()]
        self.assertEqual(len(visible), 1)
        self.assertEqual(dialog.examples[visible[0]]["trigger"], "Archive_Key_020")
        self.assertEqual(len(dialog.selected_examples()), 118)
        self.assertEqual(dialog.use_button.text(), "Use full dialogue")
        dialog.select_none_button.click()
        self.assertEqual(dialog.selected_examples(), [])
        self.assertFalse(dialog.use_button.isEnabled())
        self.check(dialog, visible[0])
        self.assertEqual(len(dialog.selected_examples()), 1)
        self.assertEqual(dialog.use_button.text(), "Use selected dialogue")
        dialog.select_all_button.click()
        self.assertEqual(len(dialog.selected_examples()), 118)
        dialog.accept()
        self.assertEqual(len(dialog.imported_records), 118)
        self.assertEqual(dialog.imported_records[0]["trigger"], "Introduction")

    def test_bulk_keep_or_replace_retains_other_writing_and_duplicate_conflicts(self):
        payload = full_dialogue_payload(74)
        records = deepcopy(self.records) + [
            {"id": "my-monday", "trigger": "Mon", "text": "My Monday.", "extension": {"reviewed": True}},
            {"id": "my-two-hearts-a", "trigger": "Mon2", "text": "First friendship draft."},
            {"id": "my-two-hearts-b", "trigger": "Mon2", "text": "Second friendship draft."},
            {"id": "my-custom-line", "trigger": "CustomTrigger", "text": "Keep this unrelated writing."},
        ]
        for decision in ("keep", "replace"):
            with self.subTest(decision=decision):
                dialog = self.dialog(records)
                dialog.receive_examples(payload)
                self.assertFalse(dialog.use_button.isEnabled())
                self.choose_bulk(dialog, decision)
                self.assertEqual(dialog.decisions["Introduction"], decision)
                self.assertEqual(dialog.decisions["Mon"], decision)
                self.assertEqual(dialog.decisions["Mon2"], "keep")
                self.assertTrue(dialog.use_button.isEnabled())
                dialog.accept()
                imported = dialog.imported_records
                self.assertEqual(len(imported), 76)
                for index in (0, 1):
                    expected = deepcopy(records[index])
                    if decision == "replace":
                        expected["text"] = payload["examples"][index]["text"]
                    self.assertEqual(imported[index], expected)
                self.assertEqual(imported[2:5], records[2:5])

    def test_bulk_choice_only_resolves_selected_conflicts(self):
        records = deepcopy(self.records) + [{"id": "my-monday", "trigger": "Mon", "text": "Mine."}]
        dialog = self.dialog(records)
        dialog.receive_examples(example_payload())
        self.select_only(dialog, 0, 2)
        self.choose_bulk(dialog, "keep")
        self.assertEqual(dialog.decisions["Introduction"], "keep")
        self.assertNotIn("Mon", dialog.decisions)
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.accept()
        self.assertEqual(dialog.imported_records[:2], records)
        self.assertEqual(dialog.imported_records[2]["trigger"], "summer_Mon")

    def test_failed_local_load_can_retry_without_leaving_stale_records_or_worker(self):
        self.download.side_effect = [OSError("Export file unavailable."), example_payload()]
        dialog = self.dialog()
        dialog.load_button.click()
        self.wait_until(lambda: dialog.worker is None)
        self.assertIn("Export file unavailable", dialog.status.text())
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertTrue(dialog.character.isEnabled())
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertEqual(dialog.examples, [])
        self.load(dialog)
        self.assertEqual(self.download.call_count, 2)
        self.assertNotIn("unavailable", dialog.status.text())
        self.assertEqual(dialog.records, self.records)

    def test_cancel_running_local_load_waits_for_worker_and_discards_late_result(self):
        started = threading.Event()

        def cooperative_download(template_id, cache_root, cancelled):
            started.set()
            deadline = time.monotonic() + 2
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(0.002)
            return example_payload()

        self.download.side_effect = cooperative_download
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

    def test_capacity_blocks_whole_selection_and_still_allows_replacement(self):
        records = deepcopy(self.records) + [
            {"id": f"line-{i}", "trigger": f"Tue{i}", "text": "An existing line."}
            for i in range(1999)
        ]
        dialog = self.dialog(records)
        dialog.receive_examples(example_payload())
        self.select_only(dialog, 0, 1)
        self.choose(dialog, 0, "replace")
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("2000", dialog.summary.text())
        dialog.accept()
        self.assertIsNone(dialog.imported_records)
        self.assertEqual(dialog.records, records)
        self.check(dialog, 1, False)
        self.assertTrue(dialog.use_button.isEnabled())
        dialog.accept()
        self.assertEqual(len(dialog.imported_records), 2000)
        self.assertEqual(dialog.imported_records[0]["id"], records[0]["id"])
        self.assertEqual(dialog.imported_records[0]["text"], example_payload()["examples"][0]["text"])

    def test_records_page_only_emits_changed_when_an_import_changes_records(self):
        page = self.keep(DialoguePage())
        page.load(self.records)
        changed = QSignalSpy(page.changed)
        self.visit_page_dialog(page, lambda dialog: dialog.reject())
        self.assertEqual(changed.count(), 0)

        def keep_intro(dialog):
            self.select_only(dialog, 0)
            self.choose(dialog, 0, "keep")
            dialog.accept()

        self.visit_page_dialog(page, keep_intro)
        self.assertEqual(changed.count(), 0)
        self.assertEqual(page.dump(), self.records)

        def import_monday(dialog):
            self.select_only(dialog, 1)
            dialog.accept()

        self.visit_page_dialog(page, import_monday)
        self.assertEqual(changed.count(), 1)
        self.assertEqual(page.dump()[0], self.records[0])
        self.assertEqual(page.current, 1)
        self.assertEqual(page.fields["trigger"].text(), "Mon")
        self.assertEqual(page.preview.text(), "Another Monday.")
        self.assertTrue(page.example_prompt.isHidden())

    def test_changing_local_folder_discards_loaded_data_and_conflict_choices(self):
        dialog = self.dialog()
        self.load(dialog)
        self.choose_bulk(dialog, "replace")
        dialog.game_source.folder.setText(str(self.root / "another export"))
        self.assertEqual(dialog.examples, [])
        self.assertEqual(dialog.decisions, {})
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn('Characters/Dialogue/Abigail', dialog.game_source.commands.toPlainText())
        dialog.character.setCurrentIndex(1)
        self.assertIn('Characters/Dialogue/Elliott', dialog.game_source.commands.toPlainText())

    def test_real_local_full_dialogue_import_and_save_preserve_source_without_folder_path(self):
        exports = self.root / "patch export"
        exports.mkdir()
        data = {row["trigger"]: row["text"] for row in full_dialogue_payload(301)["examples"]}
        (exports / "Characters_Dialogue_Abigail.json").write_text(json.dumps(data), encoding="utf-8")
        self.download.side_effect = load_local_dialogue
        dialog = self.dialog([])
        dialog.game_source.folder.setText(str(exports))
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Local import must not access the network")):
            self.load(dialog, 301)
            dialog.accept()
        imported = dialog.imported_records
        self.assertEqual({row["trigger"]: row["text"] for row in imported}, data)
        self.assertTrue(all(row["source"]["asset"] == "Characters/Dialogue/Abigail" for row in imported))
        self.assertNotIn(str(self.root), json.dumps(imported))
        window = self.keep(MainWindow())
        window.document["character"]["dialogues"] = deepcopy(imported)
        window.load_document(window.document)
        path = self.root / "local-template-project.json"
        self.assertTrue(window.save_to(path))
        self.assertEqual(load_project(path)["character"]["dialogues"], imported)
        self.assertNotIn(str(self.root), path.read_text(encoding="utf-8"))

    def test_main_window_import_dirties_once_and_survives_save_reopen_with_metadata(self):
        window = self.keep(MainWindow())
        document = deepcopy(window.document)
        document["character"]["dialogues"] = deepcopy(self.records)
        window.load_document(document)
        changed = QSignalSpy(window.dialogue.changed)
        self.visit_page_dialog(window.dialogue, lambda dialog: dialog.reject())
        self.assertFalse(window.dirty)

        def preserve_intro(dialog):
            self.select_only(dialog, 0)
            self.choose(dialog, 0, "keep")
            dialog.accept()

        self.visit_page_dialog(window.dialogue, preserve_intro)
        self.assertFalse(window.dirty)
        self.assertEqual(changed.count(), 0)

        full_payload = full_dialogue_payload(118)

        def import_all(dialog):
            self.choose_bulk(dialog, "replace")
            self.assertEqual(dialog.use_button.text(), "Use full dialogue")
            dialog.accept()

        self.visit_page_dialog(window.dialogue, import_all, full_payload)
        self.assertTrue(window.dirty)
        self.assertEqual(changed.count(), 1)
        imported = window.dialogue.dump()
        self.assertEqual(len(imported), 118)
        self.assertEqual({row["trigger"].strip(): row["text"] for row in imported},
                         {row["trigger"]: row["text"] for row in full_payload["examples"]})
        self.assertEqual(window.document["character"]["dialogues"], imported)
        self.assertEqual(imported[0]["extension"], self.records[0]["extension"])
        path = self.root / "with-examples.json"
        self.assertTrue(window.save_to(path))
        self.assertFalse(window.dirty)
        self.assertEqual(load_project(path)["character"]["dialogues"], imported)
        reopened = self.keep(MainWindow())
        self.assertTrue(reopened.open_path(path))
        self.assertEqual(reopened.dialogue.dump(), imported)
        self.assertFalse(reopened.dirty)


if __name__ == "__main__":
    unittest.main()
