"""Painting portraits and sprites from the Portraits & sprites page."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.app import MainWindow
from pixelheart.pixel_painter import PixelPainterDialog
from pixelheart.theme import apply_theme
from pixelheart_core.pixel_layers import load_layers
from pixelheart_core.pixel_raster import brush_mask
from pixelheart_core.projects import resolve_artwork
from tests.qt_support import QtTestCase

RED = (220, 40, 40, 255)
UPLOAD = (160, 90, 140, 255)


def dot(document, x, y, color=RED):
    document.begin_edit("Pencil")
    document.paint(brush_mask([(x, y)], 1, (document.width, document.height)), color)
    document.commit_edit()


class ArtworkPaintingTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-artwork-paint-")
        self.root = Path(self.temporary.name)
        self.file = self.root / "project" / "character.json"
        self.errors = self.enterContext(patch.object(MainWindow, "show_error"))
        self.window = MainWindow()
        self.assertTrue(self.window.save_to(self.file))
        self.page = self.window.artwork
        self.seen = []

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def upload(self, kind, size):
        source = self.root / f"{kind}.png"
        Image.new("RGBA", size, UPLOAD).save(source)
        with patch("pixelheart.artwork_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.page.upload(kind)
        self.errors.assert_not_called()

    def paint(self, kind, action=None, *, save=True):
        def run(dialog):
            self.seen.append(dialog)
            if action is not None:
                action(dialog)
            if save:
                self.assertTrue(dialog.save(), dialog.notice.text())
                return QDialog.DialogCode.Accepted
            return QDialog.DialogCode.Rejected
        with patch.object(PixelPainterDialog, "exec", new=run):
            self.page.paint(kind)
        return self.seen[-1]

    def record(self, kind, variant=None):
        artwork = self.window.document["artwork"]
        return (artwork["variants"][variant] if variant else artwork)[kind]

    def test_painting_an_upload_adds_a_selected_painted_version(self):
        self.upload("portrait", (128, 192))
        original = self.record("portrait")["original"]
        dialog = self.paint("portrait", lambda dialog: dot(dialog.document, 3, 4))
        record = self.record("portrait")
        self.assertEqual(record["original"], original, "the upload is kept")
        self.assertEqual(record["selected"], "painted")
        self.assertTrue(record["painted"].startswith("artwork/painted/portrait-"))
        path = resolve_artwork(self.window.document, self.file, "portrait")
        self.assertEqual(path.read_bytes(), dialog.result_png)
        with Image.open(path) as saved:
            self.assertEqual(saved.convert("RGBA").getpixel((3, 4)), RED)
            self.assertEqual(saved.convert("RGBA").getpixel((0, 0)), UPLOAD)
        select = self.page.cards["portrait"]["select"]
        self.assertEqual(select.currentData(), "painted")
        self.assertEqual([select.itemData(index) for index in range(select.count())], ["original", "painted"])
        self.assertTrue(self.window.dirty)

    def test_project_undo_returns_to_the_previous_version(self):
        self.upload("portrait", (128, 192))
        self.paint("portrait", lambda dialog: dot(dialog.document, 3, 4))
        self.window.project_history.undo()
        self.assertEqual(self.record("portrait")["selected"], "original")
        self.assertNotIn("painted", self.record("portrait"))

    def test_layers_come_back_when_painting_again(self):
        self.upload("sprite", (64, 416))

        def add_hair(dialog):
            dialog.document.add_layer("Hair")
            dot(dialog.document, 5, 5)
        first = self.paint("sprite", add_hair)
        self.assertIsNotNone(load_layers(self.file.parent, first.result_png))
        second = self.paint("sprite", save=False)
        self.assertEqual([layer.name for layer in second.document.layers], ["Sprite sheet", "Hair"])
        self.assertEqual(second.document.frame_size, (16, 32))

    def test_a_new_sheet_can_be_painted_from_scratch(self):
        dialog = self.paint("sprite", lambda dialog: dot(dialog.document, 1, 1))
        self.assertEqual((dialog.document.width, dialog.document.height), (64, 416), "romance frames included")
        record = self.record("sprite")
        self.assertEqual(set(record) - {"source_history"}, {"painted", "selected"})
        select = self.page.cards["sprite"]["select"]
        self.assertEqual([select.itemData(index) for index in range(select.count())], ["painted"])
        self.assertFalse(self.page.cards["sprite"]["prepare"].isEnabled())
        review = self.page.create_review("sprite")
        self.addCleanup(review.deleteLater)
        self.errors.assert_not_called()

    def test_cancelling_changes_nothing(self):
        self.upload("portrait", (128, 192))
        before = dict(self.record("portrait"))
        self.window.dirty = False
        self.paint("portrait", lambda dialog: dot(dialog.document, 1, 1), save=False)
        self.assertEqual(self.record("portrait"), before)
        self.assertFalse(self.window.dirty)

    def test_painting_an_inherited_appearance_starts_from_default_and_keeps_its_credit(self):
        self.upload("portrait", (128, 192))
        source = {"provider": "Your game", "attribution": "Portrait loaded from your game"}
        self.record("portrait")["source"] = source
        self.page.select_appearance("winter")
        dialog = self.paint("portrait", lambda dialog: dot(dialog.document, 2, 2))
        self.assertEqual(dialog.document.pixel_at(0, 0), UPLOAD)
        winter = self.record("portrait", "winter")
        self.assertEqual(winter["selected"], "painted")
        self.assertEqual(winter["source"], source)
        self.assertEqual(self.record("portrait")["selected"], "original", "Default is unchanged")

    def test_reference_sources_are_offered(self):
        self.upload("portrait", (128, 192))
        self.paint("portrait", lambda dialog: dot(dialog.document, 3, 4))
        dialog = self.paint("portrait", save=False)
        captions = [action.text() for action in dialog.reference_actions]
        self.assertIn("Reference: Original upload", captions)
        self.assertIn("Reference: A villager from my game…", captions)
        dialog.reference_actions[0].trigger()
        self.assertTrue(dialog.document.layers[-1].reference)
        self.assertEqual(dialog.document.layers[-1].image.getpixel((3, 4))[:3], UPLOAD[:3])

    def test_save_png_copy_protects_painted_files(self):
        self.upload("portrait", (128, 192))
        self.paint("portrait", lambda dialog: dot(dialog.document, 3, 4))
        painted = self.file.parent / self.record("portrait")["painted"]
        with patch("pixelheart.artwork_page.QFileDialog.getSaveFileName", return_value=(str(painted), "")):
            self.page.save_png_copy("portrait")
        self.errors.assert_called_once()
        self.assertIn("separate file", self.errors.call_args.args[1])

    def test_export_packs_the_painted_sheets_and_no_layer_files(self):
        def outline(dialog):
            dialog.document.add_layer("Hidden sketch")
            dot(dialog.document, 2, 2)
            dialog.document.update_layer(1, visible=False)
            dialog.document.set_active(0)
            dot(dialog.document, 1, 1)
        portrait = self.paint("portrait", outline)
        sprite = self.paint("sprite", lambda dialog: dot(dialog.document, 3, 3))
        self.window.identity.fields["name"].setText("Mira")
        self.window.identity.fields["internal_name"].setText("Mira")
        destination = self.root / "Mira pack"
        with patch("pixelheart.app.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                patch("pixelheart.app.QMessageBox.information"):
            self.assertTrue(self.window.export_project(), self.errors.call_args)
        with zipfile.ZipFile(destination.with_suffix(".zip")) as archive:
            prefix = "[CP] Mira/"
            self.assertEqual(archive.read(prefix + "assets/portraits.png"), portrait.result_png)
            self.assertEqual(archive.read(prefix + "assets/sprites.png"), sprite.result_png)
            self.assertFalse([name for name in archive.namelist() if name.endswith(".ora")])
            project = json.loads(archive.read(prefix + "project.json"))
            self.assertNotIn("painted", json.dumps(project["artwork"]), "the backup points at the packed sheet only")
        with Image.open(io.BytesIO(portrait.result_png)) as flattened:
            self.assertEqual(flattened.convert("RGBA").getpixel((2, 2))[3], 0, "hidden layers stay out")


if __name__ == "__main__":
    unittest.main()
