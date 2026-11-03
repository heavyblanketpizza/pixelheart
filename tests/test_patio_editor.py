"""Home → Spouse patio, with a synthetic game install and no Stardew files."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.game_connection import game_connection
from pixelheart.patio_editor import PatioEditor
from pixelheart_core.game_scene_assets import clear_game_cache
from pixelheart_core.projects import new_project, save_project
from tests.qt_support import QtTestCase
from tests.test_game_install import fake_game
from tests.test_spouse_patio import add_patio_maps


class PatioEditorTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        clear_game_cache()
        self.addCleanup(clear_game_cache)
        self.character = {**new_project()["character"], "name": "Mira", "romanceable": True}
        self.emitted = []

    def connect_game(self):
        game = fake_game(self.folder / "Stardew Valley")
        add_patio_maps(game / "Contents/Resources/Content/Maps")
        game_connection().set_folder(game)
        return game

    def editor(self, patio=None, project_file=None, **options):
        editor = PatioEditor(patio, project_file, self.character, **options)
        editor.changed.connect(self.emitted.append)
        editor.resize(1100, 800)
        self.addCleanup(editor.deleteLater)
        return editor

    def project(self):
        return save_project(new_project(), self.folder / "Mira" / "character.json")

    def test_without_the_game_you_can_start_empty_and_are_told_how_to_connect(self):
        editor = self.editor()
        self.assertFalse(editor.empty_page.isHidden())
        self.assertTrue(editor.farm_button.isHidden())
        self.assertTrue(editor.game_patio_button.isHidden())
        self.assertIn("Connect your game", editor.empty_game_note.text())
        self.assertEqual([editor.source.itemText(i) for i in range(editor.source.count())], ["Your tilesheet"])
        editor.empty_button.click()
        self.assertTrue(editor.empty_page.isHidden())
        self.assertEqual(self.emitted[-1]["stand"], [2, 3])
        self.assertIn("Cover all 16 ground tiles", editor.notice.text())
        editor.press(0, 0)
        editor.release()
        self.assertIn("Open or paint your tilesheet first", editor.notice.text())
        self.assertEqual(len(self.emitted), 1)

    def test_game_pieces_start_from_the_farm_and_each_stroke_is_one_change(self):
        self.connect_game()
        editor = self.editor()
        self.assertEqual([editor.source.itemText(i) for i in range(editor.source.count())],
                         ["Game patios", "Outdoors", "Your tilesheet"])
        editor.farm_button.click()
        patio = self.emitted[-1]
        self.assertEqual(patio["layers"]["Back"][0], ["spring_outdoorsTileSheet", 5])
        self.assertEqual(editor.notice.text(), "")
        self.assertEqual(editor.canvas.origin, (48, 48))
        editor.source.setCurrentIndex(1)
        editor.palette.select(1, 0, 2, 1)
        editor.set_layer("Buildings")
        editor.press(0, 0)
        editor.drag(0, 1)
        editor.drag(0, 1)
        editor.release()
        self.assertEqual(len(self.emitted), 2)
        buildings = self.emitted[-1]["layers"]["Buildings"]
        self.assertEqual((buildings[0], buildings[1], buildings[4], buildings[5]),
                         (["spring_outdoorsTileSheet", 1], ["spring_outdoorsTileSheet", 2],
                          ["spring_outdoorsTileSheet", 1], ["spring_outdoorsTileSheet", 2]))
        editor.set_tool("stand")
        editor.press(1, 1)
        editor.release()
        self.assertEqual(self.emitted[-1]["stand"], [1, 1])
        self.assertIn("standing spot", editor.notice.text())
        editor.set_tool("erase")
        editor.press(1, 1)
        editor.release()
        self.assertIsNone(self.emitted[-1]["layers"]["Buildings"][5])
        self.assertEqual(editor.notice.text(), "")

    def test_a_game_patio_brings_its_tiles_and_standing_spot(self):
        self.connect_game()
        editor = self.editor()
        editor.start_from_game_patio(index=1)
        self.assertEqual(self.emitted[-1]["stand"], [1, 2])
        self.assertEqual(list(self.emitted[-1]["sheets"]), ["patioTiles"])
        self.assertEqual(editor.canvas.stand, (1, 2))

    def test_the_canvas_maps_clicks_to_patio_cells_only(self):
        self.connect_game()
        editor = self.editor()
        editor.start_empty()
        canvas = editor.canvas
        scale = canvas.scale
        self.assertEqual(canvas.cell_at(QPointF(48 * scale + 1, 48 * scale + 1)), (0, 0))
        self.assertEqual(canvas.cell_at(QPointF((48 + 63) * scale, (48 + 63) * scale)), (3, 3))
        self.assertIsNone(canvas.cell_at(QPointF(2, 2)))

    def test_season_preview_uses_the_seasons_own_sheet(self):
        self.connect_game()
        editor = self.editor()
        editor.start_from_farm()
        editor.source.setCurrentIndex(editor.source.findText("Outdoors"))
        editor.season_choice.setCurrentIndex(editor.season_choice.findData("summer"))
        self.assertEqual(editor.texture("spring_outdoorsTileSheet").getpixel((16, 0)), (1, 9, 0, 255))
        self.assertEqual(editor.palette.image.getpixel((16, 0)), (1, 9, 0, 255))

    def test_your_own_tilesheet_is_copied_into_the_project_and_painted(self):
        project = self.project()
        sheet = self.folder / "tiles.png"
        Image.new("RGBA", (32, 16), (200, 30, 30, 255)).save(sheet)
        editor = self.editor(project_file=project)
        editor.start_empty()
        self.assertTrue(editor.open_own_sheet(str(sheet)))
        self.assertEqual(editor.source.currentText(), "Your tilesheet")
        editor.palette.select(1, 0)
        editor.set_layer("Back")
        editor.fill_layer()
        patio = self.emitted[-1]
        self.assertEqual(patio["layers"]["Back"], [["own", 1]] * 16)
        asset = patio["sheets"]["own"]["asset"]
        self.assertTrue((project.parent / asset).is_file())
        self.assertEqual(editor.notice.text(), "")
        self.assertEqual(editor.canvas.missing, set())

    def test_a_smaller_replacement_sheet_is_refused(self):
        project = self.project()
        wide, narrow = self.folder / "wide.png", self.folder / "narrow.png"
        Image.new("RGBA", (64, 16), (1, 2, 3, 255)).save(wide)
        Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(narrow)
        editor = self.editor(project_file=project)
        editor.start_empty()
        editor.open_own_sheet(str(wide))
        editor.palette.select(3, 0)
        editor.press(0, 0)
        editor.release()
        self.assertFalse(editor.open_own_sheet(str(narrow)))
        self.assertIn("fewer tiles", editor.notice.text())
        self.assertEqual(editor.own_sheet["columns"], 4)

    def test_an_unsaved_character_must_be_saved_before_adding_a_tilesheet(self):
        sheet = self.folder / "tiles.png"
        Image.new("RGBA", (16, 16)).save(sheet)
        editor = self.editor(ensure_saved=lambda: False)
        editor.start_empty()
        self.assertFalse(editor.open_own_sheet(str(sheet)))
        self.assertIn("Save your character first", editor.notice.text())

    def test_patio_animation_uses_their_sprite_frames(self):
        sprite = self.folder / "sprite.png"
        Image.new("RGBA", (64, 128), (10, 20, 30, 255)).save(sprite)
        editor = self.editor()
        editor.set_sprite(sprite)
        editor.start_empty()
        self.assertEqual(editor.sprite_frames.columns, 4)
        self.assertTrue(editor.no_sprite.isHidden())
        editor.activity.setCurrentIndex(editor.activity.findData("animate"))
        self.assertFalse(editor.animation_panel.isHidden())
        editor.sprite_frames.chosen.emit(5)
        editor.add_frame(6)
        self.assertEqual(self.emitted[-1]["animation"]["frames"], [[5, 100], [6, 100]])
        editor.frame_list.setCurrentRow(0)
        editor.duration.setValue(250)
        editor.offset_y.setValue(-4)
        self.assertEqual(self.emitted[-1]["animation"], {"frames": [[5, 250], [6, 100]], "offset": [0, -4]})
        editor.remove_frame()
        self.assertEqual(self.emitted[-1]["animation"]["frames"], [[6, 100]])
        editor.activity.setCurrentIndex(editor.activity.findData("stand"))
        self.assertEqual(self.emitted[-1]["animation"]["frames"], [])

    def test_an_existing_animation_opens_on_play_an_animation(self):
        patio = {"animation": {"frames": [[1, 100]], "offset": [0, 0]}}
        editor = self.editor(patio)
        self.assertEqual(editor.activity.currentData(), "animate")
        self.assertEqual(editor.frame_list.count(), 1)

    def test_removing_the_patio_reports_none(self):
        editor = self.editor({})
        self.assertTrue(editor.empty_page.isHidden())
        editor.remove_patio()
        self.assertIsNone(self.emitted[-1])
        self.assertFalse(editor.empty_page.isHidden())


class HomePatioTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=self.settings))
        self.enterContext(patch.dict(os.environ, {"PIXELHEART_LIBRARY": str(self.folder / "library")}))

    def window(self):
        from pixelheart.app import MainWindow, SECTION_INDEX
        document = new_project()
        document["character"].update(name="Mira", internal_name="Mira", romanceable=True)
        path = save_project(document, self.folder / "Mira" / "character.json")
        window = MainWindow(str(path))

        def close():
            window.dirty = False
            window.close()
            window.deleteLater()
        self.addCleanup(close)
        window.navigate(SECTION_INDEX["home"])
        return window

    def test_the_patio_tab_edits_the_world_with_project_undo(self):
        window = self.window()
        world = window.world
        self.assertEqual(world.room_switch.tabText(2), "Spouse patio (after marriage)")
        world.room_switch.setCurrentIndex(2)
        editor = world.patio_editor
        self.assertIsNotNone(editor)
        self.assertTrue(world.settings_button.isHidden())
        editor.start_empty()
        window.collect()
        self.assertEqual(window.document["world"]["spouse_patio"]["stand"], [2, 3])
        self.assertTrue(window.project_history.undo())
        self.assertNotIn("spouse_patio", window.document["world"])
        self.assertIsNotNone(world.patio_editor)
        self.assertIsNot(world.patio_editor, editor)
        self.assertFalse(world.patio_editor.empty_page.isHidden())
        world.room_switch.setCurrentIndex(0)
        self.assertIsNone(world.patio_editor)
        self.assertFalse(world.settings_button.isHidden())

    def test_patio_issues_open_the_patio_tab(self):
        window = self.window()
        world = window.world
        world.world["spouse_patio"] = {}
        world.open_issue("world.spouse_patio.layers.Back")
        self.assertEqual(world.room_switch.currentIndex(), 2)
        self.assertIsNotNone(world.patio_editor)
        self.assertTrue(world.patio_editor.empty_page.isHidden())


if __name__ == "__main__":
    unittest.main()
