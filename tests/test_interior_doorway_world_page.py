"""Accepting a designed doorway synchronizes its structural exit with the place."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog

from pixelheart_core.interiors import ensure_doorway, new_interior, place_doorway
from tests import test_interior_world_page as world_fixture


class DoorwayWorldPageTests(unittest.TestCase):
    setUp = world_fixture.InteriorWorldPageTests.setUp
    tearDown = world_fixture.InteriorWorldPageTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def residence(self):
        self.page.add_location()
        record = self.page.world["locations"][0]
        record.update(internal_name="TheirHome", interior=new_interior(), entry_x=4, entry_y=10)
        self.page.select_location(0)
        self.page.assign_home()

    def test_accepted_doorway_sets_real_exit_and_preserves_explicit_npc_home(self):
        self.residence()
        self.window.document["character"].update(home_x=6, home_y=9)
        self.window.identity.load(self.window.document["character"])
        design = place_doorway(new_interior(), 7, 12)
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            constructor.return_value.result_design = design
            self.assertTrue(self.page.design_interior())
        self.assertFalse(self.window.errors)
        record = self.page.world["locations"][0]
        self.assertEqual((record["entry_x"], record["entry_y"]), (7, 11))
        self.assertEqual((record["exit_x"], record["exit_y"]), (7, 13))
        self.assertEqual((self.window.document["character"]["home_x"],
                          self.window.document["character"]["home_y"]), (6, 9))

    def test_cancel_keeps_legacy_interior_and_exit_metadata_unchanged(self):
        self.residence()
        before = deepcopy(self.page.dump())
        with patch("pixelheart.interior_editor.InteriorEditor") as constructor:
            constructor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            constructor.return_value.result_design = ensure_doorway(new_interior())
            self.assertFalse(self.page.design_interior())
        self.assertEqual(self.page.dump(), before)


if __name__ == "__main__":
    unittest.main()
