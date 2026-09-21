"""World integration applies interior edits to the selected place atomically."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.editors import IdentityPage
from pixelheart.world_page import WorldPage
from pixelheart_core.interiors import new_interior, floor_cells
from pixelheart_core.projects import new_project, save_project, load_project

try:
    from .test_world import make_map
except ImportError:
    from test_world import make_map


class InteriorWorldPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        test = self

        class Window:
            def __init__(self):
                self.document = new_project()
                self.project_file = None
                self.errors = []
                self.identity = IdentityPage()
                self.identity.load(self.document["character"])

            def ensure_saved(self):
                if self.project_file is None:
                    self.project_file = test.root / "project" / "character.json"
                    self.document["world"] = test.page.dump()
                    save_project(self.document, self.project_file)
                    test.page.load(load_project(self.project_file)["world"])
                return True

            def show_error(self, title, message):
                self.errors.append((title, message))

        self.window = Window()
        self.page = WorldPage(self.window)
        self.page.load()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.window.identity.close()
        self.window.identity.deleteLater()
        self.app.processEvents()

    def two_locations(self):
        self.page.add_location()
        self.page.add_location()
        self.page.world["locations"][0].update(name="First", internal_name="First")
        self.page.world["locations"][1].update(name="Second", internal_name="Second")
        self.page.select_location(1)
        return self.page.world["locations"][1]["id"]

    def test_first_save_keeps_selected_place_and_applies_residence_design(self):
        identity = self.two_locations()
        design = new_interior()
        spy = QSignalSpy(self.page.changed)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            dialog = constructor.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_design = deepcopy(design)
            self.page.design_interior()
            constructor.assert_called_once_with(self.window.project_file, None, "residence", self.page)
        self.assertFalse(self.window.errors)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertNotIn("interior", self.page.world["locations"][0])
        record = self.page.world["locations"][1]
        self.assertEqual(record["interior"], design)
        self.assertIsNone(record["map"])
        self.assertEqual([record["entry_x"], record["entry_y"]], design["entry"])
        self.assertIn((record["exit_x"], record["exit_y"]), floor_cells(design))
        self.assertNotEqual((record["exit_x"], record["exit_y"]), tuple(design["entry"]))
        self.assertEqual(spy.count(), 1)

    def test_cancelled_first_design_preserves_place_records(self):
        identity = self.two_locations()
        before = self.page.dump()
        spy = QSignalSpy(self.page.changed)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            constructor.return_value.result_design = new_interior()
            self.page.design_interior()
        self.assertEqual(self.page.dump(), before)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], identity)
        self.assertEqual(spy.count(), 0)

    def test_assign_home_updates_document_and_identity_form_together(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(internal_name="CustomResidence", entry_x=4, entry_y=10, interior=new_interior())
        self.page.select_location(0)
        spy = QSignalSpy(self.page.changed)
        self.page.assign_home()
        expected = {"home_map": "CustomResidence", "home_x": 4, "home_y": 10}
        for key, value in expected.items():
            self.assertEqual(self.window.document["character"][key], value)
            self.assertEqual(self.window.identity.dump()[key], value)
        self.assertEqual(spy.count(), 1)

    def test_spouse_design_uses_fixed_section_and_cannot_assign_separate_home(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(spouse_room=True, room_x=4, room_y=7)
        self.page.select_location(0)
        before_home = self.window.document["character"]["home_map"]
        design = new_interior("spouse")
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = design
            self.page.design_interior()
            constructor.assert_called_once_with(self.window.project_file, None, "spouse", self.page)
        record = self.page.world["locations"][0]
        self.assertEqual(record["interior"]["kind"], "spouse")
        self.assertEqual((record["interior"]["width"], record["interior"]["height"]), (6, 9))
        self.assertEqual((record["room_x"], record["room_y"]), (0, 0))
        self.assertTrue(self.page.assign_home_button.isHidden())
        self.assertFalse(self.page.location_fields["spouse_room"].isEnabled())
        self.page.assign_home()
        self.assertEqual(self.window.document["character"]["home_map"], before_home)

    def test_importing_tmx_replaces_design_and_reenables_generic_map_editor(self):
        self.page.add_location()
        self.page.world["locations"][0]["interior"] = new_interior()
        self.page.select_location(0)
        source = make_map(self.root / "source")
        with patch("pixelheart.world_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.page.import_location()
        self.assertFalse(self.window.errors)
        record = self.page.world["locations"][0]
        self.assertNotIn("interior", record)
        self.assertTrue(record["map"])
        self.assertTrue(self.page.edit_map_button.isEnabled())
        self.assertTrue(self.page.location_fields["spouse_room"].isEnabled())

    def test_reopening_design_and_error_preserve_existing_state(self):
        self.page.add_location()
        self.page.world["locations"][0]["interior"] = new_interior()
        self.page.select_location(0)
        self.window.ensure_saved()
        before = self.page.dump()
        with patch("pixelheart.interior_editor.InteriorEditor", side_effect=ValueError("Invalid texture reference")):
            self.page.design_interior()
        self.assertEqual(self.page.dump(), before)
        self.assertIn("Invalid texture reference", self.window.errors[-1][1])


if __name__ == "__main__":
    unittest.main()
