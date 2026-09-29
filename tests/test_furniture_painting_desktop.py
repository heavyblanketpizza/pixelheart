"""Home: paint a placed piece of game furniture into a new piece for this home."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interior_furniture import import_texture, validate_definition
from pixelheart_core.interiors import InteriorDraft, new_interior
from pixelheart_core.painted_furniture import LOCAL_PREFIX
from pixelheart_core.pixel_layers import load_layers
from tests.qt_support import QtTestCase
from tests.test_painted_furniture import chair, game_sheet, mirrored
from tests.test_tilesheet_painting import PainterPatch, RED, dot


class FurniturePaintingDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        project = self.root / "project"
        project.mkdir()
        self.project_file = project / "character.json"
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        sheet = self.root / "sheet.png"
        mirrored(game_sheet()).save(sheet)
        reference = import_texture(sheet, project)
        design = new_interior("residence")
        design["catalog"] = [validate_definition(chair(preview_asset=reference)),
                             validate_definition(chair(id="(F)Test.Bed", name="Bed", kind="bed", preview_asset=reference))]
        draft = InteriorDraft(design)
        self.first = draft.place_furniture("(F)Test.Chair", 4, 6)
        self.second = draft.place_furniture("(F)Test.Chair", 6, 6)
        self.bed = draft.place_furniture("(F)Test.Bed", 8, 6)
        self.editor = InteriorEditor(self.project_file, draft.snapshot(), kind="residence", resident_name="Mira")
        self.editor.show()
        self.app.processEvents()
        self.painter = PainterPatch(self)

    def tearDown(self):
        self.editor.reject()
        self.editor.deleteLater()
        self.app.processEvents()

    def placement(self, identity):
        return next(item for item in self.editor.draft.data["furniture"] if item["id"] == identity)

    def paint(self, identity, x=0, y=0, color=RED):
        self.editor.select_furniture(identity)
        self.assertTrue(self.editor.paint_button.isEnabled(), self.editor.paint_button.toolTip())
        return self.painter.run(self.editor.paint_selected, lambda dialog: dot(dialog.document, x, y, color))

    def test_paint_makes_a_new_piece_for_the_selected_placement(self):
        dialog = self.paint(self.first)
        self.assertEqual(dialog.kind, "furniture")
        painted_id = self.placement(self.first)["item_id"]
        self.assertTrue(painted_id.startswith(LOCAL_PREFIX))
        self.assertEqual(self.placement(self.second)["item_id"], "(F)Test.Chair")
        catalog = {definition["id"]: definition for definition in self.editor.draft.data["catalog"]}
        self.assertIn("(F)Test.Chair", catalog)
        self.assertEqual(catalog[painted_id]["name"], "Painted Oak Chair")
        self.assertIsNotNone(load_layers(self.project_file.parent, dialog.result_png))
        self.assertEqual(self.editor.selection_label.text(), "Painted Oak Chair")
        self.assertTrue(self.editor.original_button.isEnabled())

    def test_one_undo_restores_the_original(self):
        self.paint(self.first)
        self.editor.undo()
        self.assertEqual(self.placement(self.first)["item_id"], "(F)Test.Chair")
        self.assertFalse(any(d["id"].startswith(LOCAL_PREFIX) for d in self.editor.draft.data["catalog"]))

    def test_repaint_replaces_all_placements_and_drops_the_old_piece(self):
        self.paint(self.first)
        old = self.placement(self.first)["item_id"]
        self.editor.select_furniture(self.second)
        self.editor.run_change(lambda: self.editor.apply_furniture_change(
            lambda trial: trial.data["furniture"].__setitem__(
                next(i for i, item in enumerate(trial.data["furniture"]) if item["id"] == self.second),
                {**self.placement(self.second), "item_id": old})))
        self.assertEqual(self.placement(self.second)["item_id"], old)
        self.paint(self.first, 1, 0, (1, 2, 3, 255))
        new = self.placement(self.first)["item_id"]
        self.assertNotEqual(new, old)
        self.assertEqual(self.placement(self.second)["item_id"], new)
        ids = [definition["id"] for definition in self.editor.draft.data["catalog"]]
        self.assertNotIn(old, ids)
        self.assertIn("(F)Test.Chair", ids)

    def test_same_painting_twice_reuses_the_piece(self):
        self.paint(self.first)
        painted = self.placement(self.first)["item_id"]
        self.paint(self.second)
        self.assertEqual(self.placement(self.second)["item_id"], painted)
        self.assertEqual(sum(d["id"] == painted for d in self.editor.draft.data["catalog"]), 1)

    def test_use_original_switches_back_and_forgets_an_unused_painting(self):
        self.paint(self.first)
        painted = self.placement(self.first)["item_id"]
        self.editor.use_original()
        self.assertEqual(self.placement(self.first)["item_id"], "(F)Test.Chair")
        self.assertNotIn(painted, [d["id"] for d in self.editor.draft.data["catalog"]])
        self.assertFalse(self.editor.original_button.isEnabled())

    def test_unpaintable_pieces_explain_why(self):
        self.editor.select_furniture(self.bed)
        self.assertFalse(self.editor.paint_button.isEnabled())
        self.assertIn("Beds", self.editor.paint_button.toolTip())
        self.editor.select_furniture("")
        self.assertFalse(self.editor.paint_button.isEnabled())
        self.assertTrue(self.editor.paint_button.isHidden())
        self.assertTrue(self.editor.original_button.isHidden())

    def test_the_animation_preview_says_what_it_does(self):
        self.assertEqual(self.editor.play.text(), "Animate")

    def test_seats_without_a_game_front_sheet_say_so(self):
        dialog = self.paint(self.first)
        self.assertIn("front", dialog.notice.text().casefold())

    def test_cancelling_the_painter_changes_nothing(self):
        before = self.editor.draft.snapshot()
        self.editor.select_furniture(self.first)
        self.painter.run(self.editor.paint_selected, save=False)
        self.assertEqual(self.editor.draft.snapshot(), before)
