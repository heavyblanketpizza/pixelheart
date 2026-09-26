"""Exercise the native Home catalogue without writing room or game data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.catalogue_workshop import CatalogueWorkshop
from pixelheart_core.projects import new_project
from tests.test_catalogue_development import make_pack
from tests.qt_support import QtTestCase


class CatalogueWorkshopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path, self.data = make_pack(self.root / "projects" / "example" / "development")
        desk = deepcopy(self.data["items"][0])
        desk.update(id="Example.Desk", slug="desk", name="Writing desk", group="Study")
        desk["vanilla"]["name"] = "Wizard Study"
        self.data["items"].append(desk)
        self.path.write_text(json.dumps(self.data))
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.page = CatalogueWorkshop()
        self.page._workspace_root = self.root
        self.page.resize(1100, 720)
        self.page.show()
        self.page.refresh()
        self.app.processEvents()

    def tearDown(self):
        self.page.stop()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def test_discovers_native_pack_and_searches_vanilla_counterpart(self):
        self.assertEqual(self.page.pieces.count(), 2)
        self.assertEqual(self.page.background.currentData(), "room")
        self.assertTrue(self.page.play_button.isEnabled())
        self.page.search.setText("Wizard")
        self.assertEqual(self.page.pieces.count(), 1)
        self.assertEqual(self.page.current_item["slug"], "desk")
        self.assertEqual(self.page.names[1].text(), "Wizard Study")

    def test_click_animates_both_globes_and_hiding_stops_both(self):
        QTest.mouseClick(self.page.canvases[0], Qt.MouseButton.LeftButton)
        self.assertTrue(all(c.is_playing for c in self.page.canvases))
        for canvas in self.page.canvases:
            self.assertEqual(canvas.action, "walk")
            canvas.advance(800)
            self.assertGreater(canvas.elapsed_ms, 0)
        self.page.hide()
        self.assertFalse(any(c.is_playing for c in self.page.canvases))
        self.page.show()
        self.page.farmer.setChecked(False)
        self.assertFalse(self.page.play_button.isEnabled())

    def test_failed_reload_keeps_current_preview_and_explains_error(self):
        previous = self.page.pack
        self.path.write_text("invalid")
        self.page.reload()
        self.assertIs(self.page.pack, previous)
        self.assertEqual(self.page.pieces.count(), 2)
        self.assertIn("Cannot read", self.page.notice.text())

    def test_empty_filter_disables_replay_and_clearing_restores_it(self):
        self.page.play()
        self.page.search.setText("no matching piece")
        self.assertEqual(self.page.pieces.count(), 0)
        self.assertFalse(self.page.play_button.isEnabled())
        self.assertFalse(any(c.is_playing for c in self.page.canvases))
        self.page.farmer.setChecked(False)
        self.page.farmer.setChecked(True)
        self.assertFalse(self.page.play_button.isEnabled())
        self.assertIsNone(self.page.current_item)
        self.page.search.clear()
        self.assertTrue(self.page.play_button.isEnabled())

    def test_home_catalogue_switch_keeps_rooms_and_project_clean(self):
        window = MainWindow(auto_download_icons=False)
        try:
            document = new_project()
            window.load_document(document)
            window.world.catalogue_workshop._workspace_root = self.root
            window.open_section("home")
            before = window.world.dump()
            window.world.home_switch.setCurrentIndex(1)
            self.assertIsNone(window.world.interior_editor)
            self.assertEqual(window.world.catalogue_workshop.pieces.count(), 2)
            self.assertEqual(window.world.dump(), before)
            self.assertFalse(window.dirty)
            window.world.home_switch.setCurrentIndex(0)
            self.assertIsNotNone(window.world.interior_editor)
            self.assertEqual(window.world.room_switch.count(), 2)
            self.assertEqual(window.world.dump(), before)
            self.assertFalse(window.dirty)
            editor = window.world.interior_editor
            editor.load_atlas(self.path.parent / "piece.png")
            editor.apply_tile(1)
            window.world.home_switch.setCurrentIndex(1)
            self.assertTrue(window.dirty)
            self.assertEqual(window.world.dump()["locations"][0]["interior"]["style"]["floor"], 1)
            window.world.home_switch.setCurrentIndex(0)
            self.assertEqual(window.world.interior_editor.draft.data["style"]["floor"], 1)
            # Missing staged artwork blocks leaving the editor, including this tab.
            editor = window.world.interior_editor
            editor.apply_tile(0)
            (editor.stage_root / editor.draft.data["atlas"]["asset"]).unlink()
            window.world.home_switch.setCurrentIndex(1)
            self.assertEqual(window.world.home_switch.currentIndex(), 0)
            self.assertIs(window.world.interior_editor, editor)
        finally:
            window.world.reset_workspace()
            window.dirty = False
            window.close()
            window.deleteLater()

    def test_day_night_and_power_control_both_previews_and_survive_item_changes(self):
        first = self.data["items"][0]
        for key in ("collection", "vanilla"):
            view = first[key]["views"][0]
            view["states"] = {state: {"image": "piece.png"} for state in ("day_off", "day_on", "night_off", "night_on")}
            view["states"]["night_on"]["animation_frames"] = [
                {"image": "piece.png", "duration_ms": 100}, {"image": "farmer.png", "duration_ms": 100}]
            view["lights"] = [{"offset": [8, 0], "radius": 32, "color": "#ffdd99", "intensity": 1,
                               "when": "always", "requires_power": True}]
        self.path.write_text(json.dumps(self.data))
        self.assertTrue(self.page.load_pack(self.path))
        self.page.time_of_day.setCurrentIndex(1)
        self.page.power.setChecked(True)
        self.page.farmer.setChecked(False)
        for canvas in self.page.canvases:
            self.assertTrue(canvas.sample_effects(0)["lights"])
            self.assertFalse(canvas.is_playing)
        self.page.pieces.setCurrentRow(1)
        self.page.pieces.setCurrentRow(0)
        self.page.background.setCurrentIndex(0)
        self.assertEqual(self.page.time_of_day.currentData(), "night")
        self.assertTrue(self.page.power.isChecked())
        self.assertTrue(all(c.sample_effects(0)["lights"] for c in self.page.canvases))
        self.page.power.setChecked(False)
        self.assertFalse(any(c.sample_effects(0)["lights"] for c in self.page.canvases))

    def test_environment_changes_keep_the_farmer_at_the_current_animation_position(self):
        self.page.play()
        for canvas in self.page.canvases:
            canvas.advance(400)
        positions = [c.sample(c.elapsed_ms)["position"] for c in self.page.canvases]
        self.page.time_of_day.setCurrentIndex(1)
        self.page.power.setChecked(True)
        self.assertEqual([c.sample(c.elapsed_ms)["position"] for c in self.page.canvases], positions)
        self.assertTrue(all(c.is_playing for c in self.page.canvases))


if __name__ == "__main__":
    unittest.main()
