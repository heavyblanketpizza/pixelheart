"""Preview time, lighting and playback stay separate from authored room edits."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor, FurnitureDetails
from pixelheart_core.interior_furniture import preview_frame, validate_definition
from pixelheart_core.interiors import InteriorDraft, render_interior
from tests.qt_support import QtTestCase


class InteriorPreviewControlsTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.project = self.root / "project" / "character.json"
        self.project.parent.mkdir()
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.dialogs = []
        with Image.new("RGBA", (64, 16), "#b02030") as image:
            image.paste("#20b030", (16, 0, 32, 16))
            image.paste("#2030b0", (32, 0, 48, 16))
            image.paste("#d0c020", (48, 0, 64, 16))
            image.save(self.project.parent / "effect.png")

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def editor(self, *, kind="residence", variants=True):
        frame = lambda x: {"rotation": 0, "rect": [x, 0, 16, 16], "duration_ms": 100}
        definition = {"id": "Test.PreviewLamp", "name": "Preview lamp", "kind": "lamp",
                      "footprint": [1, 1], "rotations": 1, "preview_asset": "effect.png",
                      "frames": [frame(0)],
                      "preview_lights": [{"rotation": 0, "offset": [.5, .5], "radius": 2,
                                          "color": "#ffd090", "intensity": .8, "when": "always"}]}
        if variants:
            definition["preview_variants"] = {"day_off": [frame(0)],
                                               "day_on": [frame(16), frame(48)],
                                               "night_off": [frame(32)],
                                               "night_on": [frame(48), frame(16)]}
        draft = InteriorDraft(kind=kind)
        design = draft.snapshot()
        design["catalog"] = [validate_definition(definition)]
        draft.apply(design)
        draft.place_furniture("(F)Test.PreviewLamp", *( (1, 6) if kind == "spouse" else (6, 7)))
        dialog = InteriorEditor(self.project, draft.snapshot(), kind)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    @staticmethod
    def choose(combo, value):
        combo.setCurrentIndex(combo.findData(value))

    def test_controls_are_available_for_both_rooms_and_default_to_day_auto(self):
        for kind in ("residence", "spouse"):
            with self.subTest(kind=kind):
                dialog = self.editor(kind=kind)
                self.assertTrue(dialog.preview_time.isVisible())
                self.assertTrue(dialog.preview_lights.isVisible())
                self.assertEqual([dialog.preview_time.itemText(i) for i in range(3)], ["Day", "Evening", "Night"])
                self.assertEqual([dialog.preview_lights.itemText(i) for i in range(3)], ["Auto", "On", "Off"])
                self.assertEqual(dialog.preview_options(), {"time_of_day": "day", "lights_on": False})
                self.assertEqual(dialog.timer.interval(), 75)

    def test_auto_and_manual_lighting_follow_time_without_editing_history(self):
        dialog = self.editor()
        original = dialog.draft.snapshot()
        history = deepcopy((dialog.draft._undo, dialog.draft._redo))
        cases = [("day", "auto", False), ("evening", "auto", True), ("night", "auto", True),
                 ("day", "on", True), ("night", "off", False)]
        for phase, mode, enabled in cases:
            with self.subTest(phase=phase, mode=mode):
                self.choose(dialog.preview_time, phase)
                self.choose(dialog.preview_lights, mode)
                self.assertEqual(dialog.preview_options(), {"time_of_day": phase, "lights_on": enabled})
                self.assertEqual((dialog.canvas.time_of_day, dialog.canvas.lights_on), (phase, enabled))
        self.assertEqual(dialog.draft.snapshot(), original)
        self.assertEqual((dialog.draft._undo, dialog.draft._redo), history)
        self.assertEqual(self.settings.allKeys(), [])

    def test_time_and_light_controls_change_rendered_pixels(self):
        dialog = self.editor()
        day = dialog.canvas.image.toImage()
        self.choose(dialog.preview_time, "night")
        night_on = dialog.canvas.image.toImage()
        self.choose(dialog.preview_lights, "off")
        night_off = dialog.canvas.image.toImage()
        self.assertNotEqual(day, night_on)
        self.assertNotEqual(night_on, night_off)
        self.assertNotEqual(day.pixelColor(6 * 16 + 8, 7 * 16 + 8),
                            night_off.pixelColor(6 * 16 + 8, 7 * 16 + 8))

    def test_light_toggle_changes_surrounding_floor_with_static_sprite(self):
        dialog = self.editor(variants=False)
        self.choose(dialog.preview_time, "night")
        self.choose(dialog.preview_lights, "off")
        dark = dialog.canvas.image.toImage()
        self.choose(dialog.preview_lights, "on")
        lit = dialog.canvas.image.toImage()
        self.assertNotEqual(dark.pixelColor(7 * 16 + 4, 7 * 16 + 8),
                            lit.pixelColor(7 * 16 + 4, 7 * 16 + 8))

    def test_paused_playback_keeps_frame_time_across_preview_changes(self):
        dialog = self.editor()
        self.choose(dialog.preview_lights, "on")
        with patch("pixelheart.interior_editor.time.monotonic", side_effect=[10., 10.125]):
            dialog.play.setChecked(True)
            dialog.advance_animation()
        dialog.play.setChecked(False)
        paused = dialog.canvas.image.toImage()
        self.assertEqual(dialog.elapsed_ms, 125)
        self.choose(dialog.preview_time, "night")
        self.choose(dialog.preview_lights, "off")
        self.choose(dialog.preview_time, "day")
        self.choose(dialog.preview_lights, "on")
        self.assertEqual(dialog.elapsed_ms, 125)
        self.assertEqual(dialog.canvas.elapsed_ms, 125)
        self.assertEqual(dialog.canvas.image.toImage(), paused)
        self.assertFalse(dialog.timer.isActive())
        with patch("pixelheart.interior_editor.time.monotonic", side_effect=[20., 20.25]):
            dialog.play.setChecked(True)
            dialog.advance_animation()
        dialog.play.setChecked(False)
        self.assertEqual(dialog.elapsed_ms, 375)

    def test_scene_move_background_and_ghost_share_environment_and_clock(self):
        dialog = self.editor()
        self.choose(dialog.preview_time, "evening")
        self.choose(dialog.preview_lights, "off")
        dialog.elapsed_ms = 175
        identity = dialog.draft.data["furniture"][0]["id"]
        dialog.canvas.drag = (identity, 6, 7, 6, 7)
        dialog.canvas.cursor_tile = (7, 7)
        with patch("pixelheart.interior_editor.render_interior", wraps=render_interior) as scene, \
                patch("pixelheart.interior_canvas.render_interior", wraps=render_interior) as background, \
                patch("pixelheart.interior_canvas.preview_frame", wraps=preview_frame) as ghost:
            dialog.render()
            dialog.canvas.grab()
        self.assertEqual(scene.call_args.kwargs["elapsed_ms"], 175)
        self.assertEqual(background.call_args.args[2], 175)
        self.assertEqual(ghost.call_args.args[3], 175)
        for call in (scene.call_args, background.call_args, ghost.call_args):
            self.assertEqual(call.kwargs["time_of_day"], "evening")
            self.assertFalse(call.kwargs["lights_on"])

    def test_catalogue_ghost_uses_selected_preview_environment(self):
        dialog = self.editor()
        self.choose(dialog.preview_time, "night")
        self.choose(dialog.preview_lights, "off")
        dialog.canvas.catalogue_drag = (dialog.draft.data["catalog"][0], 0)
        dialog.canvas.cursor_tile = (8, 8)
        dialog.canvas._update_ghost()
        with patch("pixelheart.interior_canvas.preview_frame", wraps=preview_frame) as ghost:
            dialog.canvas.grab()
        self.assertEqual(ghost.call_args.kwargs, {"time_of_day": "night", "lights_on": False})

    def test_metadata_refresh_preserves_effects_with_existing_preview_asset(self):
        dialog = self.editor()
        previous = deepcopy(dialog.draft.data["catalog"][0])
        native = self.root / "Furniture.json"
        native.write_text(json.dumps({"Test.PreviewLamp": "Lamp/lamp/1 1/1 1/1/100/0/Updated lamp/0/TileSheets/lamp"}))
        self.assertTrue(dialog.load_catalog(native))
        refreshed = dialog.draft.data["catalog"][0]
        self.assertEqual(refreshed["name"], "Updated lamp")
        for key in ("preview_asset", "frames", "preview_variants", "preview_lights"):
            self.assertEqual(refreshed[key], previous[key], key)

    def test_replacement_atlas_does_not_inherit_previous_effect_rectangles(self):
        dialog = self.editor()
        with Image.new("RGBA", (16, 16), "#885533") as image:
            image.save(self.root / "replacement.png")
        definition = {"id": "Test.PreviewLamp", "name": "Replacement", "kind": "lamp",
                      "footprint": [1, 1], "rotations": 1, "preview_asset": "replacement.png",
                      "frames": [{"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100}]}
        library = self.root / "library.json"
        library.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                       "definitions": [definition]}))
        self.assertTrue(dialog.load_catalog(library))
        refreshed = dialog.draft.data["catalog"][0]
        self.assertNotIn("preview_variants", refreshed)
        self.assertNotIn("preview_lights", refreshed)
        with preview_frame(refreshed, dialog.stage_root, time_of_day="night") as image:
            self.assertEqual(image.getpixel((8, 8)), (136, 85, 51, 255))

    def test_metadata_refresh_can_explicitly_clear_effects(self):
        dialog = self.editor()
        definition = {"id": "Test.PreviewLamp", "name": "Replacement", "kind": "lamp",
                      "footprint": [1, 1], "rotations": 1,
                      "preview_variants": {}, "preview_lights": []}
        library = self.root / "library.json"
        library.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                       "definitions": [definition]}))
        self.assertTrue(dialog.load_catalog(library))
        refreshed = dialog.draft.data["catalog"][0]
        self.assertEqual(refreshed["preview_variants"], {})
        self.assertEqual(refreshed["preview_lights"], [])
        self.assertTrue(refreshed["preview_asset"])

    def test_replacement_atlas_without_frames_does_not_inherit_old_crops(self):
        dialog = self.editor()
        original = dialog.draft.snapshot()
        original["catalog"][0]["frames"][0]["rect"] = [48, 0, 16, 16]
        dialog.draft.apply(original)
        with Image.new("RGBA", (16, 16), "#663399") as image:
            image.save(self.root / "smaller.png")
        definition = {"id": "Test.PreviewLamp", "name": "Replacement", "kind": "lamp",
                      "footprint": [1, 1], "rotations": 1, "preview_asset": "smaller.png", "frames": []}
        library = self.root / "library.json"
        library.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                       "definitions": [definition]}))
        self.assertTrue(dialog.load_catalog(library))
        self.assertEqual(dialog.draft.data["catalog"][0]["frames"], [])
        self.assertNotIn("preview_variants", dialog.draft.data["catalog"][0])

    def test_details_preserve_frame_offsets_with_their_rows(self):
        original = {"id": "Test.Cactus", "name": "Cactus", "kind": "decor",
                    "footprint": [1, 1], "rotations": 1, "frames": [
                        {"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100, "offset": [-1, 0]},
                        {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 100, "offset": [2, 3]},
                    ]}
        dialog = FurnitureDetails(original)
        self.dialogs.append(dialog)
        dialog.name.setText("Renamed cactus")
        dialog.frames.setCurrentCell(0, 0)
        dialog.remove_frame()
        dialog.add_frame()
        dialog.save_details()
        self.assertIsNotNone(dialog.result_definition)
        self.assertEqual(dialog.result_definition["frames"][0]["offset"], [2, 3])
        self.assertNotIn("offset", dialog.result_definition["frames"][1])
        self.assertEqual(original["frames"][0]["offset"], [-1, 0])

    def test_canvas_hit_uses_current_variant_sprite_offset(self):
        dialog = self.editor()
        candidate = dialog.draft.snapshot()
        definition = candidate["catalog"][0]
        definition["preview_variants"]["day_off"][0]["offset"] = [16, -16]
        definition["preview_variants"]["night_off"][0]["offset"] = [-16, -16]
        dialog.draft.apply(candidate)
        dialog.render()
        identity = dialog.draft.data["furniture"][0]["id"]
        self.assertEqual(dialog.canvas._at(7, 6)["id"], identity)
        self.assertIsNone(dialog.canvas._at(5, 6))
        self.choose(dialog.preview_lights, "off")
        self.choose(dialog.preview_time, "night")
        self.assertEqual(dialog.canvas._at(5, 6)["id"], identity)
        self.assertIsNone(dialog.canvas._at(7, 6))


if __name__ == "__main__":
    unittest.main()
