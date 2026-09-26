"""The Home section edits its two roles directly and saves drafts safely."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interiors import doorway_exit, ensure_doorway, new_interior
from pixelheart_core.projects import ProjectError, load_project, new_project, save_project
from pixelheart_core.world import exported_location_id, new_location, new_world


class HomeWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sheet = self.root / "tiles.png"
        with Image.new("RGBA", (32, 16), "#aabbcc") as image:
            image.save(self.sheet)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.window = MainWindow(auto_download_icons=False)
        self.errors = self.enterContext(patch.object(self.window, "show_error"))
        self.page = self.window.world

    def tearDown(self):
        self.page.reset_workspace()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def document(self, locations=(), *, home=None):
        document = new_project()
        document["character"].update(name="Mira", internal_name="Mira", id="workspace-mira")
        document["world"] = new_world()
        document["world"]["locations"] = deepcopy(list(locations))
        if home:
            document["character"].update(home_map=home["internal_name"],
                                         home_x=home["entry_x"], home_y=home["entry_y"])
        return document

    def place(self, name, *, spouse=False):
        design = ensure_doorway(new_interior("spouse" if spouse else "residence"))
        design["atlas"] = {"asset": "tiles.png", "columns": 2, "tile_count": 2}
        record = new_location()
        record.update(name=name, internal_name=name, interior=design, spouse_room=spouse,
                      entry_x=design["entry"][0], entry_y=design["entry"][1])
        if not spouse:
            record["exit_x"], record["exit_y"] = doorway_exit(design)
        return record

    def open_home(self, document=None, *, saved=False):
        document = document or self.document()
        path = self.root / "character.json" if saved else None
        if path:
            save_project(document, path)
        self.window.load_document(document, path)
        self.window.open_section("home")
        return self.page.interior_editor

    def edit_floor(self, editor, tile=1):
        if not editor.draft.data["atlas"]["asset"]:
            editor.load_atlas(self.sheet)
        editor.apply_tile(tile)

    def test_home_opens_residence_editor_directly_without_creating_untouched_records(self):
        editor = self.open_home()
        self.assertIsNotNone(editor)
        self.assertTrue(editor.embedded)
        self.assertFalse(editor.isWindow())
        self.assertEqual(editor.draft.data["kind"], "residence")
        self.assertEqual(self.page.room_switch.count(), 2)
        self.assertTrue(self.page.settings_dialog.isHidden())
        self.assertEqual(self.page.dump()["locations"], [])
        self.assertFalse(self.window.dirty)
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(self.page.interior_editor.draft.data["kind"], "spouse")
        self.window.open_section("identity")
        self.assertEqual(self.page.dump()["locations"], [])
        self.assertFalse(self.window.dirty)

    def test_switching_applies_the_current_undo_result_and_keeps_exactly_two_roles(self):
        editor = self.open_home()
        self.edit_floor(editor)
        editor.undo()
        self.assertEqual(editor.draft.data["style"]["floor"], 0)
        editor.redo()
        self.assertTrue(self.window.dirty)
        self.page.room_switch.setCurrentIndex(1)
        locations = self.page.dump()["locations"]
        self.assertEqual(len(locations), 1)
        residence = locations[0]
        self.assertFalse(residence["spouse_room"])
        self.assertEqual(residence["interior"]["style"]["floor"], 1)
        self.assertEqual(self.window.identity.dump()["home_map"], residence["internal_name"])
        spouse_editor = self.page.interior_editor
        self.edit_floor(spouse_editor)
        self.page.room_switch.setCurrentIndex(0)
        locations = self.page.dump()["locations"]
        self.assertEqual(len(locations), 2)
        self.assertEqual(sum(record["spouse_room"] for record in locations), 1)
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 1)
        self.assertEqual(self.window.identity.dump()["home_map"], residence["internal_name"])
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 1)
        self.assertEqual(len(self.page.dump()["locations"]), 2)

    def test_role_switch_resolves_spouse_first_and_exported_residence_assignment(self):
        spouse = self.place("SpouseRoom", spouse=True)
        residence = self.place("Residence")
        document = self.document([spouse, residence], home=residence)
        document["character"]["home_map"] = exported_location_id(residence, document["character"])
        self.open_home(document, saved=True)
        self.assertEqual(self.page._editor_record_id, residence["id"])
        self.edit_floor(self.page.interior_editor)
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(self.page._editor_record_id, spouse["id"])
        self.edit_floor(self.page.interior_editor)
        self.page.room_switch.setCurrentIndex(0)
        locations = self.page.dump()["locations"]
        self.assertEqual([record["id"] for record in locations], [spouse["id"], residence["id"]])
        self.assertEqual(locations[0]["interior"]["style"]["floor"], 1)
        self.assertEqual(locations[1]["interior"]["style"]["floor"], 1)

    def test_navigation_applies_draft_and_reopening_restores_its_assets(self):
        editor = self.open_home()
        self.edit_floor(editor)
        stage = editor.stage_root
        self.window.open_section("identity")
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["identity"])
        self.assertFalse(stage.exists())
        self.assertIsNone(self.page.interior_editor)
        self.assertTrue(self.window.dirty)
        home = self.page.dump()["locations"][0]
        self.assertEqual(home["interior"]["style"]["floor"], 1)
        self.window.open_section("home")
        restored = self.page.interior_editor
        self.assertEqual(restored.draft.data, home["interior"])
        self.assertTrue((restored.stage_root / home["interior"]["atlas"]["asset"]).is_file())

    def test_save_and_save_as_keep_staged_assets_with_the_saved_home(self):
        self.edit_floor(self.open_home())
        first = self.root / "first" / "character.json"
        self.assertTrue(self.window.save_to(first))
        first_document = load_project(first)
        home = first_document["world"]["locations"][0]
        reference = home["interior"]["atlas"]["asset"]
        self.assertEqual((first.parent / reference).read_bytes(), self.sheet.read_bytes())
        self.assertEqual(home["interior"]["style"]["floor"], 1)
        self.assertFalse(self.window.dirty)
        self.assertIsNotNone(self.page.interior_editor)
        self.page.interior_editor.apply_tile(0)
        second = self.root / "second" / "character.json"
        self.assertTrue(self.window.save_to(second))
        second_document = load_project(second)
        self.assertEqual(second_document["world"]["locations"][0]["interior"]["style"]["floor"], 0)
        self.assertEqual((second.parent / reference).read_bytes(), self.sheet.read_bytes())
        self.assertEqual(load_project(first)["world"]["locations"][0]["interior"]["style"]["floor"], 1)
        self.assertFalse(self.window.dirty)
        self.errors.assert_not_called()

    def test_invalid_draft_blocks_switch_save_and_navigation_until_repaired(self):
        editor = self.open_home()
        self.edit_floor(editor)
        reference = editor.draft.data["atlas"]["asset"]
        (editor.stage_root / reference).unlink()
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(self.page.room_switch.currentIndex(), 0)
        self.assertIs(self.page.interior_editor, editor)
        destination = self.root / "invalid" / "character.json"
        self.assertFalse(self.window.save_to(destination))
        self.assertFalse(destination.exists())
        self.window.open_section("identity")
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["home"])
        self.assertIs(self.page.interior_editor, editor)
        self.assertIn("texture is missing", editor.status.text())
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.page.dump()["locations"], [])

    def test_failed_project_save_reopens_the_applied_room_with_its_staged_assets(self):
        editor = self.open_home()
        self.edit_floor(editor)
        with patch("pixelheart.app.copy_project", side_effect=ProjectError("Disk is unavailable")):
            self.assertFalse(self.window.save_to(self.root / "unavailable" / "character.json"))
        restored = self.page.interior_editor
        self.assertIsNotNone(restored)
        self.assertEqual(restored.draft.data["style"]["floor"], 1)
        reference = restored.draft.data["atlas"]["asset"]
        self.assertTrue((restored.stage_root / reference).is_file())
        self.assertTrue(self.window.dirty)
        self.assertIsNone(self.window.project_file)
        self.errors.assert_called_once()

    def test_review_interior_issue_opens_the_correct_role_in_the_workspace(self):
        spouse, residence = self.place("SpouseRoom", spouse=True), self.place("Residence")
        self.open_home(self.document([spouse, residence], home=residence), saved=True)
        self.page.open_issue("world.locations.0.interior")
        self.assertTrue(self.page.settings_dialog.isHidden())
        self.assertEqual(self.page.room_switch.currentIndex(), 1)
        self.assertEqual(self.page._editor_record_id, spouse["id"])
        self.assertEqual(self.page.interior_editor.draft.data["kind"], "spouse")
        self.page.open_issue("world.locations.1.map")
        self.assertTrue(self.page.settings_dialog.isHidden())
        self.assertEqual(self.page.room_switch.currentIndex(), 0)
        self.assertEqual(self.page._editor_record_id, residence["id"])

    def test_first_save_from_settings_does_not_leave_a_stale_editor_under_modal_edits(self):
        self.window.show()
        self.edit_floor(self.open_home())
        self.page.open_settings()
        self.assertTrue(self.page.settings_dialog.isVisible())
        destination = self.root / "saved_from_settings" / "character.json"

        def accept_change(dialog):
            self.assertIsNone(self.page.interior_editor)
            dialog.apply_tile(0)
            dialog.save_design()
            return QDialog.DialogCode.Accepted

        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                patch.object(InteriorEditor, "exec", accept_change):
            self.assertTrue(self.page.design_interior())
        self.assertIsNone(self.page.interior_editor)
        self.assertEqual(self.page.dump()["locations"][0]["interior"]["style"]["floor"], 0)
        self.page.settings_dialog.accept()
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 0)
        self.assertTrue(self.window.dirty)
        self.errors.assert_not_called()

    def test_loading_another_project_discards_old_editor_and_scratch_assets(self):
        editor = self.open_home()
        self.edit_floor(editor)
        old_stage = editor.stage_root
        self.page.room_switch.setCurrentIndex(1)
        scratch = self.page.draft_project_file.parent
        spouse_stage = self.page.interior_editor.stage_root
        self.window.load_document(self.document())
        self.assertFalse(old_stage.exists())
        self.assertFalse(spouse_stage.exists())
        self.assertFalse(scratch.exists())
        self.assertIsNone(self.page.interior_editor)
        self.assertEqual(self.page.dump()["locations"], [])
        self.assertEqual(self.page.room_switch.currentIndex(), 0)
        self.assertFalse(self.window.dirty)

    def test_imported_residence_and_existing_story_places_are_preserved(self):
        imported = new_location()
        imported.update(name="Imported cottage", internal_name="ImportedHome", map="assets/maps/cottage/map.tmx")
        story = self.place("OldStoryPlace")
        document = self.document([story, imported], home=imported)
        self.open_home(document, saved=True)
        self.assertIsNone(self.page.interior_editor)
        self.assertEqual(self.page.location_index, 1)
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(self.page.interior_editor.draft.data["kind"], "spouse")
        self.page.room_switch.setCurrentIndex(0)
        self.assertIsNone(self.page.interior_editor)
        self.assertEqual(self.page.dump()["locations"], document["world"]["locations"])
        self.assertFalse(self.window.dirty)


if __name__ == "__main__":
    unittest.main()
