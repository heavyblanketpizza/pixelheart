"""Library setup and hydration are persistent infrastructure, not decorating undo steps."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interior_furniture import ROOM_FRAME_DOORWAY, ROOM_FRAME_TILES
from tests.test_interior_decorating_flow import make_library


class InteriorLibraryUndoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.dialogs = []
        self.library = make_library(self.root / "library")
        bundle = self.bundle()
        alternate = {**bundle["surfaces"][1], "id": "(FL)Test.Slate", "name": "Slate floor", "preview_asset": "slate.png"}
        with Image.new("RGBA", (32, 32), "#70828f") as image:
            image.save(self.library.parent / "slate.png")
        bundle["surfaces"].append(alternate)
        roles = ROOM_FRAME_TILES + ROOM_FRAME_DOORWAY
        with Image.new("RGBA", (16 * len(roles), 16)) as image:
            for index in range(len(roles)):
                image.paste((30 + index * 7 % 220, 60, 90, 255), (index * 16, 0, (index + 1) * 16, 16))
            image.save(self.library.parent / "frame.png")
        bundle["room_frame"] = {"preview_asset": "frame.png", "tiles": {
            role: [index * 16, 0, 16, 16] for index, role in enumerate(roles)}}
        bundle["spouse_context"] = {"background_asset": "background.png", "foreground_asset": "foreground.png"}
        for reference, rectangle, color in (("background.png", (0, 0, 32, 160), "#903020"),
                                             ("foreground.png", (32, 152, 128, 160), "#206090")):
            with Image.new("RGBA", (144, 176)) as image:
                image.paste(color, rectangle)
                image.save(self.library.parent / reference)
        self.write_bundle(bundle)

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def bundle(self):
        return json.loads(self.library.read_text(encoding="utf-8"))

    def write_bundle(self, bundle):
        self.library.write_text(json.dumps(bundle), encoding="utf-8")

    def remember(self):
        self.settings.setValue("interiors/librarySource", str(self.library))
        self.settings.setValue("interiors/libraryFolder", str(self.library.parent))

    def editor(self, kind="residence", saved=None):
        dialog = InteriorEditor(self.root / "project" / "character.json", saved, kind)
        self.dialogs.append(dialog)
        dialog.show()
        dialog.activateWindow()
        self.app.processEvents()
        return dialog

    def assert_connected(self, dialog):
        self.assertTrue(dialog.draft.data["catalog"])
        self.assertTrue(dialog.draft.data["atlas"]["asset"])
        self.assertGreater(dialog.surface_list.count(), 0)
        self.assertTrue((dialog.stage_root / dialog.draft.data["atlas"]["asset"]).is_file())
        self.assertTrue(dialog.draft.data.get("spouse_context" if dialog.draft.data["kind"] == "spouse" else "room_frame"))

    def place_chair(self, dialog):
        position = (4, 6) if dialog.draft.data["kind"] == "spouse" else (6, 7)
        self.assertTrue(dialog.place_furniture_once("(F)Test.Chair", *position, 0))

    def choose_slate(self, dialog):
        dialog.tabs.setCurrentIndex(1)
        dialog.surface_kind.setCurrentIndex(dialog.surface_kind.findData("floor"))
        item = next(dialog.surface_list.item(row) for row in range(dialog.surface_list.count())
                    if dialog.surface_list.item(row).data(Qt.ItemDataRole.UserRole) == "(FL)Test.Slate")
        dialog.choose_surface(item)
        dialog.click_tile(*( (2, 6) if dialog.draft.data["kind"] == "spouse" else (6, 7)))

    def key_undo(self, dialog):
        dialog.canvas.setFocus()
        QTest.keySequence(dialog.canvas, QKeySequence(QKeySequence.StandardKey.Undo))
        self.app.processEvents()

    def test_remembered_autoload_is_not_an_undo_step_for_home_or_spouse(self):
        self.remember()
        for kind in ("residence", "spouse"):
            with self.subTest(kind=kind):
                dialog = self.editor(kind)
                self.assert_connected(dialog)
                self.assertEqual(dialog.draft._undo, [])
                self.assertEqual(dialog.draft._redo, [])
                self.assertFalse(dialog.undo_button.isEnabled())
                self.assertFalse(dialog.redo_button.isEnabled())

    def test_button_and_keyboard_undo_stop_at_initialized_room_and_user_edits_redo(self):
        self.remember()
        for kind in ("residence", "spouse"):
            with self.subTest(kind=kind):
                dialog = self.editor(kind)
                initialized = dialog.draft.snapshot()
                initial_preview = dialog.canvas.image.toImage()
                self.place_chair(dialog)
                furnished = dialog.draft.snapshot()
                self.choose_slate(dialog)
                finished = dialog.draft.snapshot()
                QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
                self.assertEqual(dialog.draft.snapshot(), furnished)
                self.key_undo(dialog)
                self.assertEqual(dialog.draft.snapshot(), initialized)
                for _ in range(3):
                    QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
                    self.key_undo(dialog)
                self.assertEqual(dialog.draft.snapshot(), initialized)
                self.assertEqual(dialog.canvas.image.toImage(), initial_preview)
                self.assert_connected(dialog)
                self.assertFalse(dialog.undo_button.isEnabled())
                dialog.redo()
                self.assertEqual(dialog.draft.snapshot(), furnished)
                dialog.redo()
                self.assertEqual(dialog.draft.snapshot(), finished)
                self.assertFalse(dialog.redo_button.isEnabled())

    def test_automatic_missing_frame_or_context_hydration_has_no_undo(self):
        self.remember()
        for kind, field in (("residence", "room_frame"), ("spouse", "spouse_context")):
            with self.subTest(kind=kind):
                original = self.editor(kind)
                self.place_chair(original)
                self.choose_slate(original)
                original.save_design()
                saved = deepcopy(original.result_design)
                saved.pop(field)
                before = deepcopy(saved)
                reopened = self.editor(kind, saved)
                self.assert_connected(reopened)
                initialized = reopened.draft.snapshot()
                self.assertEqual(reopened.draft._undo, [])
                for _ in range(3):
                    reopened.undo()
                self.assertEqual(reopened.draft.snapshot(), initialized)
                for key in ("rooms", "furniture", "style", "room_styles", "entry", "spouse_stand"):
                    self.assertEqual(initialized[key], before[key], key)
                self.assertEqual(saved, before)

    def test_explicit_first_connection_initializes_without_setup_undo(self):
        dialog = self.editor()
        with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[self.library]):
            QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
        initialized = dialog.draft.snapshot()
        self.assert_connected(dialog)
        self.assertEqual(dialog.draft._undo, [])
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), initialized)

    def test_connect_preserves_prior_room_edit_history_and_keeps_library_when_undone(self):
        dialog = self.editor()
        original_rooms = deepcopy(dialog.draft.data["rooms"])
        self.assertTrue(dialog.place_room_drop(12, 5, 4, 4))
        enlarged_rooms = deepcopy(dialog.draft.data["rooms"])
        self.assertEqual(len(dialog.draft._undo), 1)
        with patch("pixelheart_core.interior_furniture.discover_furniture_libraries", return_value=[self.library]):
            QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(dialog.draft._undo), 1)
        dialog.undo()
        self.assertEqual(dialog.draft.data["rooms"], original_rooms)
        self.assert_connected(dialog)
        self.assertFalse(dialog.undo_button.isEnabled())
        dialog.redo()
        self.assertEqual(dialog.draft.data["rooms"], enlarged_rooms)
        self.assert_connected(dialog)

    def test_refresh_updates_history_infrastructure_without_discarding_user_redo(self):
        self.remember()
        dialog = self.editor()
        self.place_chair(dialog)
        furnished = deepcopy(dialog.draft.data["furniture"])
        self.choose_slate(dialog)
        chosen_finish = deepcopy(dialog.draft.data["room_styles"])
        dialog.undo()
        undo_count, redo_count = len(dialog.draft._undo), len(dialog.draft._redo)
        current_finish = deepcopy(dialog.draft.data.get("room_styles", {}))
        bundle = self.bundle()
        bundle["definitions"].append({**bundle["definitions"][0], "id": "(F)Test.NewChair", "name": "New chair"})
        self.write_bundle(bundle)
        QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
        self.assertEqual((len(dialog.draft._undo), len(dialog.draft._redo)), (undo_count, redo_count))
        self.assertEqual(dialog.draft.data.get("room_styles", {}), current_finish)
        self.assertEqual(dialog.draft.data["furniture"], furnished)
        self.assertIn("(F)Test.NewChair", {item["id"] for item in dialog.draft.data["catalog"]})
        dialog.redo()
        self.assertEqual(dialog.draft.data["room_styles"], chosen_finish)
        self.assertEqual(dialog.draft.data["furniture"], furnished)
        self.assertIn("(F)Test.NewChair", {item["id"] for item in dialog.draft.data["catalog"]})
        dialog.undo()
        dialog.undo()
        self.assertEqual(dialog.draft.data["furniture"], [])
        self.assert_connected(dialog)
        self.assertIn("(F)Test.NewChair", {item["id"] for item in dialog.draft.data["catalog"]})
        self.assertFalse(dialog.undo_button.isEnabled())

    def test_failed_refresh_of_historical_snapshot_keeps_current_and_both_histories(self):
        from pixelheart_core.interiors import normalize_interior
        self.remember()
        dialog = self.editor()
        # The original one-tile-wide chair fits at the room's right edge. It
        # exists only in undo history when its refreshed width becomes two.
        self.assertTrue(dialog.place_furniture_once("(F)Test.Chair", 11, 7, 0))
        dialog.remove_selected()
        self.choose_slate(dialog)
        dialog.undo()
        before = deepcopy((dialog.draft.data, dialog.draft._undo, dialog.draft._redo,
                           dialog._room_offsets, dialog._offsets_undo, dialog._offsets_redo))
        bundle = self.bundle()
        bundle["definitions"][0]["footprint"] = [2, 2]
        self.write_bundle(bundle)
        current_with_new_size = dialog.draft.snapshot()
        current_with_new_size["catalog"][0]["footprint"] = [2, 2]
        self.assertEqual(normalize_interior(current_with_new_size)["furniture"], [])
        self.assertFalse(dialog.load_catalog(self.library))
        self.assertIn("footprint", dialog.status.text())
        self.assertEqual((dialog.draft.data, dialog.draft._undo, dialog.draft._redo,
                          dialog._room_offsets, dialog._offsets_undo, dialog._offsets_redo), before)
        self.assertTrue(dialog.redo_button.isEnabled())
        self.assert_connected(dialog)

    def test_deliberate_custom_tilesheet_replacement_remains_undoable(self):
        self.remember()
        dialog = self.editor()
        connected = dialog.draft.snapshot()
        custom = self.root / "custom.png"
        with Image.new("RGBA", (32, 16), "#758294") as image:
            image.save(custom)
        dialog.load_atlas(custom)
        replaced = dialog.draft.snapshot()
        self.assertNotEqual(replaced["atlas"], connected["atlas"])
        self.assertNotIn("room_frame", replaced)
        self.assertEqual(len(dialog.draft._undo), 1)
        dialog.undo()
        self.assertEqual(dialog.draft.snapshot(), connected)
        self.assert_connected(dialog)
        self.assertFalse(dialog.undo_button.isEnabled())
        dialog.redo()
        self.assertEqual(dialog.draft.snapshot(), replaced)


if __name__ == "__main__":
    unittest.main()
