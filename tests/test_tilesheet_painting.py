"""Painting the creator's own tilesheets in the map painter and in Home's custom tile art."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.interior_editor import InteriorEditor, custom_tilesheet
from pixelheart.map_workshop import MapWorkshop
from pixelheart.pixel_painter import PixelPainterDialog
from pixelheart_core.interiors import new_interior
from pixelheart_core.pixel_layers import load_layers
from pixelheart_core.pixel_raster import brush_mask
from pixelheart_core.projects import new_project, save_project
from tests.qt_support import QtTestCase

RED = (220, 40, 40, 255)


def dot(document, x, y, color=RED):
    document.begin_edit("Pencil")
    document.paint(brush_mask([(x, y)], 1, (document.width, document.height)), color)
    document.commit_edit()


class PainterPatch:
    """Stand in for the user: act on the real painter window, then save or cancel."""

    def __init__(self, test):
        self.test, self.seen = test, []

    def run(self, target, action=None, *, save=True):
        def exec_(dialog):
            self.seen.append(dialog)
            if action is not None:
                action(dialog)
            if save:
                self.test.assertTrue(dialog.save(), dialog.notice.text())
                return QDialog.DialogCode.Accepted
            return QDialog.DialogCode.Rejected
        with patch.object(PixelPainterDialog, "exec", new=exec_):
            target()
        return self.seen[-1]


class MapTilesheetPaintingTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.project = self.root / "project" / "character.json"
        save_project(new_project(), self.project)
        self.workshop = MapWorkshop(self.project)
        self.painter = PainterPatch(self)

    def tearDown(self):
        self.workshop.deleteLater()
        self.app.processEvents()

    def sheet(self, size=(32, 32)):
        path = self.root / "tiles.png"
        Image.new("RGBA", size, (90, 140, 80, 255)).save(path)
        return path

    def test_a_new_tilesheet_can_be_painted_and_used(self):
        with patch("pixelheart.map_workshop.choose_tilesheet_size", return_value=(64, 32)):
            dialog = self.painter.run(self.workshop.paint_tilesheet, lambda dialog: dot(dialog.document, 17, 3))
        sheet = self.workshop.sheet
        self.assertEqual((sheet["width"], sheet["height"], sheet["tile_count"]), (64, 32, 8))
        self.assertEqual(sheet["bytes"], dialog.result_png)
        self.assertTrue(self.workshop.save_button.isEnabled())
        self.assertIsNotNone(load_layers(self.project.parent, dialog.result_png))

    def test_choosing_no_size_opens_nothing(self):
        with patch("pixelheart.map_workshop.choose_tilesheet_size", return_value=None), \
                patch.object(PixelPainterDialog, "exec") as opened:
            self.workshop.paint_tilesheet()
        opened.assert_not_called()
        self.assertIsNone(self.workshop.sheet)

    def test_a_loaded_tilesheet_is_repainted_in_place(self):
        self.workshop.load_tilesheet(self.sheet())
        self.painter.run(self.workshop.paint_tilesheet, lambda dialog: dot(dialog.document, 1, 1))
        with Image.open(io.BytesIO(self.workshop.sheet["bytes"])) as painted:
            self.assertEqual(painted.convert("RGBA").getpixel((1, 1)), RED)
        self.assertEqual(self.workshop.sheet["columns"], 2)

    def test_removing_tiles_the_map_uses_is_refused_inside_the_painter(self):
        self.workshop.load_tilesheet(self.sheet())
        before = self.workshop.sheet["bytes"]
        self.workshop.draft.paint("Back", 0, 0, 4)

        def remove_row(dialog):
            dialog.change_rows(-1)
            self.assertFalse(dialog.save())
            self.assertIn("fewer tiles", dialog.notice.text())
        self.painter.run(self.workshop.paint_tilesheet, remove_row, save=False)
        self.assertEqual(self.workshop.sheet["bytes"], before)

    def test_saved_place_reopens_with_tilesheet_layers(self):
        self.workshop.load_tilesheet(self.sheet())

        def layered(dialog):
            dialog.document.add_layer("Moss")
            dot(dialog.document, 2, 2)
        self.painter.run(self.workshop.paint_tilesheet, layered)
        self.workshop.fill_layer()
        reference = self.workshop.save_map()
        self.assertIsNotNone(reference, self.workshop.notice.text())
        reopened = MapWorkshop(self.project)
        self.addCleanup(reopened.deleteLater)
        reopened.load_map(reference)
        dialog = self.painter.run(reopened.paint_tilesheet, save=False)
        self.assertEqual([layer.name for layer in dialog.document.layers], ["Tilesheet", "Moss"])


class InteriorTilesheetPaintingTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.project = self.root / "project" / "character.json"
        self.sheet = self.root / "sheet.png"
        Image.new("RGBA", (64, 16), "#556677").save(self.sheet)
        self.dialog = InteriorEditor(self.project, new_interior(), "residence")
        self.painter = PainterPatch(self)

    def tearDown(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_custom_tilesheet_repaint_keeps_its_tile_choices(self):
        self.dialog.load_atlas(self.sheet)
        self.dialog.apply_tile(2)
        floor = self.dialog.draft.data["style"]["floor"]
        before = self.dialog.draft.data["atlas"]["asset"]
        self.painter.run(self.dialog.paint_atlas, lambda dialog: dot(dialog.document, 33, 1))
        atlas = self.dialog.draft.data["atlas"]
        self.assertNotEqual(atlas["asset"], before)
        self.assertEqual((atlas["columns"], atlas["tile_count"]), (4, 4))
        self.assertEqual(self.dialog.draft.data["style"]["floor"], floor)
        self.dialog.undo()
        self.assertEqual(self.dialog.draft.data["atlas"]["asset"], before, "one Home undo step")

    def test_rows_holding_used_tiles_cannot_be_removed(self):
        tall = self.root / "tall.png"
        Image.new("RGBA", (16, 32), "#556677").save(tall)
        self.dialog.load_atlas(tall)
        self.dialog.apply_tile(1)
        before = self.dialog.draft.data["atlas"]

        def remove_row(dialog):
            dialog.change_rows(-1)
            self.assertFalse(dialog.save())
            self.assertIn("outside the tilesheet", dialog.notice.text())
        self.painter.run(self.dialog.paint_atlas, remove_row, save=False)
        self.assertEqual(self.dialog.draft.data["atlas"], before)

    def test_new_custom_tilesheet_from_scratch(self):
        with patch("pixelheart.interior_editor.choose_tilesheet_size", return_value=(32, 32)):
            self.painter.run(self.dialog.paint_atlas, lambda dialog: dot(dialog.document, 0, 0))
        atlas = self.dialog.draft.data["atlas"]
        self.assertEqual((atlas["columns"], atlas["tile_count"]), (2, 4))

    def test_library_atlases_are_not_repainted(self):
        data = new_interior()
        data["atlas"] = {"asset": "world_assets/interiors/textures/a.png", "columns": 1, "tile_count": 1}
        self.assertTrue(custom_tilesheet(data))
        for key, value in (("surfaces", [{"id": "x"}]), ("architecture_catalog", [{"id": "y"}]), ("room_frame", {"a": 1})):
            with self.subTest(key=key):
                self.assertFalse(custom_tilesheet({**data, key: value}))
        self.assertFalse(custom_tilesheet(new_interior()))


if __name__ == "__main__":
    unittest.main()
