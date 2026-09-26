"""Headless integration coverage for authoring, persistence, and export."""

import os

# Select the headless platform before importing any Qt modules. These tests
# exercise real widgets and signals without showing windows or native dialogs.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
import shutil
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.artwork_page import PreparationDialog
from pixelheart.theme import apply_theme
from pixelheart_core.artwork import inspect_artwork
from pixelheart_core.projects import ProjectError, load_project, resolve_artwork
from tests.qt_support import QtTestCase


class DesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-desktop-test-")
        self.root = Path(self.temporary.name)
        self.file = self.root / "first project" / "character.json"
        self.windows = []
        self.error_patch = patch.object(MainWindow, "show_error")
        self.errors = self.error_patch.start()
        self.window = self.make_window()

    def tearDown(self):
        for window in reversed(self.windows):
            window.dirty = False
            window.close()
            window.deleteLater()
        self.application.processEvents()
        self.error_patch.stop()
        self.temporary.cleanup()

    def make_window(self):
        window = MainWindow()
        self.windows.append(window)
        return window

    def make_png(self, name, dimensions, color=(160, 90, 140, 255)):
        source = self.root / name
        with Image.new("RGBA", dimensions, color) as image:
            image.save(source)
        return source

    def upload(self, kind, source):
        with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "PNG artwork (*.png)")):
            self.window.artwork.upload(kind)
        self.errors.assert_not_called()

    def valid_artwork(self):
        self.assertTrue(self.window.save_to(self.file))
        portrait = self.make_png("portrait.png", (128, 192))
        sprite = self.make_png("sprite.png", (64, 416))
        self.upload("portrait", portrait)
        self.upload("sprite", sprite)
        return portrait, sprite

    def test_edit_save_reopen_preserves_unicode_and_stable_entry_ids(self):
        window = self.window
        character_id = window.document["character"]["id"]
        dialogue_id = window.dialogue.records[0]["id"]
        schedule_id = window.schedule.records[0]["id"]
        window.identity.fields["name"].setText("은하 Étoile 🍑")
        window.identity.fields["internal_name"].setText("Eunha")
        window.identity.fields["bio"].setPlainText("달빛 아래 — a café by the river.\nA second chapter.")
        window.dialogue.fields["text"].setPlainText("안녕, @! Bienvenue.$h")
        window.schedule.table.cellWidget(0, 5).setText("강가에서 책 읽기")
        self.assertTrue(window.dirty)
        self.assertEqual(window.document["character"]["name"], "은하 Étoile 🍑")
        self.assertTrue(window.save_to(self.file))
        self.assertFalse(window.dirty)

        reopened = self.make_window()
        self.assertTrue(reopened.open_path(self.file))
        self.assertEqual(reopened.document["character"]["id"], character_id)
        self.assertEqual(reopened.dialogue.records[0]["id"], dialogue_id)
        self.assertEqual(reopened.schedule.records[0]["id"], schedule_id)
        self.assertEqual(reopened.identity.fields["name"].text(), "은하 Étoile 🍑")
        self.assertIn("달빛 아래", reopened.identity.fields["bio"].toPlainText())
        self.assertEqual(reopened.dialogue.fields["text"].toPlainText(), "안녕, @! Bienvenue.$h")
        self.assertEqual(reopened.schedule.table.cellWidget(0, 5).text(), "강가에서 책 읽기")
        self.assertFalse(reopened.dirty)
        self.errors.assert_not_called()

    def test_repeated_and_same_path_saves_keep_character_and_entry_ids(self):
        window = self.window
        expected = {
            "character": window.document["character"]["id"],
            "dialogue": window.dialogue.records[0]["id"],
            "schedule": window.schedule.records[0]["id"],
        }
        self.assertTrue(window.save_to(self.file))
        self.assertTrue(window.save())
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(self.file), "")), \
                patch("pixelheart.app.copy_project") as copy_project:
            self.assertTrue(window.save_as())
        copy_project.assert_not_called()
        character = load_project(self.file)["character"]
        self.assertEqual(character["id"], expected["character"])
        self.assertEqual(character["dialogues"][0]["id"], expected["dialogue"])
        self.assertEqual(character["schedule"][0]["id"], expected["schedule"])
        self.assertFalse(window.dirty)
        self.errors.assert_not_called()

    def test_extension_metadata_survives_edit_save_validation_and_export(self):
        window = self.window
        document = deepcopy(window.document)
        character = document["character"]
        gift_metadata = {"seasonal": True, "credit": "이름"}
        entry_metadata = {"creator": "Ada", "revision": 3}
        character["gifts"]["extension"] = gift_metadata
        character["dialogues"][0]["extension"] = entry_metadata
        character["schedule"][0]["extension"] = entry_metadata
        character["events"] = [{"id": "event-extension", "name": "A walk", "hearts": 2, "location": "Town", "description": "At dusk", "extension": entry_metadata}]
        character["relationships"] = [{"id": "relationship-extension", "name": "Leah", "relation": "Friend", "description": "Artists", "extension": entry_metadata}]
        document["workspace"] = {"custom": "retained"}
        window.load_document(document)
        window.identity.fields["name"].setText("Ada")
        window.dialogue.fields["text"].setPlainText("A new beginning.$h")
        window.schedule.table.cellWidget(0, 5).setText("Read at home")
        window.events.fields["description"].setPlainText("At sunset")
        window.relationships.fields["relation"].setText("Good friend")
        window.gifts.assign_items(["421", "66"], "love")
        self.valid_artwork()
        self.assertTrue(window.save())
        loaded = load_project(self.file)
        self.assertEqual(loaded["workspace"], {"custom": "retained"})
        self.assertEqual(loaded["character"]["gifts"]["extension"], gift_metadata)
        for collection in ("dialogues", "schedule", "events", "relationships"):
            self.assertEqual(loaded["character"][collection][0]["extension"], entry_metadata)
        blockers = [issue for issue in window.export_page.refresh() if issue["level"] == "error"]
        self.assertEqual(blockers, [])
        destination = self.root / "extension-test.zip"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                patch("pixelheart.app.QMessageBox.information"):
            self.assertTrue(window.export_project())
        with zipfile.ZipFile(destination) as archive:
            exported = json.loads(archive.read("[CP] NewCharacter/project.json"))["character"]
        self.assertEqual(exported["gifts"]["extension"], gift_metadata)
        for collection in ("dialogues", "schedule", "events", "relationships"):
            self.assertEqual(exported[collection][0]["extension"], entry_metadata)
        self.errors.assert_not_called()

    def test_dialogue_event_relationship_and_schedule_edits_reach_saved_project(self):
        window = self.window
        window.dialogue.add()
        window.dialogue.fields["text"].setPlainText("Mondays smell like fresh bread.$h")
        dialogue = window.dialogue.dump()[-1]
        self.assertEqual(dialogue["trigger"], "Mon")
        window.events.add()
        window.events.fields["name"].setText("A letter by the river")
        window.events.fields["hearts"].setValue(4)
        window.events.fields["description"].setPlainText("A promise for another spring.")
        event = window.events.dump()[0]
        window.relationships.add()
        window.relationships.fields["name"].setText("Leah")
        window.relationships.fields["relation"].setText("Painting partner")
        window.relationships.fields["description"].setPlainText("They share a sketchbook.")
        relationship = window.relationships.dump()[0]

        first_stop_id = window.schedule.dump()[0]["id"]
        window.schedule.add()
        window.schedule.table.cellWidget(1, 0).setText("12:00")
        window.schedule.table.cellWidget(1, 1).setText("Forest")
        window.schedule.table.cellWidget(1, 2).setValue(21)
        window.schedule.table.cellWidget(1, 3).setValue(32)
        window.schedule.table.cellWidget(1, 4).setCurrentIndex(3)
        second_stop_id = window.schedule.dump()[1]["id"]
        window.schedule.move(-1)
        self.assertEqual([record["id"] for record in window.schedule.dump()], [second_stop_id, first_stop_id])
        window.schedule.move(1)
        self.assertEqual([record["id"] for record in window.schedule.dump()], [first_stop_id, second_stop_id])
        self.assertTrue(window.save_to(self.file))
        character = load_project(self.file)["character"]
        self.assertEqual(character["dialogues"][-1], dialogue)
        self.assertEqual(character["events"][0], event)
        self.assertEqual(character["relationships"][0], relationship)
        self.assertEqual(character["schedule"][1]["location"], "Forest")
        self.assertEqual(character["schedule"][1]["x"], 21)
        self.assertEqual(character["schedule"][1]["facing"], "left")
        self.assertEqual(character["schedule"][1]["id"], second_stop_id)
        self.errors.assert_not_called()

    def test_save_as_copies_original_and_prepared_artwork_into_portable_project(self):
        window = self.window
        window.identity.fields["name"].setText("Mira")
        self.assertTrue(window.save_to(self.file))
        source = self.make_png("large portrait.png", (256, 384))
        original_bytes = source.read_bytes()
        self.upload("portrait", source)
        with patch.object(PreparationDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window.artwork.prepare("portrait")
        self.assertEqual(window.document["artwork"]["portrait"]["selected"], "prepared")
        self.assertTrue(window.save())
        previous_json = self.file.read_bytes()
        identity = window.document["character"]["id"]
        destination = self.root / "copied project" / "mira"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
            self.assertTrue(window.save_as())
        copy_file = destination.with_suffix(".json")
        self.assertEqual(window.project_file, copy_file)
        self.assertEqual(self.file.read_bytes(), previous_json)
        self.assertEqual(window.document["character"]["id"], identity)
        record = window.document["artwork"]["portrait"]
        self.assertEqual((copy_file.parent / record["original"]).read_bytes(), original_bytes)
        self.assertEqual(inspect_artwork(copy_file.parent / record["prepared"])["width"], 128)

        shutil.rmtree(self.file.parent)
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(copy_file))
        selected = resolve_artwork(reopened.document, copy_file, "portrait")
        self.assertEqual(inspect_artwork(selected)["height"], 192)
        self.assertEqual(reopened.document["character"]["id"], identity)
        self.errors.assert_not_called()

    def test_validation_reports_missing_artwork_and_romance_frame_requirements(self):
        window = self.window
        errors = [issue for issue in window.validate_project() if issue["level"] == "error"]
        self.assertEqual({issue["field"] for issue in errors}, {"portrait", "sprite"})
        self.assertTrue(window.save_to(self.file))
        self.upload("portrait", self.make_png("portrait.png", (128, 192)))
        self.upload("sprite", self.make_png("short sprite.png", (64, 128)))
        errors = [issue for issue in window.export_page.refresh() if issue["level"] == "error"]
        self.assertEqual({issue["field"] for issue in errors}, {"sprite"})
        self.assertIn("416", errors[0]["message"])
        self.assertFalse(window.export_page.export_button.isEnabled())
        window.identity.fields["romanceable"].setChecked(False)
        self.assertFalse([issue for issue in window.export_page.refresh() if issue["level"] == "error"])
        self.assertTrue(window.export_page.export_button.isEnabled())
        window.identity.fields["romanceable"].setChecked(True)
        self.upload("sprite", self.make_png("romance sprite.png", (64, 416)))
        self.assertFalse([issue for issue in window.export_page.refresh() if issue["level"] == "error"])
        self.assertTrue(window.export_page.export_button.isEnabled())

    def test_export_writes_valid_archive_with_current_edits_and_selected_png_bytes(self):
        original_portrait, original_sprite = self.valid_artwork()
        window = self.window
        window.identity.fields["name"].setText("미라")
        window.identity.fields["internal_name"].setText("Mira")
        window.dialogue.fields["text"].setPlainText("안녕, @.$h")
        destination = self.root / "Mira pack"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                patch("pixelheart.app.QMessageBox.information") as information:
            self.assertTrue(window.export_project())
        information.assert_called_once()
        with zipfile.ZipFile(destination.with_suffix(".zip")) as archive:
            prefix = "[CP] Mira/"
            self.assertEqual(archive.read(prefix + "assets/portraits.png"), original_portrait.read_bytes())
            self.assertEqual(archive.read(prefix + "assets/sprites.png"), original_sprite.read_bytes())
            dialogue = json.loads(archive.read(prefix + "assets/dialogue.json"))
            self.assertEqual(dialogue["Introduction"], "안녕, @.$h")
            project = json.loads(archive.read(prefix + "project.json"))
            self.assertEqual(project["character"]["name"], "미라")
            self.assertEqual(project["character"]["id"], window.document["character"]["id"])
            manifest = json.loads(archive.read(prefix + "manifest.json"))
            self.assertEqual(manifest["ContentPackFor"]["UniqueID"], "Pathoschild.ContentPatcher")
        self.errors.assert_not_called()

    def test_blocked_export_does_not_open_save_dialog(self):
        with patch("pixelheart.app.QFileDialog.getSaveFileName") as choose:
            self.assertFalse(self.window.export_project())
        choose.assert_not_called()
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["export"])

    def test_canceling_export_creates_no_archive(self):
        self.valid_artwork()
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=("", "")), \
                patch("pixelheart.app.QMessageBox.information") as information:
            self.assertFalse(self.window.export_project())
        information.assert_not_called()
        self.assertEqual(list(self.root.glob("*.zip")), [])

    def test_unsaved_cancel_keeps_character_and_edits(self):
        window = self.window
        identity = window.document["character"]["id"]
        window.identity.fields["name"].setText("Keep this draft")
        with patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Cancel):
            window.new_character()
        self.assertEqual(window.document["character"]["id"], identity)
        self.assertEqual(window.identity.fields["name"].text(), "Keep this draft")
        self.assertTrue(window.dirty)

    def test_unsaved_discard_starts_fresh_without_writing_the_old_draft(self):
        window = self.window
        identity = window.document["character"]["id"]
        window.identity.fields["name"].setText("Discard this draft")
        with patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Discard), \
                patch("pixelheart.app.QFileDialog.getSaveFileName") as choose:
            window.new_character()
        self.assertNotEqual(window.document["character"]["id"], identity)
        self.assertFalse(window.dirty)
        self.assertIsNone(window.project_file)
        choose.assert_not_called()

    def test_unsaved_save_writes_old_character_before_starting_fresh(self):
        window = self.window
        identity = window.document["character"]["id"]
        window.identity.fields["name"].setText("Save this one 먼저")
        with patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Save), \
                patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(self.file), "")):
            window.new_character()
        previous = load_project(self.file)["character"]
        self.assertEqual(previous["id"], identity)
        self.assertEqual(previous["name"], "Save this one 먼저")
        self.assertNotEqual(window.document["character"]["id"], identity)
        self.assertFalse(window.dirty)
        self.errors.assert_not_called()

    def test_canceling_save_from_unsaved_prompt_keeps_the_draft(self):
        window = self.window
        identity = window.document["character"]["id"]
        window.identity.fields["name"].setText("Still here")
        with patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Save), \
                patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=("", "")):
            window.new_character()
        self.assertEqual(window.document["character"]["id"], identity)
        self.assertTrue(window.dirty)
        self.assertFalse(self.file.exists())

    def test_failed_save_from_unsaved_prompt_keeps_draft_and_previous_file(self):
        window = self.window
        self.assertTrue(window.save_to(self.file))
        previous_bytes = self.file.read_bytes()
        identity = window.document["character"]["id"]
        window.identity.fields["name"].setText("A change worth keeping")
        with patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Save), \
                patch("pixelheart.app.save_project", side_effect=ProjectError("Disk is full")):
            window.new_character()
        self.assertEqual(window.document["character"]["id"], identity)
        self.assertEqual(window.identity.fields["name"].text(), "A change worth keeping")
        self.assertTrue(window.dirty)
        self.assertEqual(self.file.read_bytes(), previous_bytes)
        self.errors.assert_called_once_with("Could not save project", "Disk is full")

    def test_preparation_dialog_supports_pixelation_and_disables_incompatible_sheets(self):
        compatible = self.make_png("large portrait.png", (256, 384))
        incompatible = self.make_png("single portrait.png", (64, 64))
        original_bytes = compatible.read_bytes()
        for source, expected_enabled in ((compatible, True), (incompatible, False)):
            with self.subTest(source=source.name):
                dialog = PreparationDialog(source, "portrait", False, self.window)
                try:
                    self.assertEqual(dialog.accept_button.isEnabled(), expected_enabled)
                    if expected_enabled:
                        self.assertEqual(inspect_artwork(dialog.prepared)["width"], 128)
                        dialog.pixelation.setCurrentIndex(2)
                        self.assertTrue(dialog.accept_button.isEnabled())
                        self.assertEqual(dialog.pixelation.currentData(), 4)
                        self.assertFalse(dialog.output_preview.pixmap.isNull())
                    else:
                        self.assertIn("Upscaling", dialog.result.text())
                        self.assertTrue(dialog.output_preview.pixmap.isNull())
                        self.assertFalse(dialog.prepared.exists())
                finally:
                    dialog.temporary.cleanup()
                    dialog.close()
                    dialog.deleteLater()
        self.assertEqual(compatible.read_bytes(), original_bytes)

    def test_artwork_preparation_cancel_selection_and_removal_preserve_original(self):
        window = self.window
        self.assertTrue(window.save_to(self.file))
        source = self.make_png("large portrait.png", (256, 384))
        self.upload("portrait", source)
        original_record = dict(window.document["artwork"]["portrait"])
        original_path = window.project_file.parent / original_record["original"]
        with patch.object(PreparationDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            window.artwork.prepare("portrait")
        self.assertEqual(window.document["artwork"]["portrait"], original_record)
        with patch.object(PreparationDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window.artwork.prepare("portrait")
        record = window.document["artwork"]["portrait"]
        prepared_path = window.project_file.parent / record["prepared"]
        self.assertEqual(resolve_artwork(window.document, window.project_file, "portrait"), prepared_path)
        self.assertEqual(inspect_artwork(prepared_path)["width"], 128)
        chooser = window.artwork.cards["portrait"]["select"]
        chooser.setCurrentIndex(chooser.findData("original"))
        self.assertEqual(resolve_artwork(window.document, window.project_file, "portrait"), original_path)
        self.assertTrue(any(issue["field"] == "portrait" and issue["level"] == "error" for issue in window.validate_project()))
        chooser.setCurrentIndex(chooser.findData("prepared"))
        self.assertFalse(any(issue["field"] == "portrait" and issue["level"] == "error" for issue in window.validate_project()))
        window.artwork.remove("portrait")
        self.assertIsNone(window.document["artwork"]["portrait"])
        self.assertEqual(original_path.read_bytes(), source.read_bytes())
        self.assertTrue(prepared_path.exists())
        self.assertFalse(chooser.isEnabled())
        self.errors.assert_not_called()

    def test_invalid_artwork_upload_and_canceled_first_save_leave_document_unmodified(self):
        window = self.window
        source = self.root / "broken.png"
        source.write_bytes(b"broken PNG")
        with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
                patch("pixelheart.app.QFileDialog.getSaveFileName") as save_dialog:
            window.artwork.upload("portrait")
        save_dialog.assert_not_called()
        self.assertIsNone(window.document["artwork"]["portrait"])
        self.errors.assert_called_once()
        self.errors.reset_mock()
        source = self.make_png("valid portrait.png", (128, 192))
        with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "")), \
                patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=("", "")):
            window.artwork.upload("portrait")
        self.assertIsNone(window.project_file)
        self.assertIsNone(window.document["artwork"]["portrait"])
        self.assertFalse(window.dirty)
        self.errors.assert_not_called()

    def test_seasonal_artwork_prepares_saves_copies_exports_and_falls_back(self):
        default_portrait, default_sprite = self.valid_artwork()
        window = self.window
        self.assertTrue(window.save_to(self.file))
        window.artwork.select_appearance("winter")
        self.assertFalse(window.dirty)
        self.assertIn("Using Default", window.artwork.cards["portrait"]["info"].text())
        self.assertFalse(window.artwork.cards["portrait"]["remove"].isEnabled())
        winter_source = self.make_png("winter.png", (256, 384), (50, 90, 180, 255))
        self.upload("portrait", winter_source)
        self.assertEqual(resolve_artwork(window.document, window.project_file, "portrait").read_bytes(), default_portrait.read_bytes())
        with patch.object(PreparationDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            window.artwork.prepare("portrait")
        record = window.document["artwork"]["variants"]["winter"]["portrait"]
        self.assertEqual(record["selected"], "prepared")
        prepared_bytes = resolve_artwork(window.document, window.project_file, "portrait", variant="winter").read_bytes()
        self.assertIsNone(resolve_artwork(window.document, window.project_file, "sprite", variant="winter"))
        self.assertIn("Using Default", window.artwork.cards["sprite"]["info"].text())
        destination = self.root / "second project" / "character.json"
        self.assertTrue(window.save_to(destination))
        shutil.rmtree(self.file.parent)
        reopened = self.make_window()
        self.assertTrue(reopened.open_path(destination))
        reopened.artwork.select_appearance("winter")
        self.assertFalse(reopened.dirty)
        self.assertEqual(resolve_artwork(reopened.document, destination, "portrait", variant="winter").read_bytes(), prepared_bytes)
        self.assertFalse([issue for issue in reopened.validate_project() if issue["level"] == "error"])
        archive_file = self.root / "seasonal.zip"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(archive_file), "")), \
                patch("pixelheart.app.QMessageBox.information"):
            self.assertTrue(reopened.export_project())
        with zipfile.ZipFile(archive_file) as archive:
            prefix = "[CP] NewCharacter/"
            self.assertEqual(archive.read(prefix + "assets/portraits.png"), default_portrait.read_bytes())
            self.assertEqual(archive.read(prefix + "assets/sprites.png"), default_sprite.read_bytes())
            self.assertEqual(archive.read(prefix + "assets/appearances/winter/portraits.png"), prepared_bytes)
        reopened.artwork.remove("portrait")
        self.assertIsNone(resolve_artwork(reopened.document, destination, "portrait", variant="winter"))
        self.assertIn("Using Default", reopened.artwork.cards["portrait"]["info"].text())
        self.assertEqual(resolve_artwork(reopened.document, destination, "portrait").read_bytes(), default_portrait.read_bytes())
        self.errors.assert_not_called()

    def test_appearance_validation_opens_the_affected_set(self):
        self.valid_artwork()
        window = self.window
        window.artwork.select_appearance("summer")
        self.upload("portrait", self.make_png("incomplete.png", (64, 64)))
        window.artwork.select_appearance()
        issues = window.export_page.refresh()
        errors = [issue for issue in issues if issue["level"] == "error"]
        self.assertTrue(any(issue["field"] == "appearances.summer.portrait" for issue in errors))
        self.assertFalse(window.export_page.export_button.isEnabled())
        for index in range(window.export_page.list.count()):
            item = window.export_page.list.item(index)
            if "Appearances.summer.portrait" in item.text():
                window.export_page.open_issue(item)
                break
        self.assertEqual(window.navigation.currentRow(), 5)
        self.assertEqual(window.artwork.variant, "summer")

    def test_appearance_path_errors_keep_the_correct_editor_destination(self):
        self.valid_artwork()
        window = self.window
        window.artwork.select_appearance("winter")
        sprite = self.make_png("winter-sprite.png", (64, 416), (40, 70, 200, 255))
        self.upload("sprite", sprite)
        imported = resolve_artwork(window.document, window.project_file, "sprite", variant="winter")
        imported.unlink()
        imported.symlink_to(sprite)
        window.artwork.select_appearance()
        issues = window.export_page.refresh()
        self.assertFalse(any(issue["level"] == "success" for issue in issues))
        errors = [issue for issue in issues if issue["level"] == "error"]
        self.assertEqual([issue["field"] for issue in errors], ["appearances.winter.sprite"])
        for index in range(window.export_page.list.count()):
            item = window.export_page.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)["field"] == "appearances.winter.sprite":
                window.export_page.open_issue(item)
                break
        self.assertEqual(window.navigation.currentRow(), 5)
        self.assertEqual(window.artwork.variant, "winter")
        self.assertIn("symlink", window.artwork.cards["sprite"]["info"].text())


if __name__ == "__main__":
    unittest.main()
