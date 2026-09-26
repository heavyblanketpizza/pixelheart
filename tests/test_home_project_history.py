"""Project snapshots include live Home drafts and their dependent destinations."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart_core.life import new_life_record
from pixelheart_core.story import new_actor, new_beat, new_event
from pixelheart_core.world import exported_location_id, new_companion, new_location
from pixelheart_core.projects import ProjectError, load_project
from tests.qt_support import QtTestCase
from tests import test_home_workspace as home_tests
from tests.test_interior_decorating_flow import make_library


class HomeProjectHistoryTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = home_tests.HomeWorkspaceTests.setUp
    tearDown = home_tests.HomeWorkspaceTests.tearDown
    document = home_tests.HomeWorkspaceTests.document
    place = home_tests.HomeWorkspaceTests.place
    open_home = home_tests.HomeWorkspaceTests.open_home
    edit_floor = home_tests.HomeWorkspaceTests.edit_floor

    def snapshot(self):
        self.window.collect()
        return self.page.history_snapshot(self.window.document)

    def restore(self, snapshot):
        self.window.loading = True
        try:
            self.window.document = deepcopy(snapshot)
            self.window.identity.load(snapshot["character"])
            self.page.load(snapshot["world"], from_history=True)
            self.page.activate_workspace()
        finally:
            self.window.loading = False

    def test_live_new_room_snapshot_is_stable_and_matches_navigation_commit(self):
        editor = self.open_home()
        untouched = self.snapshot()
        self.assertEqual(untouched["world"]["locations"], [])
        self.edit_floor(editor)
        first = self.snapshot()
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(self.page.dump()["locations"], [])
        self.assertIs(self.page.interior_editor, editor)
        editor.apply_tile(0)
        second = self.snapshot()
        self.assertEqual(first["world"]["locations"][0]["id"], second["world"]["locations"][0]["id"])
        self.assertTrue(self.page.flush_editor())
        self.assertEqual(self.snapshot(), second)

    def test_room_move_snapshots_translate_all_destinations_once_without_mutating_forms(self):
        home = self.place("TheirHome")
        other = new_location()
        other.update(name="Other place", internal_name="OtherPlace")
        other["entrance"].update(map="TheirHome", x=7, y=8, arrival_x=8, arrival_y=9)
        document = self.document([home, other], home=home)
        character = document["character"]
        character.update(home_x=7, home_y=8)
        exported = exported_location_id(home, character)
        character["schedule"] = [{"time": "600", "location": exported, "x": 7, "y": 8,
                                  "facing": "down", "activity": "Read"}]
        character.setdefault("life", {})["routines"] = [new_life_record("routines", character)]
        event = new_event(character)
        actor = new_actor()
        actor.update(x=8, y=9)
        beat = new_beat("move")
        beat.update(x=-1, y=1)
        event["story"].update(actors=[actor], beats=[beat])
        character["events"] = [event]
        bundled = new_companion("Visitor")
        bundled["character"].update(home_map="TheirHome", home_x=7, home_y=8)
        document["world"]["characters"] = [bundled]
        editor = self.open_home(document, saved=True)
        before = self.snapshot()
        self.assertTrue(editor.move_room("main", 4, 5), editor.status.text())
        moved = self.snapshot()
        self.assertEqual(self.snapshot(), moved)
        self.assertEqual(self.window.identity.dump()["home_x"], before["character"]["home_x"])
        self.assertEqual(self.window.schedule.dump(), before["character"]["schedule"])
        self.assertEqual(self.page.dump(), before["world"])
        after = moved["character"]
        self.assertEqual((after["home_x"], after["home_y"]), (9, 8))
        self.assertEqual(int(after["schedule"][0]["x"]), 9)
        self.assertEqual(int(after["life"]["routines"][0]["stops"][0]["x"]), 9)
        self.assertEqual(after["events"][0]["story"]["actors"][0]["x"], 10)
        self.assertEqual(after["events"][0]["story"]["beats"], [beat])
        self.assertEqual(moved["world"]["characters"][0]["character"]["home_x"], 9)
        self.assertEqual(moved["world"]["locations"][1]["entrance"]["x"], 9)
        self.assertEqual(moved["world"]["locations"][1]["entrance"]["arrival_x"], 10)
        self.assertTrue(self.page.flush_editor())
        self.assertEqual(self.snapshot(), moved)

    def test_history_retains_replaced_unsaved_texture_after_editor_disposal(self):
        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        first_texture = self.root / "first.png"
        second_texture = self.root / "second.png"
        for path, color in ((first_texture, "#112233"), (second_texture, "#aabbcc")):
            with Image.new("RGBA", (32, 16), color) as image:
                image.save(path)
        editor.load_atlas(first_texture)
        first = self.snapshot()
        reference = first["world"]["locations"][0]["interior"]["atlas"]["asset"]
        self.assertFalse((self.window.project_file.parent / reference).exists())
        scratch = self.page.draft_project_file.parent
        self.assertEqual((scratch / reference).read_bytes(), first_texture.read_bytes())
        editor.load_atlas(second_texture)
        self.snapshot()
        self.assertTrue(self.page.flush_editor())
        self.assertFalse((self.window.project_file.parent / reference).exists())
        self.restore(first)
        restored = self.page.interior_editor
        self.assertEqual((restored.stage_root / reference).read_bytes(), first_texture.read_bytes())
        self.assertTrue(self.page.flush_editor())
        self.assertTrue(self.page.publish_history_assets())
        self.assertEqual((self.window.project_file.parent / reference).read_bytes(), first_texture.read_bytes())
        self.page.reset_history_resources()
        self.assertFalse(scratch.exists())

    def test_unfinished_room_remains_snapshotable_before_save_validation(self):
        editor = self.open_home()
        candidate = editor.draft.snapshot()
        candidate.pop("doorway")
        editor.run_change(lambda: editor.draft.apply(candidate))
        snapshot = self.snapshot()
        self.assertNotIn("doorway", snapshot["world"]["locations"][0]["interior"])
        self.assertFalse(editor.prepare_design())
        self.assertIs(self.page.interior_editor, editor)
        self.restore(snapshot)
        self.assertEqual(self.page.interior_editor.draft.data, snapshot["world"]["locations"][0]["interior"])

    def test_history_restore_does_not_reimport_the_remembered_library(self):
        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        before = self.snapshot()
        self.assertEqual(editor.draft.data["catalog"], [])
        self.assertTrue(editor.load_catalog(make_library(self.root / "library")))
        after = self.snapshot()
        self.assertTrue(after["world"]["locations"][0]["interior"]["catalog"])
        self.restore(before)
        self.assertEqual(self.page.interior_editor.draft.data, before["world"]["locations"][0]["interior"])
        self.restore(after)
        self.assertEqual(self.page.interior_editor.draft.data, after["world"]["locations"][0]["interior"])

    def test_embedded_project_uses_only_global_undo_controls(self):
        # The project controller owns shortcuts throughout the main workspace.
        if not hasattr(self.window, "project_history"):
            self.window.project_history = object()
        editor = self.open_home()
        self.assertTrue(editor.undo_button.isHidden())
        self.assertTrue(editor.redo_button.isHidden())
        standard = {QKeySequence(key).toString() for key in (QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo)}
        self.assertFalse(standard & {shortcut.key().toString() for shortcut in editor.shortcuts})

    def test_global_history_follows_room_and_identity_edits_without_navigation_steps(self):
        residence = self.place("TheirHome")
        spouse = self.place("SpouseRoom", spouse=True)
        editor = self.open_home(self.document([residence, spouse], home=residence), saved=True)
        controller = self.window.project_history
        editor.apply_tile(1)
        self.assertEqual(controller.history.undo_count, 1)
        self.window.open_section("identity")
        self.assertEqual(controller.history.undo_count, 1)
        self.window.identity.fields["name"].setText("Mira Edited")
        self.assertEqual(controller.history.undo_count, 2)
        self.window.open_section("home")
        self.page.room_switch.setCurrentIndex(1)
        self.assertEqual(controller.history.undo_count, 2)
        self.page.interior_editor.apply_tile(1)
        self.assertEqual(controller.history.undo_count, 3)
        self.assertTrue(controller.undo())
        self.assertEqual(self.page.room_switch.currentIndex(), 1)
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 0)
        self.assertEqual(self.window.identity.dump()["name"], "Mira Edited")
        self.assertTrue(controller.undo())
        self.assertEqual(self.window.identity.dump()["name"], "Mira")
        self.assertEqual(self.page.world["locations"][0]["interior"]["style"]["floor"], 1)
        self.assertTrue(controller.undo())
        self.assertEqual(self.page.world["locations"][0]["interior"]["style"]["floor"], 0)
        self.assertFalse(self.window.dirty)
        self.assertFalse(controller.history.can_undo)
        for _ in range(3):
            self.assertTrue(controller.redo())
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 1)
        self.assertEqual(self.window.identity.dump()["name"], "Mira Edited")
        self.assertEqual(controller.history.undo_count, 3)

    def test_first_save_initializes_project_history_and_retains_current_room_textures(self):
        editor = self.open_home()
        self.edit_floor(editor)
        controller = self.window.project_history
        self.assertTrue(controller.history.can_undo)
        scratch = self.page.draft_project_file.parent
        destination = self.root / "saved" / "character.json"
        self.assertTrue(self.window.save_to(destination))
        saved = load_project(destination)
        design = saved["world"]["locations"][0]["interior"]
        self.assertEqual((destination.parent / design["atlas"]["asset"]).read_bytes(), self.sheet.read_bytes())
        self.assertFalse(scratch.exists())
        self.assertFalse(controller.history.can_undo)
        self.assertFalse(controller.history.can_redo)
        self.assertFalse(self.window.dirty)
        self.page.interior_editor.apply_tile(0)
        self.assertEqual(controller.history.undo_count, 1)
        self.assertTrue(controller.undo())
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 1)
        self.assertFalse(self.window.dirty)
        self.assertTrue(self.window.save_to(destination))
        self.assertFalse(controller.history.can_redo)
        self.assertFalse(controller.redo())

    def test_save_after_undo_from_identity_publishes_only_referenced_history_texture(self):
        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        first_texture = self.root / "first-history.png"
        second_texture = self.root / "second-history.png"
        for path, color in ((first_texture, "#102030"), (second_texture, "#405060")):
            with Image.new("RGBA", (32, 16), color) as image:
                image.save(path)
        editor.load_atlas(first_texture)
        first = editor.draft.snapshot()
        editor.load_atlas(second_texture)
        second = editor.draft.snapshot()
        controller = self.window.project_history
        self.assertEqual(controller.history.undo_count, 2)
        self.assertTrue(controller.undo())
        self.assertEqual(self.page.interior_editor.draft.data, first)
        self.window.open_section("identity")
        self.assertIsNone(self.page.interior_editor)
        first_destination = self.window.project_file.parent / first["atlas"]["asset"]
        second_destination = self.window.project_file.parent / second["atlas"]["asset"]
        self.assertFalse(first_destination.exists())
        self.assertFalse(second_destination.exists())
        scratch = self.page.draft_project_file.parent
        self.assertTrue(self.window.save_to(self.window.project_file))
        self.assertEqual(first_destination.read_bytes(), first_texture.read_bytes())
        self.assertFalse(second_destination.exists())
        self.assertFalse(scratch.exists())
        self.assertEqual(load_project(self.window.project_file)["world"]["locations"][0]["interior"], first)
        self.assertFalse(controller.history.can_undo)
        self.assertFalse(controller.history.can_redo)

    def test_failed_save_keeps_global_undo_redo_and_staged_texture(self):
        editor = self.open_home()
        self.edit_floor(editor)
        controller = self.window.project_history
        before_count = controller.history.undo_count
        expected = self.window.project_snapshot()
        with patch("pixelheart.app.copy_project", side_effect=ProjectError("Disk unavailable")):
            self.assertFalse(self.window.save_to(self.root / "blocked" / "character.json"))
        self.assertEqual(controller.history.undo_count, before_count)
        self.assertEqual(self.window.project_snapshot(), expected)
        self.assertTrue(controller.undo())
        self.assertEqual(self.page.interior_editor.draft.data["style"]["floor"], 0)
        self.assertTrue(controller.redo())
        self.assertEqual(self.window.project_snapshot(), expected)
        reference = self.page.interior_editor.draft.data["atlas"]["asset"]
        self.assertEqual((self.page.interior_editor.stage_root / reference).read_bytes(), self.sheet.read_bytes())
        self.assertTrue(self.window.dirty)
        self.errors.assert_called_once()

    def test_failed_history_asset_copy_reopens_home_without_resetting_history(self):
        editor = self.open_home()
        self.edit_floor(editor)
        controller = self.window.project_history
        before = self.window.project_snapshot()
        count = controller.history.undo_count
        with patch.object(self.page, "publish_history_assets", return_value=False):
            self.assertFalse(self.window.save_to(self.root / "unwritten" / "character.json"))
        self.assertIsNotNone(self.page.interior_editor)
        self.assertEqual(self.window.project_snapshot(), before)
        self.assertEqual(controller.history.undo_count, count)
        self.assertTrue(self.window.dirty)

    def test_full_place_list_cannot_create_unrestorable_pending_room(self):
        places = []
        for index in range(32):
            place = new_location()
            place.update(name=f"Place {index}", internal_name=f"Place{index}")
            places.append(place)
        self.open_home(self.document(places))
        controller = self.window.project_history
        self.assertIsNone(self.page.interior_editor)
        self.page.room_switch.setCurrentIndex(1)
        self.assertIsNone(self.page.interior_editor)
        self.assertEqual(len(self.window.project_snapshot()["world"]["locations"]), 32)
        self.assertFalse(controller.history.can_undo)
        self.page.location_list.setCurrentRow(0)
        self.page.remove_location()
        self.page.activate_workspace()
        self.assertIsNotNone(self.page.interior_editor)
        self.assertEqual(controller.history.undo_count, 1)
        self.assertTrue(controller.undo())
        self.assertEqual(len(self.page.world["locations"]), 32)
        self.assertIsNone(self.page.interior_editor)
        self.assertTrue(controller.redo())
        self.assertEqual(len(self.page.world["locations"]), 31)
        self.assertIsNotNone(self.page.interior_editor)

    def test_global_undo_room_move_and_resize_keep_schedule_with_the_room(self):
        home = self.place("TheirHome")
        document = self.document([home], home=home)
        document["character"].update(home_x=7, home_y=8)
        document["character"]["schedule"] = [{"time": "600", "location": "TheirHome", "x": 7, "y": 8,
                                               "facing": "down", "activity": "Read"}]
        editor = self.open_home(document, saved=True)
        controller = self.window.project_history
        self.assertTrue(editor.move_room("main", 4, 5))
        self.assertTrue(editor.resize_room("main", 3, 5, 11, 8))
        self.assertEqual(controller.history.undo_count, 2)
        for direction, expected_x, expected_width in (("undo", 9, 10), ("undo", 7, 10),
                                                      ("redo", 9, 10), ("redo", 9, 11)):
            self.assertTrue(getattr(controller, direction)())
            self.assertEqual(self.window.identity.dump()["home_x"], expected_x)
            self.assertEqual(int(self.window.schedule.dump()[0]["x"]), expected_x)
            self.assertEqual(self.page.interior_editor.draft.data["rooms"][0]["width"], expected_width)
        self.window.open_section("identity")
        self.assertEqual(controller.history.undo_count, 2)
        self.assertEqual(self.window.identity.dump()["home_x"], 9)
        self.assertEqual(int(self.window.schedule.dump()[0]["x"]), 9)

    def test_global_history_restores_unfinished_dependency_text_without_validation_errors(self):
        self.open_home()
        controller = self.window.project_history
        self.page.add_dependency()
        version = self.page.dependency_fields["minimum_version"]
        version.setText("1.")
        version.setText("1.2")
        self.assertTrue(controller.undo())
        self.assertEqual(version.text(), "1.")
        self.assertEqual(self.page.world["dependencies"][0]["minimum_version"], "1.")
        self.assertTrue(controller.undo())
        self.assertEqual(version.text(), "")
        self.assertTrue(controller.redo())
        self.assertEqual(version.text(), "1.")
        identity = self.page.dependency_fields["id"]
        identity.setText("")
        identity.setText("Author.Replacement")
        self.assertTrue(controller.undo())
        self.assertEqual(identity.text(), "")
        self.assertTrue(controller.redo())
        self.assertEqual(identity.text(), "Author.Replacement")

    def test_advanced_room_tools_use_global_keyboard_history_and_stay_open(self):
        home = self.place("TheirHome")
        editor = self.open_home(self.document([home], home=home), saved=True)
        self.window.show()
        editor.advanced_tabs.setCurrentIndex(1)
        editor.advanced.open()
        self.app.processEvents()
        self.assertTrue(editor.advanced.property("projectHistoryLive"))
        width = editor.draft.data["width"]
        editor.canvas_width.setValue(width + 1)
        editor.resize_canvas()
        self.assertEqual(self.window.project_history.history.undo_count, 1)
        editor.canvas_width.setFocus()
        QTest.keySequence(editor.canvas_width, QKeySequence(QKeySequence.StandardKey.Undo))
        self.app.processEvents()
        restored = self.page.interior_editor
        self.assertEqual(restored.draft.data["width"], width)
        self.assertTrue(restored.advanced.isVisible())
        self.assertEqual(restored.advanced_tabs.currentIndex(), 1)
        restored.canvas_width.setFocus()
        QTest.keySequence(restored.canvas_width, QKeySequence(QKeySequence.StandardKey.Redo))
        self.app.processEvents()
        restored = self.page.interior_editor
        self.assertEqual(restored.draft.data["width"], width + 1)
        self.assertTrue(restored.advanced.isVisible())
        self.assertEqual(restored.advanced_tabs.currentIndex(), 1)
        self.assertTrue(self.window.save_to(self.window.project_file))
        self.assertTrue(self.page.interior_editor.advanced.isHidden())
        self.assertFalse(self.window.project_history.history.can_undo)


if __name__ == "__main__":
    unittest.main()
