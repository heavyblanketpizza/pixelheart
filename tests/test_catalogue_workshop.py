"""Exercise the native Home catalogue without writing room or game data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from pixelheart.app import MainWindow
from pixelheart.catalogue_workshop import CatalogueWorkshop
from pixelheart_core.catalogue_development import CataloguePackError, load_development_pack
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

    def _delete_button(self, item_id):
        for index in range(self.page.pieces.count()):
            row = self.page.pieces.item(index)
            item = row.data(Qt.ItemDataRole.UserRole)
            if item["id"] != item_id:
                continue
            widget = self.page.pieces.itemWidget(row)
            self.assertIsNotNone(widget, "Each catalogue row needs its own delete control")
            buttons = [child for child in widget.findChildren(QPushButton) if child.text() == "Delete"]
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].accessibleName(), f"Delete {item['name']} from catalogue")
            return buttons[0]
        self.fail(f"No visible catalogue row for {item_id}")

    def _visible_item_ids(self):
        return [self.page.pieces.item(index).data(Qt.ItemDataRole.UserRole)["id"]
                for index in range(self.page.pieces.count())]

    def test_row_delete_targets_unselected_piece_and_preserves_selection(self):
        self.data["items"][0]["group"] = "Display"
        self.path.write_text(json.dumps(self.data))
        self.page.reload()
        self.page.pieces.setCurrentRow(1)
        self.assertEqual(self.page.current_item["id"], "Example.Desk")
        assets = {path.name: path.read_bytes() for path in self.path.parent.iterdir()
                  if path.is_file() and path != self.path}
        with patch("pixelheart.catalogue_workshop.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes) as question:
            QTest.mouseClick(self._delete_button("Example.Globe"), Qt.MouseButton.LeftButton)
        self.app.processEvents()

        question.assert_called_once()
        args, kwargs = question.call_args
        self.assertIn("Example globe", args[2])
        default_button = kwargs.get("defaultButton", args[4] if len(args) > 4 else None)
        self.assertEqual(default_button, QMessageBox.StandardButton.No)
        self.assertEqual(self._visible_item_ids(), ["Example.Desk"])
        self.assertEqual(self.page.current_item["id"], "Example.Desk")
        self.assertEqual(self.page.title.text(), "Writing desk")
        self.assertEqual(self.page.count.text(), "1 of 1 pieces")
        self.assertEqual([self.page.group.itemData(i) for i in range(self.page.group.count())], ["", "Study"])
        saved = load_development_pack(self.path).data
        self.assertEqual([item["id"] for item in saved["items"]], ["Example.Desk"])
        self.assertEqual(assets, {name: (self.path.parent / name).read_bytes() for name in assets})

    def test_clicking_row_name_and_thumbnail_still_selects_the_piece(self):
        before = self.path.read_bytes()
        row = self.page.pieces.item(1)
        content = self.page.pieces.itemWidget(row)
        self.assertIsNotNone(content)
        labels = content.findChildren(QLabel)
        name = next(child for child in labels if child.text() == "Writing desk")
        thumbnail = next(child for child in labels if child.pixmap() and not child.pixmap().isNull())
        with patch("pixelheart.catalogue_workshop.QMessageBox.question") as question:
            for target in (name, thumbnail):
                with self.subTest(target="name" if target is name else "thumbnail"):
                    self.page.pieces.setCurrentRow(0)
                    self.app.processEvents()
                    # Send through the window so Qt performs normal child hit-testing.
                    position = target.mapTo(self.page, target.rect().center())
                    QTest.mouseClick(self.page.windowHandle(), Qt.MouseButton.LeftButton, pos=position)
                    self.app.processEvents()
                    self.assertEqual(self.page.current_item["id"], "Example.Desk")
                    self.assertEqual(self.page.title.text(), "Writing desk")
        question.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_cancelled_row_delete_keeps_manifest_selection_and_preview(self):
        before = self.path.read_bytes()
        previous_pack = self.page.pack
        previous_id = self.page.current_item["id"]
        previous_names = [name.text() for name in self.page.names]
        with patch("pixelheart.catalogue_workshop.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No), \
                patch("pixelheart.catalogue_workshop.delete_development_item") as delete:
            QTest.mouseClick(self._delete_button("Example.Desk"), Qt.MouseButton.LeftButton)
        self.app.processEvents()

        delete.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIs(self.page.pack, previous_pack)
        self.assertEqual(self._visible_item_ids(), ["Example.Globe", "Example.Desk"])
        self.assertEqual(self.page.current_item["id"], previous_id)
        self.assertEqual([name.text() for name in self.page.names], previous_names)
        self.assertTrue(self.page.play_button.isEnabled())

    def test_filtered_row_delete_retains_query_and_existing_group(self):
        gallery = deepcopy(self.data["items"][0])
        gallery.update(id="Example.Gallery", slug="gallery", name="Gallery globe", group="Gallery")
        self.data["items"].append(gallery)
        self.path.write_text(json.dumps(self.data))
        self.page.reload()
        self.page.group.setCurrentIndex(self.page.group.findData("Study"))
        self.page.search.setText("Writing")
        self.assertEqual(self._visible_item_ids(), ["Example.Desk"])
        with patch("pixelheart.catalogue_workshop.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(self._delete_button("Example.Desk"), Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(self.page.search.text(), "Writing")
        self.assertEqual(self.page.group.currentData(), "Study")
        self.assertEqual(self.page.count.text(), "0 of 2 pieces")
        self.assertEqual(self.page.title.text(), "No matching pieces")
        self.assertIsNone(self.page.current_item)
        self.assertFalse(self.page.play_button.isEnabled())
        self.assertEqual([item["id"] for item in load_development_pack(self.path).data["items"]],
                         ["Example.Globe", "Example.Gallery"])
        self.page.search.clear()
        self.assertEqual(self._visible_item_ids(), ["Example.Globe"])
        self.assertEqual(self.page.count.text(), "1 of 2 pieces")
        self.assertTrue(self.page.play_button.isEnabled())

    def test_deleting_final_piece_clears_preview_and_stays_empty_after_reload(self):
        self.data["items"] = self.data["items"][:1]
        self.path.write_text(json.dumps(self.data))
        self.page.reload()
        self.page.play()
        self.assertTrue(all(canvas.is_playing for canvas in self.page.canvases))
        with patch("pixelheart.catalogue_workshop.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(self._delete_button("Example.Globe"), Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(load_development_pack(self.path).data["items"], [])

        def assert_empty_preview():
            self.assertEqual(self.page.pieces.count(), 0)
            self.assertEqual(self.page.count.text(), "0 of 0 pieces")
            self.assertEqual(self.page.title.text(), "Catalogue is empty")
            self.assertIsNone(self.page.current_item)
            self.assertEqual(self.page.group.count(), 1)
            self.assertEqual(self.page.group.currentData(), "")
            self.assertEqual(self.page.match.text(), "")
            for labels in (self.page.names, self.page.dimensions, self.page.states):
                self.assertTrue(all(not label.text() for label in labels))
            self.assertTrue(all(control.count() == 0 for control in self.page.view_controls))
            self.assertTrue(all(canvas._image.isNull() for canvas in self.page.canvases))
            self.assertTrue(all(canvas.visible_bounds.isEmpty() for canvas in self.page.canvases))
            self.assertFalse(any(canvas.is_playing for canvas in self.page.canvases))
            self.assertFalse(self.page.play_button.isEnabled())

        assert_empty_preview()
        self.page.reload()
        assert_empty_preview()
        self.page.farmer.setChecked(False)
        self.page.farmer.setChecked(True)
        self.page.play()
        assert_empty_preview()

    def test_failed_row_delete_keeps_manifest_rows_and_current_preview(self):
        self.page.pieces.setCurrentRow(1)
        before = self.path.read_bytes()
        previous_pack = self.page.pack
        previous_names = [name.text() for name in self.page.names]
        previous_dimensions = [label.text() for label in self.page.dimensions]
        with patch("pixelheart.catalogue_workshop.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes), \
                patch("pixelheart.catalogue_workshop.delete_development_item",
                      side_effect=CataloguePackError("Cannot save catalogue: read-only destination")) as delete:
            QTest.mouseClick(self._delete_button("Example.Globe"), Qt.MouseButton.LeftButton)
        self.app.processEvents()

        delete.assert_called_once()
        self.assertIn("Cannot save catalogue: read-only destination", self.page.notice.text())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIs(self.page.pack, previous_pack)
        self.assertEqual(self._visible_item_ids(), ["Example.Globe", "Example.Desk"])
        self.assertEqual(self.page.current_item["id"], "Example.Desk")
        self.assertEqual(self.page.title.text(), "Writing desk")
        self.assertEqual(self.page.count.text(), "2 of 2 pieces")
        self.assertEqual([name.text() for name in self.page.names], previous_names)
        self.assertEqual([label.text() for label in self.page.dimensions], previous_dimensions)

    def test_discovers_native_pack_and_searches_vanilla_counterpart(self):
        self.assertEqual(self.page.pieces.count(), 2)
        self.assertEqual(self.page.background.currentData(), "room")
        self.assertTrue(self.page.play_button.isEnabled())
        self.page.search.setText("Wizard")
        self.assertEqual(self.page.pieces.count(), 1)
        self.assertEqual(self.page.current_item["slug"], "desk")
        self.assertEqual(self.page.names[1].text(), "Wizard Study")

    def test_catalogue_rows_keep_keyboard_name_search(self):
        self.page.pieces.setFocus()
        QTest.keyClicks(self.page.pieces, "w")
        self.assertEqual(self.page.current_item["id"], "Example.Desk")

    def test_surface_hides_reference_and_interaction_then_restores_furniture(self):
        surface=deepcopy(self.data["items"][0])
        surface.update(id="(WP)Example.Frost:0",slug="frost-wall",name="Frost wallpaper",group="Patterns",vanilla=None)
        surface["collection"].update(id=surface["id"],name=surface["name"],kind="wall")
        self.data["items"].append(surface)
        self.path.write_text(json.dumps(self.data))
        self.page.reload()
        self.page.search.setText("Frost wallpaper")
        self.assertEqual(self.page.pieces.count(),1)
        self.assertTrue(self.page.preview_panels[1].isHidden())
        self.assertFalse(self.page.play_button.isEnabled())
        self.assertFalse(self.page.farmer.isEnabled())
        self.assertFalse(self.page.power.isEnabled())
        self.page.update_background()
        self.page.farmer.setChecked(False)
        self.page.farmer.setChecked(True)
        self.assertFalse(self.page.play_button.isEnabled())
        self.page.search.setText("Wizard")
        self.assertFalse(self.page.preview_panels[1].isHidden())
        self.assertTrue(self.page.play_button.isEnabled())
        self.assertTrue(self.page.farmer.isEnabled())
        self.assertEqual(self.page.names[1].text(),"Wizard Study")

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

    def test_home_shows_the_catalogue_tool_only_when_there_are_packs(self):
        window = MainWindow(auto_download_icons=False)
        try:
            window.world.catalogue_workshop._workspace_root = self.root / "nowhere"
            window.load_document(new_project())
            self.assertTrue(window.world.home_switch.isHidden())
            self.assertEqual(window.world.home_switch.tabText(1), "Furniture catalogue · for mod makers")
            window.world.catalogue_workshop._workspace_root = self.root
            window.load_document(new_project())
            self.assertFalse(window.world.home_switch.isHidden())
        finally:
            window.world.reset_workspace()
            window.dirty = False
            window.close()
            window.deleteLater()

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

    def test_different_window_sizes_share_wall_height_across_views_and_backgrounds(self):
        for name, size, rectangle in (("tall-window.png", (32, 80), (4, 4, 27, 67)),
                                       ("medium-window.png", (16, 48), (2, 2, 13, 43)),
                                       ("short-window.png", (16, 32), (1, 11, 14, 29))):
            image = Image.new("RGBA", size)
            ImageDraw.Draw(image).rectangle(rectangle, fill="#145aaf")
            image.save(self.path.parent / name)
        item = self.data["items"][0]
        item["collection"] = {"id": "Test.TallWindow", "name": "Tall window", "kind": "window", "views": [
            {"label": "Tall", "rotation": 0, "image": "tall-window.png", "width": 32, "height": 80,
             "footprint": [2, 3]},
            {"label": "Medium", "rotation": 1, "image": "medium-window.png", "width": 16, "height": 48,
             "footprint": [1, 2]}]}
        item["vanilla"] = {"id": "Test.ShortWindow", "name": "Short window", "kind": "window", "views": [
            {"label": "Default", "rotation": 0, "image": "short-window.png", "width": 16, "height": 32,
             "footprint": [1, 2]}]}
        self.path.write_text(json.dumps(self.data))
        before = self.path.read_bytes()
        self.assertTrue(self.page.load_pack(self.path))

        def assert_shared_wall():
            shared = max(canvas.recommended_wall_height for canvas in self.page.canvases)
            for canvas in self.page.canvases:
                layout = canvas.scene_layout()
                self.assertEqual(layout["wall_height"], shared)
                visible = canvas.visible_bounds.translated(layout["image_position"])
                self.assertGreater(visible.top(), 0)
                self.assertLessEqual(visible.bottom(), shared - 8)
            return shared

        tall_height = assert_shared_wall()
        for background in (0, 1):
            self.page.background.setCurrentIndex(background)
            for time in (0, 1):
                self.page.time_of_day.setCurrentIndex(time)
                self.assertEqual(assert_shared_wall(), tall_height)
        self.page.view_controls[0].setCurrentIndex(1)
        medium_height = assert_shared_wall()
        self.assertLess(medium_height, tall_height)
        self.page.background.setCurrentIndex(0)
        self.assertEqual(assert_shared_wall(), medium_height)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
