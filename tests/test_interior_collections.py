"""Portable furniture collections retain authored activity placement guides."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interior_furniture import (
    FurnitureValidationError, import_furniture_library, interaction_layouts, validate_definition,
)
from pixelheart_core.interiors import new_interior, normalize_interior


def activity_piece():
    return {"id": "(F)Example.Desk", "name": "Reading desk", "kind": "other", "footprint": [1, 1],
            "rotations": 2, "dependency": "Example.Furniture",
            "collection": {"id": "Example.Study", "name": "The study"},
            "description": "A writing surface with a reading place.",
            "interaction_profiles": [{"id": "read", "name": "Quiet reading", "description": "Leave the approach clear.",
                "rotations": {
                    "0": {"approach": [0, 1], "seat": [1, 1],
                          "companions": [{"item_id": "Example.Chair", "offset": [1, 1], "rotation": 0}]},
                    "1": {"approach": [-1, 0], "seat": [-1, 1],
                          "companions": [{"item_id": "Example.Chair", "offset": [-1, 1], "rotation": 1}]},
                }}]}


class FurnitureCollectionValidationTests(unittest.TestCase):
    def test_metadata_roundtrip_is_detached_and_normalizes_companion_ids(self):
        original = activity_piece()
        definition = validate_definition(original)
        self.assertEqual(definition["interaction_profiles"][0]["rotations"]["0"]["companions"][0]["item_id"],
                         "(F)Example.Chair")
        self.assertEqual(validate_definition(json.loads(json.dumps(definition))), definition)
        definition["collection"]["name"] = "Another collection"
        definition["interaction_profiles"][0]["rotations"]["0"]["approach"][0] = 8
        self.assertEqual(original["collection"]["name"], "The study")
        self.assertEqual(original["interaction_profiles"][0]["rotations"]["0"]["approach"], [0, 1])

    def test_legacy_definitions_remain_without_optional_metadata(self):
        definition = validate_definition({"id": "Example.Legacy", "footprint": [1, 1]})
        self.assertNotIn("collection", definition)
        self.assertNotIn("interaction_profiles", definition)
        self.assertEqual(list(interaction_layouts(definition, 0)), [])

    def test_rejects_invalid_profile_layouts_and_bounded_metadata(self):
        alterations = [
            lambda d: d.update(collection="Study"),
            lambda d: d.update(description="x" * 1025),
            lambda d: d.update(interaction_profiles=[d["interaction_profiles"][0]] * 17),
            lambda d: d["interaction_profiles"].append(deepcopy(d["interaction_profiles"][0])),
            lambda d: d["interaction_profiles"][0].update(rotations={"2": {"approach": [0, 1]}}),
            lambda d: d["interaction_profiles"][0].update(rotations={}),
            lambda d: d["interaction_profiles"][0]["rotations"]["0"].update(approach=[17, 0]),
            lambda d: d["interaction_profiles"][0]["rotations"]["0"].update(seat=[False, 0]),
            lambda d: d["interaction_profiles"][0]["rotations"]["0"].update(companions=[{}] * 9),
            lambda d: d["interaction_profiles"][0]["rotations"]["0"]["companions"][0].update(item_id="(O)Example.Object"),
        ]
        for change in alterations:
            with self.subTest(change=alterations.index(change)):
                definition = activity_piece()
                change(definition)
                with self.assertRaises(FurnitureValidationError):
                    validate_definition(definition)

    def test_missing_rotation_has_no_inferred_layout(self):
        definition = activity_piece()
        del definition["interaction_profiles"][0]["rotations"]["1"]
        definition = validate_definition(definition)
        self.assertEqual(len(list(interaction_layouts(definition, 0))), 1)
        self.assertEqual(list(interaction_layouts(definition, 1)), [])

    def test_resolved_library_and_interior_preserve_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.json"
            library.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                          "definitions": [activity_piece()]}))
            imported = import_furniture_library(library, root / "project")
            design = new_interior()
            design["catalog"] = imported["definitions"]
            reopened = normalize_interior(json.loads(json.dumps(design)))
            self.assertEqual(reopened["catalog"], imported["definitions"])


class FurnitureCollectionEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        design = new_interior()
        design["catalog"] = [activity_piece(), {"id": "Example.Chair", "name": "Chair", "kind": "chair",
                                             "footprint": [1, 1], "rotations": 2}]
        self.editor = InteriorEditor(self.root / "character.json", design)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.reject()
        self.editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def definition(self):
        return next(d for d in self.editor.draft.data["catalog"] if d["id"] == "(F)Example.Desk")

    def test_collection_filter_description_and_search_combine(self):
        dialog = self.editor
        self.assertEqual(dialog.catalog_list.count(), 2)
        dialog.collection.setCurrentIndex(dialog.collection.findData("Example.Study"))
        self.assertEqual(dialog.catalog_list.count(), 1)
        dialog.catalog_list.setCurrentRow(0)
        self.assertIn("writing surface", dialog.catalog_details.text())
        self.assertIn("Quiet reading", dialog.catalog_details.text())
        dialog.search.setText("reading place")
        self.assertEqual(dialog.catalog_list.count(), 1)
        dialog.category.setCurrentIndex(dialog.category.findData("seating"))
        self.assertEqual(dialog.catalog_list.count(), 0)
        dialog.category.setCurrentIndex(0)
        dialog.collection.setCurrentIndex(0)
        dialog.search.setText("")
        self.assertEqual(dialog.catalog_list.count(), 2)

    def test_refresh_preserves_authored_metadata_in_current_and_history(self):
        dialog = self.editor
        dialog.draft.place_furniture("(F)Example.Desk", 6, 7)
        dialog.draft.move_furniture(dialog.draft.data["furniture"][0]["id"], 7, 7)
        dialog.draft.undo()
        history_counts = len(dialog.draft._undo), len(dialog.draft._redo)
        before = deepcopy(self.definition())
        for blanks in (False, True):
            observation = {"id": "Example.Desk", "name": "Updated desk", "footprint": [1, 1], "rotations": 2}
            if blanks:
                observation.update(dependency="", collection={}, interaction_profiles=[])
            source = self.root / "observed.json"
            source.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                          "definitions": [observation]}))
            self.assertTrue(dialog.load_catalog(source), dialog.status.text())
            for snapshot in [dialog.draft.data, *dialog.draft._undo, *dialog.draft._redo]:
                definition = next(d for d in snapshot["catalog"] if d["id"] == "(F)Example.Desk")
                for key in ("dependency", "collection", "description", "interaction_profiles"):
                    self.assertEqual(definition[key], before[key])
                self.assertEqual(definition["name"], "Updated desk")
            self.assertEqual((len(dialog.draft._undo), len(dialog.draft._redo)), history_counts)

    def test_preview_and_selected_guides_follow_move_rotation_and_companions(self):
        dialog = self.editor
        canvas = dialog.canvas
        before = dialog.draft.snapshot()
        canvas.set_placement(self.definition(), 0)
        canvas.cursor_tile = (6, 7)
        canvas._update_ghost()
        guides = canvas.interaction_overlays()
        self.assertEqual([(g["x"], g["y"]) for g in guides], [(6, 8), (7, 8), (7, 8)])
        self.assertFalse(guides[-1]["present"])
        canvas.set_placement(self.definition(), 1)
        self.assertEqual([(g["x"], g["y"]) for g in canvas.interaction_overlays()], [(5, 7), (5, 8), (5, 8)])
        self.assertEqual(dialog.draft.snapshot(), before)
        canvas.clear_placement()
        identity = dialog.draft.place_furniture("(F)Example.Desk", 6, 7)
        dialog.draft.place_furniture("(F)Example.Chair", 7, 8)
        dialog.select_furniture(identity)
        self.assertTrue(canvas.interaction_overlays()[-1]["present"])
        placed = deepcopy(dialog.draft.data["furniture"])
        canvas.drag = (identity, 6, 7, 6, 7)
        canvas.cursor_tile = (8, 7)
        canvas._update_ghost()
        self.assertEqual((canvas.interaction_overlays()[0]["x"], canvas.interaction_overlays()[0]["y"]), (8, 8))
        self.assertEqual(dialog.draft.data["furniture"], placed)
        canvas.drag = None
        canvas.ghost = None
        dialog.draft.move_furniture(identity, 8, 7)
        self.assertEqual(canvas.interaction_overlays()[0]["x"], 8)
        self.assertFalse(canvas.interaction_overlays()[-1]["present"])
        dialog.draft.rotate_furniture(identity)
        self.assertEqual((canvas.interaction_overlays()[0]["x"], canvas.interaction_overlays()[0]["y"]), (7, 7))
        canvas.grab()  # Exercise the actual painter with the current guides.


if __name__ == "__main__":
    unittest.main()
