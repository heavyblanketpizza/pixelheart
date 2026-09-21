"""Native map previews use local game art without making it a project asset."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.game_import import (
    LocalGameSourceWidget, LocalMapSourceWidget, game_source_directory,
    map_game_content_root, remember_game_source_directory,
)
from pixelheart.home_editor import HomeDialog
from pixelheart.stage_canvas import StageCanvas
from pixelheart.world_page import WorldPage
from pixelheart_core.projects import new_project
from pixelheart_core.world import new_location, new_world


class MapGameSourceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pixelheart-map-source-")))
        self.settings = QSettings(str(self.root / "preferences.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.widgets = []
        self.project = self.root / "character.json"
        self.project.write_text("{}")
        self.map = self.root / "home.tmx"
        layers = "".join(
            f'<layer name="{name}" width="12" height="12"><data encoding="csv">'
            + ",".join([str(gid)] * 144) + "</data></layer>"
            for name, gid in (("Back", 1), ("Buildings", 0), ("Front", 0))
        )
        self.map.write_text(
            '<map orientation="orthogonal" width="12" height="12" tilewidth="16" tileheight="16">'
            '<tileset firstgid="1" name="townInterior" tilewidth="16" tileheight="16" columns="1" tilecount="1">'
            '<image source="townInterior" width="16" height="16"/></tileset>' + layers + "</map>"
        )

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def source(self, name="source", color="#147e86"):
        root = self.root / name
        root.mkdir()
        Image.new("RGBA", (16, 16), color).save(root / "Maps_townInterior.png")
        return root

    def home(self):
        character = new_project()["character"]
        character.update(home_map="NativeHome", home_x=5, home_y=5)
        world = new_world()
        place = new_location()
        place.update(name="Native home", internal_name="NativeHome", map="home.tmx")
        world["locations"].append(place)
        return self.keep(HomeDialog(character, world, project_file=self.project))

    def test_source_is_shared_machine_preference_and_resolves_game_folder(self):
        game = self.root / "Game"
        exports = game / "patch export"
        exports.mkdir(parents=True)
        widget = self.keep(LocalMapSourceWidget())
        with patch("pixelheart.game_import.QFileDialog.getExistingDirectory", return_value=str(game)):
            widget.browse_button.click()
        self.assertEqual(game_source_directory(), str(game))
        self.assertEqual(map_game_content_root(), exports.resolve())
        template = self.keep(LocalGameSourceWidget("abigail", "dialogue"))
        self.assertEqual(template.directory(), str(game))

    def test_missing_art_retains_dimensions_bounds_and_actionable_error(self):
        dialog = self.home()
        self.assertEqual(dialog.canvas.map_size, (12, 12))
        self.assertTrue(dialog.canvas.background.isNull())
        self.assertIn("Maps/townInterior", dialog.preview_hint.text())
        self.assertIn("coordinate grid only", dialog.preview_hint.text())
        self.assertIn("Preview unavailable", dialog.map_status.text())
        dialog.canvas.move_selected(50, 60)
        self.assertEqual((dialog.home_x.value(), dialog.home_y.value()), (11, 11))
        self.assertEqual(dialog.game_source.commands.toPlainText(), 'patch export "Maps/townInterior" image')

    def test_browse_refreshes_existing_preview_without_changing_project_data(self):
        dialog = self.home()
        before_character, before_world = deepcopy(dialog.character), deepcopy(dialog.world)
        source = self.source()
        with patch("pixelheart.game_import.QFileDialog.getExistingDirectory", return_value=str(source)):
            dialog.game_source.browse_button.click()
        self.assertFalse(dialog.canvas.background.isNull())
        self.assertFalse(dialog.canvas.preview_error)
        self.assertEqual(dialog.canvas.background.pixelColor(0, 0).name(), "#147e86")
        self.assertEqual(dialog.character, before_character)
        self.assertEqual(dialog.world, before_world)
        self.assertEqual(dialog.changed_ids, set())
        self.assertEqual(self.project.read_text(), "{}")

    def test_source_path_change_invalidates_staging_cache(self):
        canvas = self.keep(StageCanvas())
        first = self.source("first", "#174978")
        second = self.source("second", "#875f25")
        remember_game_source_directory(first)
        canvas.set_map(self.map)
        self.assertEqual(canvas.background.pixelColor(0, 0).name(), "#174978")
        remember_game_source_directory(second)
        canvas.set_map(self.map)
        self.assertEqual(canvas.background.pixelColor(0, 0).name(), "#875f25")
        self.assertEqual(canvas.background_key, str(self.map))

    def test_refresh_retries_same_source_after_images_are_exported(self):
        source = self.root / "exports"
        source.mkdir()
        remember_game_source_directory(source)
        dialog = self.home()
        self.assertTrue(dialog.canvas.background.isNull())
        Image.new("RGBA", (16, 16), "#5d8689").save(source / "Maps_townInterior.png")
        dialog.game_source.refresh_button.click()
        self.assertFalse(dialog.canvas.background.isNull())
        self.assertNotIn("Preview unavailable", dialog.map_status.text())

    def test_unavailable_saved_folder_does_not_break_custom_tiles(self):
        self.map.write_text(self.map.read_text().replace('source="townInterior"', 'source="custom.png"'))
        Image.new("RGBA", (16, 16), "#996644").save(self.root / "custom.png")
        remember_game_source_directory(self.root / "unavailable")
        canvas = self.keep(StageCanvas())
        canvas.set_map(self.map)
        self.assertFalse(canvas.background.isNull())
        self.assertEqual(canvas.game_assets, [])

    def test_world_preview_uses_source_preference_and_refreshes_on_change(self):
        dialog = self.home()
        window = SimpleNamespace(document={"character": dialog.character}, project_file=self.project)
        page = self.keep(WorldPage(window))
        page.load(dialog.world)
        page.select_location(0)
        self.assertIn("Preview unavailable", page.map_preview.text())
        remember_game_source_directory(self.source())
        page.refresh_location()
        self.assertFalse(page.map_preview.pixmap().isNull())


if __name__ == "__main__":
    unittest.main()
