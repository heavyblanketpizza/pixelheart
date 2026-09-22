"""Windows are discoverable and remain wall furniture when dragged."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from tests.test_interior_decorating_flow import make_library
from tests import test_interior_furniture_drag as furniture_drag


WINDOW = "(F)Test.Window"
PORTHOLE = "(F)Test.Porthole"
BOARDED = "(F)Test.Boarded"
PAINTING = "(F)Test.Painting"
CHAIR = "(F)Test.Chair"


class InteriorWindowCatalogueTests(unittest.TestCase):
    # Reuse native Qt dispatch helpers without inheriting unrelated test cases.
    point = staticmethod(furniture_drag.InteriorFurnitureDragTests.point)
    state = staticmethod(furniture_drag.InteriorFurnitureDragTests.state)
    dispatch = furniture_drag.InteriorFurnitureDragTests.dispatch
    gesture = furniture_drag.InteriorFurnitureDragTests.gesture
    native_drop = furniture_drag.InteriorFurnitureDragTests.native_drop

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pixelheart-window-catalogue-")))
        self.library = make_library(self.root / "library")
        bundle = json.loads(self.library.read_text(encoding="utf-8"))
        prototype = deepcopy(bundle["definitions"][0])
        prototype.update(rotations=1, rotation_footprints={})
        prototype["frames"] = prototype["frames"][:1]
        bundle["definitions"] += [
            {**deepcopy(prototype), "id": identity, "name": name, "kind": kind}
            for identity, name, kind in (
                (WINDOW, "Small Window", "window"),
                (PORTHOLE, "Porthole", "window"),
                (BOARDED, "Boarded Window", "painting"),
                (PAINTING, "Seascape", "painting"),
            )
        ]
        self.library.write_text(json.dumps(bundle), encoding="utf-8")
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.dialogs = []

    def tearDown(self):
        for dialog in reversed(self.dialogs):
            dialog.reject()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def editor(self, kind="residence"):
        dialog = InteriorEditor(self.root / f"project-{len(self.dialogs)}" / "character.json", kind=kind,
                                resident_name="Test resident", allow_rebase=True)
        self.dialogs.append(dialog)
        self.assertTrue(dialog.load_catalog(self.library))
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.catalog_list.count(), 5)
        return dialog

    @staticmethod
    def visible_ids(dialog):
        return {dialog.catalog_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(dialog.catalog_list.count())}

    def test_windows_category_includes_porthole_and_boarded_window(self):
        dialog = self.editor()
        index = dialog.category.findData("windows")
        self.assertGreaterEqual(index, 0)
        self.assertEqual(dialog.category.itemText(index), "Windows")
        dialog.category.setCurrentIndex(index)
        self.assertEqual(self.visible_ids(dialog), {WINDOW, PORTHOLE, BOARDED})
        boarded = next(item for item in dialog.draft.data["catalog"] if item["id"] == BOARDED)
        self.assertEqual(boarded["kind"], "painting")

    def test_wall_decorations_still_include_windows_and_paintings(self):
        dialog = self.editor()
        dialog.category.setCurrentIndex(dialog.category.findData("wall"))
        self.assertEqual(self.visible_ids(dialog), {WINDOW, PORTHOLE, BOARDED, PAINTING})

    def test_window_search_includes_item_type_and_unnamed_windows(self):
        dialog = self.editor()
        dialog.search.setText("window")
        self.assertEqual(self.visible_ids(dialog), {WINDOW, PORTHOLE, BOARDED})
        dialog.search.setText(" CHAIR ")
        self.assertEqual(self.visible_ids(dialog), {CHAIR})

    def test_window_drag_accepts_wall_and_rejects_floor_without_losing_history(self):
        for kind, wall, floor, extra in (
            ("residence", (6, 2), (6, 7), (8, 2)),
            ("spouse", (1, 0), (1, 6), (3, 0)),
        ):
            with self.subTest(kind=kind):
                dialog = self.editor(kind)
                dialog.search.setText("Porthole")
                before = dialog.draft.snapshot()
                undo_count = len(dialog.draft._undo)
                self.gesture(dialog, lambda source, mime: self.native_drop(dialog, source, mime, *wall))
                placed = dialog.draft.data["furniture"][0]
                self.assertEqual((placed["item_id"], placed["x"], placed["y"]), (PORTHOLE, *wall))
                self.assertEqual(len(dialog.draft._undo), undo_count + 1)

                # Preserve populated undo and redo stacks across the bad drop.
                dialog.draft.place_furniture(PORTHOLE, *extra)
                dialog.undo()
                history = self.state(dialog)

                def reject_floor(source, mime):
                    enter = self.dispatch(QDragEnterEvent, dialog, source, mime, *floor)
                    self.assertTrue(enter.isAccepted())
                    move = self.dispatch(QDragMoveEvent, dialog, source, mime, *floor)
                    self.assertFalse(move.isAccepted())
                    self.assertFalse(dialog.canvas.ghost["valid"])
                    drop = self.dispatch(QDropEvent, dialog, source, mime, *floor)
                    self.assertFalse(drop.isAccepted())
                    return Qt.DropAction.IgnoreAction

                self.gesture(dialog, reject_floor)
                self.assertEqual(self.state(dialog), history)
                dialog.undo()
                self.assertEqual(dialog.draft.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
