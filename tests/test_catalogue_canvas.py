"""Interaction previews use authored poses and never walk through solid props."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.catalogue_canvas import CataloguePreviewCanvas


def solid(width, height, color):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


class CatalogueCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        solid(32, 32, "#145aaf").save(str(self.root / "piece.png"))
        front = QImage(32, 32, QImage.Format.Format_ARGB32)
        front.fill(Qt.GlobalColor.transparent)
        painter = QPainter(front)
        painter.fillRect(0, 24, 32, 8, QColor("#17aa39"))
        painter.end()
        front.save(str(self.root / "front.png"))
        red = solid(16, 32, "#e33535")
        self.actor = {"frames": {direction: [red, red] for direction in ("south", "east", "north", "west")},
                      "idle": {direction: red for direction in ("south", "east", "north", "west")},
                      "seated": {direction: red for direction in ("south", "east", "north", "west")}}
        self.canvas = CataloguePreviewCanvas()
        self.canvas.resize(176, 160)
        self.canvas.set_scale(1)
        self.addCleanup(self.canvas.deleteLater)
        self.addCleanup(self.canvas.close)

    def scene(self, kind="other", footprint=(2, 1), **view):
        side = {"id": "test-piece", "name": "Test piece", "kind": kind,
                "views": [{"label": "South", "rotation": 0, "width": 32, "height": 32,
                           "footprint": list(footprint), "image": "piece.png", **view}]}
        self.canvas.set_scene(side, self.root, self.actor)
        return side

    def samples(self, canvas=None, step=20):
        canvas = canvas or self.canvas
        return [canvas.sample(time) for time in range(0, math.ceil(canvas.duration_ms) + 1, step)]

    def test_decorative_walk_completes_entire_collision_perimeter(self):
        self.scene(footprint=(4, 3))
        states = self.samples()
        positions = [state["position"] for state in states]
        self.assertEqual(self.canvas.action, "walk")
        self.assertLess(min(x for x, _ in positions), 0)
        self.assertGreater(max(x for x, _ in positions), 64)
        self.assertLess(min(y for _, y in positions), 0)
        self.assertGreater(max(y for _, y in positions), 48)
        # Include the farmer's small ground-contact area, not just its centre.
        self.assertTrue(all(x <= -5 or x >= 69 or y <= -3 or y >= 51 for x, y in positions))
        self.assertEqual({state["direction"] for state in states}, {"south", "east", "north", "west"})
        self.assertEqual({state["occlusion"] for state in states}, {"front", "behind"})
        self.assertEqual(self.canvas.sample(0)["position"], self.canvas.sample(self.canvas.duration_ms)["position"])

    def test_walk_speed_is_stable_across_footprints_and_display_zoom(self):
        self.scene(footprint=(1, 1))
        before = self.canvas.sample(0)["position"]
        at_half_second = self.canvas.sample(500)["position"]
        self.assertEqual(math.dist(before, at_half_second), 16)
        self.canvas.set_scale(3)
        self.assertEqual(self.canvas.sample(500)["position"], at_half_second)
        self.scene(footprint=(5, 2))
        self.assertEqual(math.dist(self.canvas.sample(0)["position"], self.canvas.sample(500)["position"]), 16)

    def test_rug_allows_crossing_and_stays_under_farmer(self):
        self.scene("rug", (3, 2))
        states = self.samples()
        self.assertEqual(self.canvas.action, "cross")
        self.assertTrue(any(0 < state["position"][0] < 48 and 0 < state["position"][1] < 32 for state in states))
        self.assertEqual({state["occlusion"] for state in states}, {"front"})

    def test_tables_only_approach_without_inventing_an_action(self):
        self.scene("table", (2, 2), seat=[16, 16], foreground="front.png")
        self.assertEqual(self.canvas.action, "approach")
        states = self.samples()
        self.assertTrue(all(state["position"][1] > 32 for state in states))
        self.assertEqual({state["pose"] for state in states}, {"walk", "idle"})
        self.assertTrue(any(state["phase"] == "approach" and state["direction"] == "north" for state in states))

    def test_wall_pieces_end_at_wall_floor_seam_for_short_and_tall_art(self):
        for kind in ("window", "sconce", "painting", "wall_decor"):
            for height in (32, 48):
                with self.subTest(kind=kind, height=height):
                    solid(16, height, "#145aaf").save(str(self.root / "wall.png"))
                    self.scene(kind, (1, 1), image="wall.png", width=16, height=height)
                    self.canvas.set_interaction(False)
                    self.canvas.show()
                    self.app.processEvents()
                    image = self.canvas.grab().toImage()
                    self.assertEqual(image.pixelColor(88, 48 - height).name(), "#145aaf")
                    self.assertEqual(image.pixelColor(88, 47).name(), "#145aaf")
                    self.assertNotEqual(image.pixelColor(88, 48).name(), "#145aaf")
                    self.assertNotEqual(image.pixelColor(88, 80).name(), "#145aaf")

    def test_wall_interactions_stay_on_floor_and_approach_without_circling(self):
        for kind in ("window", "sconce", "painting", "wall_decor"):
            with self.subTest(kind=kind):
                self.scene(kind, (2, 2))
                self.assertEqual(self.canvas.action, "approach")
                states = self.samples()
                footprint_origin_y = 48 - 32
                self.assertTrue(all(footprint_origin_y + state["position"][1] >= 60 for state in states))
                self.assertEqual({state["occlusion"] for state in states}, {"front"})
                self.assertTrue(any(state["phase"] == "approach" and state["direction"] == "north" for state in states))

    def test_sitting_requires_explicit_seat_pose_and_matching_foreground(self):
        self.scene("armchair", seat=[16, 16])
        self.assertEqual(self.canvas.action, "walk")
        self.scene("armchair", foreground="front.png")
        self.assertEqual(self.canvas.action, "walk")
        side = self.scene("armchair", seat=[16, 16], foreground="front.png", rotation=1)
        self.assertEqual(self.canvas.action, "sit")
        seated = next(state for state in self.samples() if state["phase"] == "seated")
        self.assertEqual((seated["position"], seated["direction"]), ((16, 16), "east"))
        self.assertTrue(all(state["position"][1] >= 28 for state in self.samples() if state["pose"] == "walk"))
        unsupported_actor = {**self.actor, "seated": {"south": self.actor["idle"]["south"]}}
        self.canvas.set_scene(side, self.root, unsupported_actor)
        self.assertEqual(self.canvas.action, "walk")

    def test_bed_approaches_without_verified_sleeping_pose_or_anchor(self):
        side = self.scene("bed", (2, 2), seat=[16, 16], foreground="front.png")
        self.assertEqual(self.canvas.action, "approach")
        sleeping_actor = {**self.actor, "sleeping": solid(16, 32, "#aabbee"),
                          "bed_awake": self.actor["idle"]["south"],
                          "native_position_anchor": [8, 16], "native_sleeping_draw_offset": [0, -24]}
        self.canvas.set_scene(side, self.root, sleeping_actor)
        self.assertEqual(self.canvas.action, "sleep")
        self.assertTrue(any(state["pose"] == "sleeping" for state in self.samples()))
        side["views"][0].pop("seat")
        self.canvas.set_scene(side, self.root, sleeping_actor)
        self.assertEqual(self.canvas.action, "approach")
        side["views"][0]["sleep"] = [16, 32]
        self.canvas.set_scene(side, self.root, sleeping_actor)
        self.assertEqual(self.canvas.action, "sleep")
        self.assertEqual(next(state["position"] for state in self.samples() if state["pose"] == "sleeping" and state["phase"] == "sleeping"), (16, 32))

    def test_bed_enters_from_side_and_changes_pose_only_after_arrival(self):
        self.actor.update({"sleeping": solid(16, 32, "#aabbee"), "bed_awake": solid(16, 32, "#eeaaaa"),
                           "native_position_anchor": [8, 16], "native_sleeping_draw_offset": [0, -24],
                           "foot_anchor": [8, 32]})
        self.scene("bed", (2, 3), sleep=[16, 32], foreground="front.png")
        states = self.samples(step=10)
        entering = [state for state in states if state["phase"] == "entering"]
        self.assertTrue(entering)
        self.assertTrue(all(state["pose"] == "walk" and state["direction"] == "east" and
                            state["position"][1] == 24 and state["occlusion"] == "seated" for state in entering))
        self.assertTrue(all(state["position"][0] == -12 for state in states if state["phase"] == "walking"))
        sleeping = [state for state in states if state["pose"] == "sleeping"]
        self.assertTrue(sleeping)
        self.assertEqual({state["position"] for state in sleeping}, {(16, 32)})
        self.assertEqual({state["direction"] for state in sleeping}, {"south"})
        self.assertEqual({state["phase"] for state in states if state["pose"] == "bed_awake"}, {"settling", "waking"})
        self.assertTrue(all(state["pose"] == "walk" and state["direction"] == "west" and
                            state["position"][1] == 24 for state in states if state["phase"] == "exiting"))
        # The standing entry endpoint and captured bed pose share a top-left.
        entry = next(part for part in self.canvas._segments if part["phase"] == "entering")["end"]
        self.assertEqual((entry[0] - 8, entry[1] - 32), (16 - 8, 32 - 16 - 24))
        sleeping_part = next(part for part in self.canvas._segments if part["phase"] == "sleeping")
        self.assertGreaterEqual(sleeping_part["duration"], 2000)

    def test_canonical_native_double_and_child_bed_types_use_verified_sleep(self):
        self.actor.update({"sleeping": solid(16, 32, "#aabbee"), "bed_awake": solid(16, 32, "#eeaaaa"),
                           "native_position_anchor": [8, 16], "native_sleeping_draw_offset": [0, -24]})
        for kind in ("bed double", "bed child"):
            with self.subTest(kind=kind):
                self.scene(kind, (3, 3), sleep=[16, 32], foreground="front.png")
                self.assertEqual(self.canvas.action, "sleep")
                self.assertTrue(any(state["phase"] == "sleeping" for state in self.samples()))

    def test_native_bed_pose_stays_under_cover_with_visible_head(self):
        solid(32, 64, "#145aaf").save(str(self.root / "bed.png"))
        front = QImage(32, 64, QImage.Format.Format_ARGB32)
        front.fill(Qt.GlobalColor.transparent)
        painter = QPainter(front)
        painter.fillRect(0, 32, 32, 32, QColor("#17aa39"))
        painter.end()
        front.save(str(self.root / "cover.png"))
        self.actor.update({"sleeping": solid(16, 32, "#aabbee"), "bed_awake": solid(16, 32, "#eeaaaa"),
                           "native_position_anchor": [8, 16], "native_sleeping_draw_offset": [0, -24]})
        self.scene("bed", (2, 3), image="bed.png", foreground="cover.png", sleep=[16, 32])
        self.canvas.play()
        sleep = next(part for part in self.canvas._segments if part["phase"] == "sleeping")
        self.canvas.advance(sleep["begin"] + 500)
        rendered = self.canvas.render_scene()
        self.assertEqual(rendered.pixelColor(85, 85).name(), "#aabbee")
        self.assertEqual(rendered.pixelColor(85, 105).name(), "#17aa39")
        self.assertEqual(rendered.pixelColor(79, 85).name(), "#145aaf")

    def effects_scene(self):
        colors = {"day_off": "#145aaf", "day_on": "#f08020", "night_off": "#203050", "night_on": "#ffe060"}
        for key, color in colors.items():
            solid(32, 32, color).save(str(self.root / f"{key}.png"))
        return self.scene("fireplace", states={key: {"image": f"{key}.png"} for key in colors})

    def test_time_and_power_select_actual_native_state_images(self):
        self.effects_scene()
        for time, power, expected in (("day", False, "#145aaf"), ("day", True, "#f08020"),
                                       ("night", False, "#203050"), ("night", True, "#ffe060")):
            self.canvas.set_time_of_day(time)
            self.canvas.set_power(power)
            sample = self.canvas.sample_effects(0)
            self.assertIsInstance(sample["image"], QImage)
            self.assertEqual(sample["image"].pixelColor(0, 0).name(), expected)
            self.assertEqual(sample["ambient"].name(), "#ffffff" if time == "day" else "#526086")

    def test_flame_frames_loop_on_independent_clock_after_farmer_stops(self):
        side = self.effects_scene()
        side["views"][0]["states"]["day_on"]["animation_frames"] = [
            {"image": "day_on.png", "duration_ms": 100}, {"image": "night_on.png", "duration_ms": 200}]
        self.canvas.set_scene(side, self.root, self.actor)
        self.canvas.set_power(True)
        self.assertEqual([self.canvas.sample_effects(t)["frame"] for t in (0, 99, 100, 299, 300)], [0, 0, 1, 1, 0])
        self.canvas.play()
        self.canvas.advance(450)
        self.assertEqual(self.canvas.effects_elapsed_ms, 0)
        self.canvas.advance_effects(150)
        self.assertEqual(self.canvas.elapsed_ms, 450)
        self.canvas.stop()
        self.assertEqual(self.canvas.effects_elapsed_ms, 150)
        self.assertEqual(self.canvas.sample_effects(self.canvas.effects_elapsed_ms)["image"].pixelColor(0, 0).name(), "#ffe060")
        self.canvas.advance_effects(150)
        self.assertEqual(self.canvas.sample_effects(self.canvas.effects_elapsed_ms)["frame"], 0)
        self.canvas.set_power(False)
        self.assertEqual(self.canvas.sample_effects(150)["image"].pixelColor(0, 0).name(), "#145aaf")

    def test_powered_effect_timer_survives_stop_but_pauses_hidden(self):
        side = self.effects_scene()
        side["views"][0]["states"]["day_on"]["animation_frames"] = ["day_on.png", "night_on.png"]
        self.canvas.set_scene(side, self.root, self.actor)
        self.canvas.set_interaction(False)
        self.canvas.set_power(True)
        self.canvas.show()
        self.assertTrue(self.canvas._timer.isActive())
        self.canvas.stop()
        self.assertTrue(self.canvas._timer.isActive())
        self.canvas.advance_effects(70)
        self.canvas.hide()
        self.assertFalse(self.canvas._timer.isActive())
        self.assertEqual(self.canvas.effects_elapsed_ms, 70)
        self.canvas.show()
        self.assertTrue(self.canvas._timer.isActive())
        self.assertEqual(self.canvas.effects_elapsed_ms, 70)
        self.canvas.set_power(False)
        self.assertFalse(self.canvas._timer.isActive())

    def test_night_darkens_farmer_and_native_alpha_light_restores_local_color(self):
        solid(8, 4, "#000000").save(str(self.root / "mask.png"))
        self.scene("lamp", lights=[{"offset": [-12, 12], "radius": 32, "mask": "mask.png",
                                  "color": "#ffffff", "intensity": 1, "when": "night",
                                  "requires_power": True, "blend": "illuminate", "mask_channel": "alpha"}])
        day = self.canvas.render_scene()
        self.canvas.set_time_of_day("night")
        off = self.canvas.render_scene()
        self.assertEqual(self.canvas.sample_effects(0)["lights"], [])
        self.canvas.set_power(True)
        on = self.canvas.render_scene()
        self.assertEqual(len(self.canvas.sample_effects(0)["lights"]), 1)
        self.assertEqual(day.pixelColor(56, 102).name(), "#e33535")
        self.assertLess(off.pixelColor(56, 102).red(), day.pixelColor(56, 102).red())
        self.assertEqual(on.pixelColor(56, 102), day.pixelColor(56, 102))
        # The exact same native mask restores floor colour, while distant room remains dark.
        self.assertEqual(on.pixelColor(40, 110), day.pixelColor(40, 110))
        self.assertEqual(on.pixelColor(150, 140), off.pixelColor(150, 140))

    def test_overlay_keeps_native_rgba_and_mask_aspect_ratio(self):
        solid(2, 6, "#e8c088").save(str(self.root / "rays.png"))
        self.scene(lights=[{"offset": [16, 8], "radius": 12, "mask": "rays.png", "intensity": 1,
                           "when": "day", "requires_power": False, "blend": "overlay"}])
        self.canvas.set_interaction(False)
        day = self.canvas.render_scene()
        self.assertEqual(day.pixelColor(88, 100).name(), "#e8c088")
        self.assertNotEqual(day.pixelColor(80, 100).name(), "#e8c088")
        self.assertNotEqual(day.pixelColor(88, 96).name(), "#e8c088")
        self.canvas.set_time_of_day("night")
        self.assertEqual(self.canvas.sample_effects(0)["lights"], [])

    def test_optional_mask_uses_radial_falloff_but_missing_asset_does_not(self):
        side = self.scene(lights=[{"offset": [-12, 0], "radius": 32, "color": "#ffffff",
                                  "when": "night", "requires_power": True, "intensity": 1}])
        self.canvas.set_time_of_day("night")
        off = self.canvas.render_scene()
        self.canvas.set_power(True)
        on = self.canvas.render_scene()
        self.assertGreater(on.pixelColor(60, 102).red(), off.pixelColor(60, 102).red() + 100)
        self.assertEqual(on.pixelColor(150, 140), off.pixelColor(150, 140))
        side["views"][0]["lights"][0]["mask"] = "missing-mask.png"
        self.canvas.set_scene(side, self.root, self.actor)
        missing = self.canvas.render_scene()
        self.assertEqual(missing.pixelColor(60, 102), off.pixelColor(60, 102))

    def test_legacy_animation_is_on_only_and_effect_times_reject_nonfinite_values(self):
        solid(32, 32, "#ff8800").save(str(self.root / "flame.png"))
        self.scene(animation_frames=["piece.png", "flame.png"])
        self.assertEqual(self.canvas.sample_effects(150)["image"].pixelColor(0, 0).name(), "#145aaf")
        self.canvas.set_power(True)
        self.assertEqual(self.canvas.sample_effects(150)["image"].pixelColor(0, 0).name(), "#ff8800")
        with self.assertRaises(ValueError):
            self.canvas.set_time_of_day("dusk")
        with self.assertRaises(ValueError):
            self.canvas.sample_effects(float("nan"))
        with self.assertRaises(ValueError):
            self.canvas.advance_effects(float("inf"))
        with self.assertRaises(ValueError):
            self.canvas.advance_effects(-1)

    def test_captured_native_seated_offset_controls_actual_sprite_position(self):
        side = self.scene("armchair", seat=[16, 16], foreground="front.png", rotation=1)
        actor = {**self.actor, "foot_anchor": [8, 32], "native_position_anchor": [8, 16],
                 "native_seated_draw_offset": {"east": [3, -18]}, "walking_frame_duration_ms": 110}
        self.canvas.set_scene(side, self.root, actor)
        self.assertEqual(self.canvas.sample(220)["frame"], 2)
        self.canvas.play()
        self.canvas.advance(1500)
        self.canvas.show()
        self.app.processEvents()
        self.canvas._timer.stop()
        image = self.canvas.grab().toImage()
        # Feet=(88,118); native position=(80,102); captured top-left=(83,84).
        self.assertEqual(image.pixelColor(83, 84).name(), "#e33535")
        self.assertNotEqual(image.pixelColor(82, 84).name(), "#e33535")

    def test_foreground_really_occludes_seated_farmer_pixels(self):
        self.scene("armchair", seat=[16, 16], foreground="front.png")
        self.assertTrue(self.canvas.play())
        self.canvas.advance(1500)
        self.assertEqual(self.canvas.sample(1500)["phase"], "seated")
        self.canvas.show()
        self.app.processEvents()
        self.canvas._timer.stop()  # freeze the deterministic frame for raster assertions
        image = self.canvas.grab().toImage()
        self.assertEqual(image.pixelColor(88, 95).name(), "#e33535")
        self.assertEqual(image.pixelColor(82, 110).name(), "#17aa39")
        self.assertEqual(image.pixelColor(74, 88).name(), "#145aaf")

    def test_nearest_neighbor_rendering_preserves_exact_sprite_colors(self):
        self.scene()
        self.canvas.set_interaction(False)
        self.canvas.set_scale(2)
        self.canvas.resize(352, 320)
        self.canvas.show()
        self.app.processEvents()
        image = self.canvas.grab().toImage()
        # Top-left furniture source pixel, well away from the dotted footprint.
        self.assertEqual({image.pixelColor(x, y).name() for x in (144, 145) for y in (172, 173)}, {"#145aaf"})

    def test_hidden_canvas_pauses_timer_and_resumes_without_reset(self):
        self.scene()
        self.canvas.show()
        self.app.processEvents()
        self.assertTrue(self.canvas.play())
        self.canvas.advance(400)
        self.canvas.hide()
        self.assertFalse(self.canvas._timer.isActive())
        self.assertTrue(self.canvas.is_playing)
        self.assertEqual(self.canvas.elapsed_ms, 400)
        self.canvas.show()
        self.assertTrue(self.canvas._timer.isActive())
        self.assertEqual(self.canvas.elapsed_ms, 400)
        self.canvas.stop()
        self.assertFalse(self.canvas._timer.isActive())

    def test_owner_can_synchronize_click_and_keyboard_between_panes(self):
        side = self.scene()
        other = CataloguePreviewCanvas()
        self.addCleanup(other.deleteLater)
        self.addCleanup(other.close)
        other.set_scene(side, self.root, self.actor)
        self.canvas.clicked.connect(self.canvas.play)
        self.canvas.clicked.connect(other.play)
        self.canvas.show()
        self.app.processEvents()
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=QPoint(88, 80))
        self.assertTrue(self.canvas.is_playing and other.is_playing)
        self.canvas.advance(700)
        other.advance(700)
        self.assertEqual(self.canvas.sample(self.canvas.elapsed_ms), other.sample(other.elapsed_ms))
        QTest.keyClick(self.canvas, Qt.Key.Key_Space)
        self.assertEqual((self.canvas.elapsed_ms, other.elapsed_ms), (0, 0))

    def test_finish_view_change_and_disabled_interaction_stop_cleanly(self):
        side = self.scene()
        side["views"].append({**side["views"][0], "rotation": 1, "footprint": [3, 1]})
        self.canvas.set_scene(side, self.root, self.actor)
        self.canvas.play()
        self.canvas.advance(self.canvas.duration_ms + 999)
        self.assertFalse(self.canvas.is_playing)
        self.assertTrue(self.canvas.sample(self.canvas.elapsed_ms)["finished"])
        self.canvas.play()
        self.canvas.advance(600)
        self.canvas.set_view(1)
        self.assertEqual(self.canvas.elapsed_ms, 0)
        self.assertFalse(self.canvas.is_playing)
        self.canvas.set_interaction(False)
        self.assertFalse(self.canvas.play())

    def test_missing_or_external_art_fails_as_a_static_unavailable_preview(self):
        side = self.scene()
        side["views"][0]["image"] = "../outside.png"
        self.canvas.set_scene(side, self.root, self.actor)
        self.assertFalse(self.canvas.play())
        self.canvas.show()
        self.app.processEvents()
        self.assertFalse(self.canvas.grab().isNull())
        with self.assertRaises(ValueError):
            self.canvas.advance(float("nan"))
        with self.assertRaises(ValueError):
            self.canvas.sample(float("nan"))
        with self.assertRaises(ValueError):
            self.canvas.set_background("unknown")


if __name__ == "__main__":
    unittest.main()
