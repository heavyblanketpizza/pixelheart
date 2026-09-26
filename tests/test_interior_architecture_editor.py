"""The architectural catalogue creates static pieces through real Qt editing."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interiors import new_interior, reachable_tiles
from tests.qt_support import QtTestCase

try:
    from .test_interior_architecture_canvas import architecture_design
except ImportError:
    from test_interior_architecture_canvas import architecture_design


class ArchitectureEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library_root = self.root / "library"
        self.library_root.mkdir()
        definitions = architecture_design(self.library_root)["architecture_catalog"]
        bundle = {"format": "pixelheart-interior-library", "version": 1, "definitions": [],
                  "architecture": [{**piece, "preview_asset": "fixture.png", "columns": 2,
                                    "tile_count": 2} for piece in definitions]}
        self.library = self.library_root / "library.json"
        self.library.write_text(json.dumps(bundle), encoding="utf-8")
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.source = new_interior()
        self.source_before = deepcopy(self.source)
        self.project = self.root / "project" / "character.json"
        self.editor = InteriorEditor(self.project, self.source)
        self.editors = [self.editor]
        self.editor.show()
        self.app.processEvents()
        self.assertTrue(self.editor.load_catalog(self.library), self.editor.status.text())
        self.editor.tabs.setCurrentIndex(2)
        self.editor.layout_mode.setCurrentIndex(self.editor.layout_mode.findData("architecture"))
        self.app.processEvents()

    def tearDown(self):
        self.app.processEvents()
        for editor in self.editors:
            editor.reject()
            editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def point(self, x, y):
        cell = self.editor.canvas.scale * 16
        return QPoint(x*cell + cell//2, y*cell + cell//2)

    def move(self, point, held=False):
        self.app.sendEvent(self.editor.canvas, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(point),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier))

    def pick(self, identity="fixture"):
        catalogue = self.editor.architecture_panel.catalog
        item = next(catalogue.item(row) for row in range(catalogue.count())
                    if catalogue.item(row).data(Qt.ItemDataRole.UserRole) == identity)
        QTest.mouseClick(catalogue.viewport(), Qt.MouseButton.LeftButton,
                         pos=catalogue.visualItemRect(item).center())
        self.assertEqual(self.editor.canvas.tool, "architecture-place")
        self.assertIsNotNone(self.editor.canvas._architecture_placement)

    def place(self, x=6, y=7):
        self.pick()
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(x, y))
        self.assertEqual(self.editor.canvas.tool, "architecture-select", self.editor.status.text())
        return self.editor.draft.data["architecture"][-1]["id"]

    def state(self):
        return (self.editor.draft.snapshot(), deepcopy(self.editor.draft._undo),
                deepcopy(self.editor.draft._redo), deepcopy(self.editor._room_offsets),
                deepcopy(self.editor._offsets_undo), deepcopy(self.editor._offsets_redo))

    def test_library_shows_picture_catalogue_separate_from_real_furniture(self):
        panel = self.editor.architecture_panel
        self.assertEqual(panel.catalog.count(), 2)
        self.assertFalse(panel.catalog.item(0).icon().isNull())
        self.assertFalse(panel.isHidden())
        self.assertTrue(self.editor.room_controls.isHidden())
        self.assertTrue(self.editor.wall_controls.isHidden())
        self.assertTrue(self.editor.rotate_button.isHidden())
        self.assertEqual(self.editor.draft.data["catalog"], [])
        self.assertEqual(len(self.editor.draft.data["architecture_catalog"]), 2)
        panel.search.setText("Counter")
        self.assertEqual(panel.catalog.count(), 1)
        self.assertEqual(panel.catalog.item(0).data(Qt.ItemDataRole.UserRole), "fixture")

    def test_click_catalogue_place_once_then_drag_updates_static_piece_with_history(self):
        history = len(self.editor.draft._undo)
        identity = self.place()
        self.assertEqual(len(self.editor.draft.data["architecture"]), 1)
        self.assertEqual(self.editor.selected_architecture, identity)
        self.assertIsNone(self.editor.canvas._architecture_placement)
        self.assertEqual(self.editor.draft.data["furniture"], [])
        self.assertEqual(len(self.editor.draft._undo), history + 1)
        placed = self.editor.draft.snapshot()
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(7, 8))
        self.move(self.point(9, 8), held=True)
        self.assertTrue(self.editor.canvas.preview_valid, self.editor.canvas.preview_message)
        self.assertEqual(self.editor.draft.snapshot(), placed)
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 8))
        item = self.editor.draft.data["architecture"][0]
        self.assertEqual((item["x"], item["y"]), (8, 7))
        self.assertEqual(item["id"], identity)
        self.assertEqual(len(self.editor.draft._undo), history + 2)
        self.editor.undo()
        self.assertEqual(self.editor.draft.snapshot(), placed)
        self.editor.redo()
        self.assertEqual(self.editor.draft.data["architecture"][0]["x"], 8)

    def test_duplicate_and_remove_buttons_act_on_architecture_not_furniture(self):
        first = self.place()
        self.assertTrue(self.editor.duplicate_button.isEnabled())
        self.editor.duplicate_button.click()
        self.assertEqual(self.editor.canvas.tool, "architecture-place")
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
        pieces = self.editor.draft.data["architecture"]
        self.assertEqual(len(pieces), 2)
        self.assertNotEqual(pieces[0]["id"], pieces[1]["id"])
        self.assertEqual(pieces[0]["piece_id"], pieces[1]["piece_id"])
        self.editor.remove_button.click()
        self.assertEqual([piece["id"] for piece in self.editor.draft.data["architecture"]], [first])
        self.assertEqual(self.editor.draft.data["furniture"], [])
        self.editor.undo()
        self.assertEqual(len(self.editor.draft.data["architecture"]), 2)

    def test_escape_and_mode_changes_cancel_pending_placement_without_mutation(self):
        for action in ("escape", "floorplan", "furniture"):
            with self.subTest(action=action):
                self.editor.tabs.setCurrentIndex(2)
                self.editor.layout_mode.setCurrentIndex(self.editor.layout_mode.findData("architecture"))
                self.pick()
                before = self.state()
                QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
                if action == "escape":
                    QTest.keyClick(self.editor.canvas, Qt.Key.Key_Escape)
                elif action == "floorplan":
                    self.editor.layout_mode.setCurrentIndex(0)
                else:
                    self.editor.tabs.setCurrentIndex(0)
                QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
                self.assertEqual(self.state(), before)
                self.assertIsNone(self.editor.canvas._architecture_placement)
                self.assertIsNone(self.editor.canvas._architecture_preview)

    def test_invalid_piece_cannot_cover_externally_authored_destination(self):
        def protect(candidate, offsets):
            if (7, 8) not in reachable_tiles(candidate):
                raise ValueError("Keep the authored scene position clear.")
        self.editor.validate_layout = protect
        before = self.state()
        self.pick()
        self.move(self.point(6, 7))
        self.assertFalse(self.editor.canvas.preview_valid)
        self.assertIn("authored scene", self.editor.canvas.preview_message)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(self.state(), before)
        self.assertFalse(self.editor.place_architecture("fixture", 6, 7))
        self.assertIn("authored scene", self.editor.status.text())
        self.assertEqual(self.state(), before)

    def test_search_or_category_hiding_held_piece_cancels_placement(self):
        panel = self.editor.architecture_panel
        for filter_kind in ("search", "category"):
            with self.subTest(filter_kind=filter_kind):
                panel.search.clear()
                panel.category.setCurrentIndex(panel.category.findData("all"))
                self.pick()
                self.move(self.point(6, 7))
                self.assertIsNotNone(self.editor.canvas._architecture_preview)
                before = self.state()
                if filter_kind == "search":
                    panel.search.setText("wall fixture")
                else:
                    panel.category.setCurrentIndex(panel.category.findData("Wall details"))
                self.assertEqual(panel.catalog.count(), 1)
                self.assertEqual(panel.catalog.item(0).data(Qt.ItemDataRole.UserRole), "wall")
                self.assertEqual(self.editor.canvas.tool, "architecture-select")
                self.assertIsNone(self.editor.canvas._architecture_placement)
                self.assertIsNone(self.editor.canvas._architecture_preview)
                QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
                self.assertEqual(self.state(), before)

    def test_library_refresh_does_not_restore_a_piece_canceled_by_filtering(self):
        self.pick()
        self.editor.architecture_panel.search.setText("no matching piece")
        self.assertEqual(self.editor.architecture_panel.catalog.count(), 0)
        self.assertEqual(self.editor.canvas.tool, "architecture-select")
        self.assertTrue(self.editor.load_catalog(self.library), self.editor.status.text())
        before_click = self.state()
        self.assertEqual(self.editor.architecture_panel.catalog.count(), 0)
        self.assertEqual(self.editor.canvas.tool, "architecture-select")
        self.assertIsNone(self.editor.canvas._architecture_placement)
        QTest.mouseClick(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.assertEqual(self.state(), before_click)

    def test_undo_during_piece_drag_clears_preview_and_prevents_late_commit(self):
        self.place()
        QTest.mousePress(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(6, 7))
        self.move(self.point(9, 7), held=True)
        self.assertIsNotNone(self.editor.canvas._architecture_drag)
        self.editor.undo()
        after_undo = self.state()
        QTest.mouseRelease(self.editor.canvas, Qt.MouseButton.LeftButton, pos=self.point(9, 7))
        self.assertEqual(self.state(), after_undo)
        self.assertEqual(self.editor.draft.data.get("architecture", []), [])
        self.assertIsNone(self.editor.canvas._architecture_preview)

    def test_cancel_discards_pieces_and_keeps_imported_artwork_staged(self):
        self.place()
        self.assertFalse(self.project.parent.exists())
        self.editor.reject()
        self.assertIsNone(self.editor.result_design)
        self.assertFalse(self.project.parent.exists())
        self.assertEqual(self.source, self.source_before)

    def test_save_preserves_architecture_and_reopens_with_its_local_artwork(self):
        self.place()
        before = self.editor.draft.snapshot()
        self.editor.save_design()
        self.assertEqual(self.editor.result_design, before)
        reference = before["atlas"]["asset"]
        self.assertTrue((self.project.parent / reference).is_file())
        reopened = InteriorEditor(self.project, self.editor.result_design)
        self.editors.append(reopened)
        self.assertEqual(reopened.draft.data["architecture"], before["architecture"])
        self.assertEqual(reopened.architecture_panel.catalog.count(), 2)
        self.assertFalse(reopened.architecture_panel.catalog.item(0).icon().isNull())


if __name__ == "__main__":
    unittest.main()
