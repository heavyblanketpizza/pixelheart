"""A repaired connected library replaces schematic farmhouse art without restyling rooms."""
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
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from tests.test_interior_decorating_flow import make_library


class SpouseLibraryRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "project" / "character.json"
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.dialogs = []
        self.library = make_library(self.root / "library")
        bundle = json.loads(self.library.read_text(encoding="utf-8"))
        for source, identity, name, color, size in (
            (bundle["surfaces"][0], "(WP)Test.Plum", "Plum wallpaper", "#91688b", (16, 48)),
            (bundle["surfaces"][1], "(FL)Test.Slate", "Slate floor", "#698493", (32, 32)),
        ):
            reference = name.replace(" ", "-") + ".png"
            with Image.new("RGBA", size, color) as image:
                image.save(self.library.parent / reference)
            bundle["surfaces"].append({**source, "id": identity, "name": name, "preview_asset": reference})
        self.library.write_text(json.dumps(bundle), encoding="utf-8")

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def editor(self, saved=None):
        dialog = InteriorEditor(self.project, saved, "spouse")
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def decorated_without_context(self):
        dialog = self.editor()
        self.assertTrue(dialog.load_catalog(self.library))
        self.assertNotIn("spouse_context", dialog.draft.data)
        self.assertEqual(dialog.remembered_library(), str(self.library.resolve()))
        dialog.tabs.setCurrentIndex(1)
        for kind, identity in (("wall", "(WP)Test.Plum"), ("floor", "(FL)Test.Slate")):
            dialog.surface_kind.setCurrentIndex(dialog.surface_kind.findData(kind))
            item = next(dialog.surface_list.item(row) for row in range(dialog.surface_list.count())
                        if dialog.surface_list.item(row).data(Qt.ItemDataRole.UserRole) == identity)
            dialog.choose_surface(item)
            dialog.click_tile(2, 6)
        dialog.tabs.setCurrentIndex(0)
        self.assertTrue(dialog.place_furniture_once("(F)Test.Chair", 4, 6, 0))
        dialog.set_tool("spouse_stand")
        dialog.click_tile(2, 5)
        self.assertEqual(dialog.draft.data["room_styles"]["main"]["wall_pattern"]["surface_id"], "(WP)Test.Plum")
        self.assertEqual(dialog.draft.data["room_styles"]["main"]["floor_pattern"]["surface_id"], "(FL)Test.Slate")
        return dialog

    def repair_library_context(self):
        context = {"background_asset": "farmhouse-background.png", "foreground_asset": "farmhouse-foreground.png"}
        for key, color, rectangle in (
            ("background_asset", "#903020", (0, 0, 32, 160)),
            ("foreground_asset", "#206090", (32, 152, 128, 160)),
        ):
            with Image.new("RGBA", (144, 176), (0, 0, 0, 0)) as image:
                image.paste(color, rectangle)
                image.save(self.library.parent / context[key])
        bundle = json.loads(self.library.read_text(encoding="utf-8"))
        bundle["spouse_context"] = context
        self.library.write_text(json.dumps(bundle), encoding="utf-8")
        return {key: (self.library.parent / reference).read_bytes() for key, reference in context.items()}

    def assert_native_context(self, dialog, source_bytes):
        context = dialog.draft.data["spouse_context"]
        for key, payload in source_bytes.items():
            self.assertEqual((dialog.stage_root / context[key]).read_bytes(), payload)
        self.assertEqual(dialog.canvas._context_background.toImage().pixelColor(8, 80).name(), "#903020")
        self.assertEqual(dialog.canvas._context_foreground.toImage().pixelColor(64, 156).name(), "#206090")
        captured = dialog.canvas.grab().toImage()
        scale = dialog.canvas.scale
        self.assertEqual(captured.pixelColor(8 * scale, 80 * scale).name(), "#903020")
        self.assertEqual(captured.pixelColor(64 * scale, 156 * scale).name(), "#206090")

    def test_refresh_repaired_library_replaces_both_native_layers_and_preserves_edits(self):
        dialog = self.decorated_without_context()
        before = dialog.draft.snapshot()
        raw_preview = dialog.canvas.image.toImage()
        schematic_background = dialog.canvas._context_background.toImage()
        schematic_foreground = dialog.canvas._context_foreground.toImage()
        source_bytes = self.repair_library_context()
        self.assertEqual(dialog.library_button.text(), "Refresh game library…")
        QTest.mouseClick(dialog.library_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assert_native_context(dialog, source_bytes)
        self.assertNotEqual(dialog.canvas._context_background.toImage(), schematic_background)
        self.assertNotEqual(dialog.canvas._context_foreground.toImage(), schematic_foreground)
        self.assertEqual(dialog.canvas.image.toImage(), raw_preview)
        after = dialog.draft.snapshot()
        after.pop("spouse_context")
        self.assertEqual(after, before)

    def test_reopen_older_design_uses_repaired_remembered_context_without_restyling(self):
        original = self.decorated_without_context()
        original.save_design()
        saved = deepcopy(original.result_design)
        self.assertNotIn("spouse_context", saved)
        self.assertEqual(saved["spouse_stand"], [2, 5])
        source_bytes = self.repair_library_context()
        reopened = self.editor(saved)
        self.assert_native_context(reopened, source_bytes)
        after = reopened.draft.snapshot()
        after.pop("spouse_context")
        self.assertEqual(after, saved)
        self.assertNotIn("spouse_context", saved)


if __name__ == "__main__":
    unittest.main()
