"""Headless coverage for catalog import, gift assignment, and portable projects."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import io
import json
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow
from pixelheart.gifts_page import EXPORT_COMMAND, GIFT_MIME, GameImportDialog, GiftsPage
from pixelheart_core.catalog import load_catalog
from pixelheart_core.exporting import build_mod_archive, validate_character
from pixelheart_core.projects import load_project
from pixelheart_core.validation import validate_draft
from tests.qt_support import QtTestCase


MOD_ITEM = "Example.Orchard_Moonfruit"


class GiftDrop:
    """The event methods used by GiftList, with a real Qt MIME payload."""

    def __init__(self, source, values=None, *, payload=None):
        self.origin = source
        self.mime = QMimeData()
        self.mime.setData(GIFT_MIME, payload if payload is not None else json.dumps(values).encode("utf-8"))
        self.accepted = False
        self.action = None

    def source(self):
        return self.origin

    def mimeData(self):
        return self.mime

    def acceptProposedAction(self):
        self.accepted = True

    def setDropAction(self, action):
        self.action = action

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


class GiftEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-gifts-test-")
        self.root = Path(self.temporary.name)
        self.widgets = []
        self.page = self.keep(GiftsPage())
        self.export_file = self.write_objects(self.root / "Data_Objects.json")
        self.snapshot = load_catalog(self.export_file)

    def tearDown(self):
        for widget in reversed(self.widgets):
            if isinstance(widget, MainWindow):
                widget.dirty = False
            widget.close()
            widget.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def write_objects(self, path, data=None):
        if data is None:
            data = {
                "66": {"Name": "Amethyst", "DisplayName": "[LocalizedText Strings\\Objects:Amethyst_Name]", "Category": -2, "CanBeGivenAsGift": True},
                "395": {"Name": "Coffee", "DisplayName": "Coffee", "Category": -7},
                MOD_ITEM: {"Name": "Moonfruit", "DisplayName": "Moonfruit 🌙", "Category": -79, "CanBeGivenAsGift": True},
                "Example.Orchard_Quest": {"Name": "Quest parcel", "Category": 0, "CanBeGivenAsGift": False},
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def use_import(self):
        self.page.load_catalog_snapshot(self.snapshot)

    def list_ids(self, widget):
        return [self.page.key(widget.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(widget.count())]

    def select(self, widget, item_id):
        widget.clearSelection()
        for row in range(widget.count()):
            item = widget.item(row)
            if self.page.key(item.data(Qt.ItemDataRole.UserRole)) == item_id:
                item.setSelected(True)
                return
        self.fail(f"Item {item_id!r} is absent from the list")

    def test_default_catalog_is_available_without_a_local_game(self):
        self.assertIsNone(self.page.catalog_snapshot())
        self.assertIn("66", self.list_ids(self.page.library))
        self.assertIn("395", self.list_ids(self.page.library))
        self.assertGreater(self.page.library.count(), 100)

    def test_vanilla_snapshot_without_import_timestamp_loads(self):
        snapshot = deepcopy(self.page.base_catalog)
        snapshot.pop("imported_at", None)
        self.assertEqual(snapshot["source"], "vanilla")
        self.page.load_catalog_snapshot(snapshot)
        self.assertEqual(self.page.catalog_snapshot(), snapshot)
        self.assertEqual(set(self.list_ids(self.page.library)), {item["id"] for item in snapshot["items"]})
        self.assertNotIn("\nImported:", self.page.source_label.toolTip())

    def test_dialog_imports_modded_objects_and_filters_explicit_non_gifts(self):
        dialog = self.keep(GameImportDialog())
        self.assertTrue(dialog.import_path(self.export_file))
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        records = {item["id"]: item for item in dialog.catalog["items"]}
        self.assertEqual(set(records), {"66", "395", MOD_ITEM})
        self.assertEqual(records["66"]["name"], "Amethyst")
        self.assertEqual(records[MOD_ITEM]["name"], "Moonfruit 🌙")
        self.assertIn("imported_at", dialog.catalog)
        self.assertEqual(dialog.catalog["source"], "content-patcher-export")

    def test_export_file_picker_imports_the_selected_snapshot(self):
        dialog = self.keep(GameImportDialog())
        with patch("pixelheart.gifts_page.QFileDialog.getOpenFileName", return_value=(str(self.export_file), "")):
            dialog.choose_export()
        self.assertIn(MOD_ITEM, {item["id"] for item in dialog.catalog["items"]})

    def test_game_folder_picker_finds_desktop_mac_and_export_directories(self):
        for index, relative in enumerate((
            "patch export/Data_Objects.json",
            "Contents/MacOS/patch export/Data_Objects.json",
            "Stardew Valley.app/Contents/MacOS/patch export/Data_Objects.json",
            "Data_Objects.json",
        )):
            with self.subTest(relative=relative):
                game = self.root / f"game-{index}"
                self.write_objects(game / relative)
                dialog = self.keep(GameImportDialog())
                with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(game)):
                    dialog.choose_game_folder()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertIn(MOD_ITEM, {item["id"] for item in dialog.catalog["items"]})

    def test_ambiguous_or_missing_game_exports_require_an_explicit_file(self):
        for ambiguous in (False, True):
            with self.subTest(ambiguous=ambiguous):
                game = self.root / ("ambiguous-game" if ambiguous else "empty-game")
                game.mkdir()
                if ambiguous:
                    self.write_objects(game / "Data_Objects.json")
                    self.write_objects(game / "patch export" / "Data_Objects.json")
                dialog = self.keep(GameImportDialog())
                with patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=str(game)):
                    dialog.choose_game_folder()
                self.assertIsNone(dialog.catalog)
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertTrue(dialog.status.text())

    def test_cancelled_file_and_folder_pickers_do_not_import(self):
        dialog = self.keep(GameImportDialog())
        with patch("pixelheart.gifts_page.QFileDialog.getOpenFileName", return_value=("", "")), \
                patch("pixelheart.gifts_page.QFileDialog.getExistingDirectory", return_value=""):
            dialog.choose_export()
            dialog.choose_game_folder()
        self.assertIsNone(dialog.catalog)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_invalid_import_keeps_previous_catalog_and_shows_an_error(self):
        dialog = self.keep(GameImportDialog())
        dialog.catalog = deepcopy(self.snapshot)
        for index, content in enumerate(("{broken", "[]", '{"invalid": {"Name": "Incomplete record"}}')):
            with self.subTest(content=content):
                invalid = self.root / f"bad-{index}.json"
                invalid.write_text(content, encoding="utf-8")
                self.assertFalse(dialog.import_path(invalid))
                self.assertEqual(dialog.catalog, self.snapshot)
                self.assertTrue(dialog.status.text())

    def test_copy_command_uses_the_supported_content_patcher_export(self):
        dialog = self.keep(GameImportDialog())
        dialog.copy_command()
        self.assertEqual(EXPORT_COMMAND, "patch export Data/Objects")
        self.assertEqual(self.application.clipboard().text(), EXPORT_COMMAND)

    def test_accepting_game_import_replaces_catalog_and_marks_editor_changed(self):
        dialog = GameImportDialog(self.page)
        self.assertTrue(dialog.import_path(self.export_file))
        changes = QSignalSpy(self.page.changed)
        with patch("pixelheart.gifts_page.GameImportDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.page.import_game()
        self.assertEqual(self.page.catalog_snapshot(), dialog.catalog)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(set(self.list_ids(self.page.library)), {"66", "395", MOD_ITEM})

    def test_cancelling_game_import_preserves_catalog_assignments_and_clean_state(self):
        self.use_import()
        self.page.assign_items([MOD_ITEM], "love")
        before = self.page.dump()
        changes = QSignalSpy(self.page.changed)
        dialog = GameImportDialog(self.page)
        dialog.catalog = deepcopy(self.page.base_catalog)
        with patch("pixelheart.gifts_page.GameImportDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.page.import_game()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(self.page.catalog_snapshot(), self.snapshot)
        self.assertEqual(changes.count(), 0)

    def test_search_category_and_unassigned_filters_work_together(self):
        self.use_import()
        self.page.search.setText("MOON example.orchard")
        self.assertEqual(self.list_ids(self.page.library), [MOD_ITEM])
        self.page.search.clear()
        self.page.filter.setCurrentIndex(self.page.filter.findText("Cooking"))
        self.assertEqual(self.list_ids(self.page.library), ["395"])
        self.page.filter.setCurrentIndex(0)
        self.page.assign_items([MOD_ITEM], "love")
        self.page.unassigned_only.setChecked(True)
        self.assertEqual(set(self.list_ids(self.page.library)), {"66", "395"})
        self.page.assign_items([MOD_ITEM], None)
        self.assertIn(MOD_ITEM, self.list_ids(self.page.library))

    def test_id_aliases_move_exclusively_between_tastes(self):
        self.use_import()
        self.assertTrue(self.page.assign_items(["66", "(O)66", MOD_ITEM], "love"))
        self.assertEqual(self.page.dump()["love"], ["(O)66", "(O)" + MOD_ITEM])
        self.assertTrue(self.page.assign_items(["Amethyst"], "hate"))
        self.assertEqual(self.page.dump()["love"], ["(O)" + MOD_ITEM])
        self.assertEqual(self.page.dump()["hate"], ["(O)66"])
        self.assertTrue(self.page.assign_items(["id:66"], None))
        self.assertEqual(self.page.dump()["hate"], [])

    def test_unknown_item_or_category_cannot_partially_change_assignments(self):
        self.use_import()
        self.page.assign_items(["66"], "love")
        before = self.page.dump()
        for values, taste in ((["395", "Missing.Mod_Item"], "like"), (["66"], "unexpected")):
            self.assertFalse(self.page.assign_items(values, taste))
            self.assertEqual(self.page.dump(), before)

    def test_drag_between_tastes_and_back_to_catalog_moves_assignments(self):
        self.use_import()
        to_love = GiftDrop(self.page.library, ["(O)" + MOD_ITEM])
        self.page.lists["love"].dropEvent(to_love)
        self.assertTrue(to_love.accepted)
        self.assertEqual(to_love.action, Qt.DropAction.MoveAction)
        to_like = GiftDrop(self.page.lists["love"], ["(O)" + MOD_ITEM])
        self.page.lists["like"].dropEvent(to_like)
        self.assertTrue(to_like.accepted)
        self.assertEqual(self.page.dump()["love"], [])
        self.assertEqual(self.page.dump()["like"], ["(O)" + MOD_ITEM])
        to_default = GiftDrop(self.page.lists["like"], ["(O)" + MOD_ITEM])
        self.page.library.dropEvent(to_default)
        self.assertTrue(to_default.accepted)
        self.assertEqual(self.page.dump()["like"], [])

    def test_drag_rejects_other_editors_forged_ids_and_malformed_payloads(self):
        self.use_import()
        other = self.keep(GiftsPage())
        for event in (
            GiftDrop(other.library, ["(O)66"]),
            GiftDrop(None, ["(O)66"]),
            GiftDrop(self.page.library, ["(O)Missing.Mod_Item"]),
            GiftDrop(self.page.lists["hate"], ["(O)66"]),
            GiftDrop(self.page.library, payload=b"{bad"),
            GiftDrop(self.page.library, payload=b'{"id": "66"}'),
        ):
            with self.subTest(source=event.origin, payload=bytes(event.mime.data(GIFT_MIME))):
                self.page.lists["love"].dropEvent(event)
                self.assertFalse(event.accepted)
                self.assertEqual(self.page.dump()["love"], [])

    def test_keyboard_delete_and_backspace_return_selected_items_to_default(self):
        self.use_import()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            with self.subTest(key=key):
                self.page.assign_items(["66", MOD_ITEM], "love")
                self.select(self.page.lists["love"], MOD_ITEM)
                QTest.keyClick(self.page.lists["love"], key)
                self.assertEqual(self.page.dump()["love"], ["(O)66"])

    def test_assign_button_path_supports_multiple_selection_without_dragging(self):
        self.use_import()
        for row in range(self.page.library.count()):
            self.page.library.item(row).setSelected(True)
        self.page.target.setCurrentIndex(self.page.target.findData("dislike"))
        self.page.assign_selected()
        self.assertEqual(set(self.list_ids(self.page.lists["dislike"])), {"66", "395", MOD_ITEM})

    def test_assign_uses_last_taste_selection_instead_of_stale_library_selection(self):
        self.use_import()
        self.page.assign_items(["66"], "love")
        self.select(self.page.library, "395")
        self.select(self.page.lists["love"], "66")
        self.page.target.setCurrentIndex(self.page.target.findData("hate"))
        self.page.assign_selected()
        self.assertEqual(self.page.dump()["love"], [])
        self.assertEqual(self.page.dump()["hate"], ["(O)66"])
        self.assertNotIn("395", self.page.memberships())

    def test_reset_to_vanilla_retains_mod_assignments_and_unknown_gift_metadata(self):
        self.use_import()
        gifts = {"love": ["(O)" + MOD_ITEM], "like": ["Coffee"], "extension": {"credit": "Creator", "seasonal": True}}
        self.page.load(gifts)
        self.page.reset_catalog()
        self.assertIsNone(self.page.catalog_snapshot())
        self.assertEqual(self.page.dump()["love"], gifts["love"])
        self.assertEqual(self.page.dump()["extension"], gifts["extension"])
        self.assertIn("not in catalog", self.page.lists["love"].item(0).text())
        self.assertTrue(self.page.feedback.text())
        self.page.assign_items(["(O)" + MOD_ITEM], "hate")
        self.assertEqual(self.page.dump()["love"], [])
        self.assertEqual(self.page.dump()["hate"], gifts["love"])
        self.page.assign_items(["(O)" + MOD_ITEM], None)
        self.assertEqual(self.page.dump()["hate"], [])

    def test_loading_and_dumping_do_not_mutate_callers_or_emit_edits(self):
        gifts = {"love": ["Amethyst"], "extension": {"preserved": [1, 2]}}
        original = deepcopy(gifts)
        changes = QSignalSpy(self.page.changed)
        self.page.load_catalog_snapshot(self.snapshot)
        self.page.load(gifts)
        snapshot = self.page.catalog_snapshot()
        snapshot["items"].clear()
        dumped = self.page.dump()
        dumped["extension"]["preserved"].clear()
        dumped["love"].clear()
        self.assertEqual(gifts, original)
        self.assertEqual(self.page.dump()["love"], ["(O)66"])
        self.assertEqual(self.page.dump()["extension"], original["extension"])
        self.assertEqual(self.page.catalog_snapshot(), self.snapshot)
        self.assertEqual(changes.count(), 0)

    def test_more_than_one_hundred_gifts_can_be_assigned_validated_and_saved(self):
        raw = {f"Example.Collection_Item{index}": {"Name": f"Catalog item {index}", "Category": -79} for index in range(125)}
        large_snapshot = load_catalog(self.write_objects(self.root / "many-items.json", raw))
        window = self.keep(MainWindow())
        window.gifts.load_catalog_snapshot(large_snapshot)
        self.assertTrue(window.gifts.assign_items(list(raw), "love"))
        gifts = window.gifts.dump()
        self.assertEqual(len(validate_draft({"gifts": gifts})["gifts"]["love"]), 125)
        self.assertTrue(window.save_to(self.root / "many-gifts" / "character.json"))
        saved = load_project(window.project_file)
        self.assertEqual(saved["character"]["gifts"], gifts)
        gift_errors = [issue for issue in validate_character(saved["character"]) if issue["level"] == "error" and issue["field"].startswith("gifts")]
        self.assertEqual(gift_errors, [])

    def test_imported_snapshot_survives_save_as_and_reopen_without_source_export(self):
        window = self.keep(MainWindow())
        window.gifts.load_catalog_snapshot(self.snapshot)
        window.gifts.load({"love": [], "extension": {"note": "Gift notes stay portable"}})
        window.document["workspace"] = {"extension": "keep"}
        window.gifts.assign_items([MOD_ITEM], "love")
        source = self.root / "original" / "character.json"
        destination = self.root / "copy" / "character.json"
        self.assertTrue(window.save_to(source))
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")):
            self.assertTrue(window.save_as())
        self.export_file.unlink()
        source.unlink()
        reopened = self.keep(MainWindow())
        self.assertTrue(reopened.open_path(destination))
        self.assertEqual(reopened.gifts.catalog_snapshot(), self.snapshot)
        self.assertEqual(reopened.gifts.dump()["love"], ["(O)" + MOD_ITEM])
        self.assertEqual(reopened.gifts.dump()["extension"], {"note": "Gift notes stay portable"})
        self.assertEqual(reopened.document["workspace"], {"extension": "keep"})
        self.assertFalse(reopened.dirty)
        self.assertIn("Moonfruit", reopened.gifts.lists["love"].item(0).text())

    def test_mod_item_assignment_exports_its_exact_id_in_content_patcher_tastes(self):
        window = self.keep(MainWindow())
        window.gifts.load_catalog_snapshot(self.snapshot)
        window.gifts.assign_items([MOD_ITEM], "love")
        window.collect()
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        Image.new("RGBA", (64, 416)).save(sprite)
        payload = build_mod_archive(window.document["character"], portrait, sprite)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            content = json.loads(archive.read("[CP] NewCharacter/content.json"))
            gift_patch = next(change for change in content["Changes"] if change["Target"] == "Data/NPCGiftTastes")
            fields = next(iter(gift_patch["Entries"].values())).split("/")
        self.assertEqual(fields[1], MOD_ITEM)

    def test_qualified_numeric_item_ids_preserve_leading_zeros_in_export(self):
        raw = {
            "395": {"Name": "Coffee", "Category": -7},
            "000395": {"Name": "Alternate Coffee", "Category": -7},
        }
        snapshot = load_catalog(self.write_objects(self.root / "numeric-items.json", raw))
        window = self.keep(MainWindow())
        window.gifts.load_catalog_snapshot(snapshot)
        window.gifts.assign_items(["(O)000395"], "love")
        window.gifts.assign_items(["(O)395"], "hate")
        window.collect()
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        Image.new("RGBA", (64, 416)).save(sprite)
        payload = build_mod_archive(window.document["character"], portrait, sprite)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            content = json.loads(archive.read("[CP] NewCharacter/content.json"))
            gift_patch = next(change for change in content["Changes"] if change["Target"] == "Data/NPCGiftTastes")
            fields = next(iter(gift_patch["Entries"].values())).split("/")
        self.assertEqual(fields[1], "000395")
        self.assertEqual(fields[7], "395")


if __name__ == "__main__":
    unittest.main()
