"""An automatic save includes the open Home room without closing or finalizing it."""
from copy import deepcopy
import json
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox

from pixelheart import app as app_module
from pixelheart_core.projects import ProjectError, load_project

from tests.qt_support import QtTestCase
from tests import test_home_workspace as home_tests
from tests import test_home_project_history as history_tests


class AutosaveHomeTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = home_tests.HomeWorkspaceTests.setUp
    tearDown = home_tests.HomeWorkspaceTests.tearDown
    document = home_tests.HomeWorkspaceTests.document
    place = home_tests.HomeWorkspaceTests.place
    open_home = home_tests.HomeWorkspaceTests.open_home
    snapshot = history_tests.HomeProjectHistoryTests.snapshot
    restore = history_tests.HomeProjectHistoryTests.restore

    def texture(self, name, color):
        path = self.root / name
        with Image.new("RGBA", (32, 16), color) as image:
            image.save(path)
        return path

    def open_saved_home(self):
        home = self.place("TheirHome")
        return self.open_home(self.document([home], home=home), saved=True)

    def test_without_room_changes_the_document_is_copied_unchanged(self):
        self.open_saved_home()
        self.window.collect()
        document = deepcopy(self.window.document)
        self.assertFalse(self.page.room_draft_open())
        result, issue = self.page.quiet_save_document(document, self.window.project_file)
        self.assertIsNone(issue)
        self.assertEqual(result, document)
        self.assertIsNot(result, document)

    def test_open_room_is_projected_and_its_new_texture_copied_into_the_project(self):
        editor = self.open_saved_home()
        texture = self.texture("new.png", "#445566")
        editor.load_atlas(texture)
        self.assertTrue(self.page.room_draft_open())
        applied = deepcopy(self.page.dump())
        self.window.collect()
        result, issue = self.page.quiet_save_document(self.window.document, self.window.project_file)
        self.assertIsNone(issue)
        reference = result["world"]["locations"][0]["interior"]["atlas"]["asset"]
        self.assertNotEqual(reference, "tiles.png")
        saved = self.window.project_file.parent / reference
        self.assertEqual(saved.read_bytes(), texture.read_bytes())
        self.assertIs(self.page.interior_editor, editor)
        self.assertTrue(self.page.room_draft_open())
        self.assertEqual(self.page.dump(), applied)

    def test_conflicting_texture_in_the_project_keeps_the_applied_room(self):
        editor = self.open_saved_home()
        editor.load_atlas(self.texture("new.png", "#445566"))
        self.window.collect()
        projected, _ = self.page.quiet_save_document(self.window.document, self.window.project_file)
        reference = projected["world"]["locations"][0]["interior"]["atlas"]["asset"]
        destination = self.window.project_file.parent / reference
        destination.write_bytes(b"different bytes")
        result, issue = self.page.quiet_save_document(self.window.document, self.window.project_file)
        self.assertIn("different contents", issue)
        self.assertEqual(result, self.window.document)
        self.assertEqual(destination.read_bytes(), b"different bytes")

    def test_undo_restored_room_textures_are_copied_add_only(self):
        editor = self.open_saved_home()
        editor.load_atlas(self.texture("first.png", "#112233"))
        first = self.snapshot()
        reference = first["world"]["locations"][0]["interior"]["atlas"]["asset"]
        self.restore(first)
        destination = self.window.project_file.parent / reference
        self.assertFalse(destination.exists())
        self.assertIsNone(self.page.copy_history_assets(self.window.project_file))
        self.assertEqual(destination.read_bytes(), (self.root / "first.png").read_bytes())
        self.assertIsNone(self.page.copy_history_assets(self.window.project_file))


class AutosaveRoomWindowTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = home_tests.HomeWorkspaceTests.setUp
    document = home_tests.HomeWorkspaceTests.document
    place = home_tests.HomeWorkspaceTests.place
    open_home = home_tests.HomeWorkspaceTests.open_home
    texture = AutosaveHomeTests.texture

    def tearDown(self):
        self.window.autosave.timer.stop()
        home_tests.HomeWorkspaceTests.tearDown(self)

    def saved_room_asset(self):
        data = json.loads(self.window.project_file.read_text())
        return data["world"]["locations"][0]["interior"]["atlas"]["asset"]

    def test_open_room_changes_are_saved_and_the_room_stays_open(self):
        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        editor.load_atlas(self.texture("new.png", "#445566"))
        self.assertTrue(self.window.dirty)
        self.window.autosave.timer.timeout.emit()
        reference = self.saved_room_asset()
        self.assertNotEqual(reference, "tiles.png")
        self.assertTrue((self.window.project_file.parent / reference).is_file())
        self.assertFalse(self.window.dirty)
        self.assertIs(self.page.interior_editor, editor)
        self.assertEqual(self.window.save_state.text(), "All changes saved")
        reopened = load_project(self.window.project_file)
        self.assertEqual(reopened["world"]["locations"][0]["interior"]["atlas"]["asset"], reference)

    def test_invalid_room_saves_rest_and_close_asks(self):
        real_save = app_module.save_project

        def reject_projected_room(document, path):
            if document["world"]["locations"][0]["interior"]["atlas"]["asset"] != "tiles.png":
                raise ProjectError("This room has a problem.")
            return real_save(document, path)

        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        self.window.identity.fields["name"].setText("Juniper")
        editor.load_atlas(self.texture("new.png", "#445566"))
        with patch("pixelheart.app.save_project", side_effect=reject_projected_room):
            self.window.autosave.timer.timeout.emit()
        saved = json.loads(self.window.project_file.read_text())
        self.assertEqual(saved["character"]["name"], "Juniper")
        self.assertEqual(self.saved_room_asset(), "tiles.png")
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.window.save_state.text(), "Saved, except the room you're editing")
        self.assertIn("This room has a problem.", self.window.save_state.toolTip())
        self.assertTrue(self.window.save_button.isHidden())
        with (patch("pixelheart.app.save_project", side_effect=reject_projected_room),
              patch("pixelheart.app.QMessageBox.warning", return_value=QMessageBox.StandardButton.Cancel) as question):
            self.assertFalse(self.window.confirm_discard())
        self.assertIn("This room has a problem.", question.call_args.args[2])
        self.assertIs(self.page.interior_editor, editor)
