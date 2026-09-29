"""Local scene art stays read-only while placement keeps exact game tiles."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pixelheart.stage_canvas import StageCanvas
from tests.qt_support import QtTestCase


def sheet(colors=("#ee3344", "#33aa55", "#4466ee", "#ddbb33"), factor=1):
    image = QImage(int(64 * factor), int(128 * factor), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    for row, color in enumerate(colors):
        painter.fillRect(0, int(row * 32 * factor), int(16 * factor), int(32 * factor), QColor(color))
    painter.end()
    return image


class SceneStageCanvasTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.canvas = StageCanvas()
        self.canvas.resize(700, 410)
        self.actors = [dict(id="npc", name="$npc", x=10, y=10, facing=2),
                       dict(id="farmer", name="farmer", x=12, y=10, facing=3)]
        self.canvas.load(self.actors, {"$npc": "Rowan", "farmer": "Farmer"}, fit=True)
        self.canvas.show()
        self.app.processEvents()

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        self.app.processEvents()

    def test_facing_uses_the_standard_idle_row_at_original_and_enlarged_resolution(self):
        for factor in (1, 1.5, 2):
            with self.subTest(factor=factor):
                self.canvas.set_scene_preview(sprites={"$npc": sheet(factor=factor)})
                for facing, expected in ((0, "#4466ee"), (1, "#33aa55"), (2, "#ee3344"), (3, "#ddbb33")):
                    actor = {**self.actors[0], "facing": facing}
                    frame = self.canvas.sprite_frame(actor)
                    self.assertEqual((frame.width(), frame.height()), (16 * factor, 32 * factor))
                    self.assertEqual(frame.pixelColor(2, 2).name(), expected)

    def test_preview_and_enlarged_copy_never_mutate_source_art_or_cast(self):
        background = QImage(320, 240, QImage.Format.Format_ARGB32_Premultiplied)
        background.fill(QColor("#223344"))
        sprites = {"$npc": sheet()}
        foreground = background.copy()
        foreground.fill(QColor("#667788"))
        original = deepcopy(self.actors)
        self.canvas.set_scene_preview(background, (20, 15), sprites, "A supplied garden", "Local art", "male", "garden-v1", foreground)
        self.canvas.set_grid_visible(True)
        self.canvas.set_zoom(1.5)
        other = StageCanvas()
        try:
            other.load(self.actors)
            other.copy_preview_from(self.canvas)
            self.assertEqual(other.map_size, (20, 15))
            self.assertEqual(other.bounds, self.canvas.bounds)
            self.assertEqual(other.zoom_factor, 1.5)
            self.assertEqual(other.farmer_gender, "male")
            self.assertTrue(other.grid_visible)
            self.assertEqual(other.source_note, "Local art")
            foreground.fill(QColor("#ffffff"))
            self.assertEqual(other.foreground.pixelColor(0, 0).name(), "#667788")
            self.canvas.foreground.fill(QColor("#000000"))
            self.assertEqual(other.foreground.pixelColor(0, 0).name(), "#667788")
            background.fill(QColor("#000000"))
            sprites["$npc"].fill(QColor("#ffffff"))
            self.assertEqual(self.canvas.background.pixelColor(0, 0).name(), "#223344")
            self.canvas.background.fill(QColor("#123456"))
            self.assertEqual(other.background.pixelColor(0, 0).name(), "#223344")
            self.assertEqual(other.sprite_frame(self.actors[0]).pixelColor(2, 2).name(), "#ee3344")
            self.assertEqual(self.actors, original)
        finally:
            other.deleteLater()

    def test_missing_assets_are_explicit_and_never_invent_people_or_scenery(self):
        self.assertFalse(self.canvas.grid_visible)
        self.assertIn("map unavailable", self.canvas.preview_note)
        self.assertTrue(self.canvas.background.isNull())
        self.assertTrue(self.canvas.foreground.isNull())
        self.assertTrue(self.canvas.sprite_frame(self.actors[0]).isNull())
        self.assertTrue(self.canvas.sprite_frame(self.actors[1]).isNull())
        before = deepcopy(self.canvas.actors)
        self.canvas.set_scene_preview(farmer_gender="male")
        self.assertTrue(self.canvas.sprite_frame(self.actors[1]).isNull())
        self.assertEqual(self.canvas.actors, before)
        rendered = self.canvas.grab().toImage()
        self.assertEqual(rendered.pixelColor(10, 120).name(), "#262d2a")
        self.assertEqual(rendered.pixelColor(620, 330).name(), "#262d2a")
        self.assertEqual(self.canvas.actor_at(self.canvas.marker_rect(0).center()), 0)
        self.assertIn("named position markers", self.canvas.accessibleDescription())

    def test_invalid_sprite_sheet_uses_a_named_marker(self):
        for width, height in ((65, 128), (64, 127), (3, 6)):
            with self.subTest(size=(width, height)):
                invalid = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
                invalid.fill(QColor("#ff0000"))
                self.canvas.set_scene_preview(sprites={"$npc": invalid})
                self.assertTrue(self.canvas.sprite_frame(self.actors[0]).isNull())
                self.assertEqual(self.canvas.actor_at(self.canvas.marker_rect(0).center()), 0)

    def test_camera_uses_full_width_and_zoom_preserves_tile_coordinates(self):
        before = deepcopy(self.canvas.actors)
        for width in (460, 1000):
            self.canvas.resize(width, 410)
            for zoom in (0.5, 1, 2):
                self.canvas.set_zoom(zoom)
                left, top, scale, ox, oy = self.canvas.geometry_grid()
                self.assertEqual(ox, 0)
                self.assertEqual(self.canvas.scene_rect().width(), width)
                for actor in self.actors:
                    point = self.canvas.tile_point(actor["x"], actor["y"])
                    self.assertEqual(self.canvas.tile_at(point), (actor["x"], actor["y"]))
                    target = self.canvas.actor_rect(self.actors.index(actor))
                    self.assertAlmostEqual(target.bottom(), point.y() + scale / 2)
        self.canvas.fit()
        self.assertEqual(self.canvas.zoom_factor, 1)
        self.assertEqual(self.canvas.actors, before)
        self.assertIsNone(self.canvas.tile_at(QPointF(20, 10)))

    def test_clicking_a_sprite_head_selects_its_actor_without_relocating_it(self):
        self.canvas.set_scene_preview(sprites={"$npc": sheet(), "farmer": sheet()})
        selected, moved = [], []
        self.canvas.actorSelected.connect(selected.append)
        self.canvas.actorMoved.connect(lambda *args: moved.append(args))
        self.canvas.select_actor(1)
        body = self.canvas.actor_rect(0)
        head = QPointF(body.center().x(), body.top() + body.height() * .2)
        self.assertNotEqual(self.canvas.tile_at(head), (10, 10))
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=head.toPoint())
        self.assertEqual(selected, [0])
        self.assertEqual(moved, [])
        self.assertEqual(self.canvas.actors, self.actors)

    def test_sprite_body_drag_preserves_grab_offset_and_emits_one_gesture(self):
        self.canvas.set_scene_preview(sprites={"$npc": sheet(), "farmer": sheet()})
        phases, moved = [], []
        self.canvas.gestureStarted.connect(lambda: phases.append("start"))
        self.canvas.gestureFinished.connect(lambda: phases.append("finish"))
        self.canvas.actorMoved.connect(lambda *args: moved.append(args))
        body = self.canvas.actor_rect(0)
        start = QPointF(body.center().x(), body.top() + 5).toPoint()
        scale = self.canvas.geometry_grid()[2]
        end = QPointF(start) + QPointF(scale * 2, scale)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.canvas, move)
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=end.toPoint())
        self.assertEqual((self.canvas.actors[0]["x"], self.canvas.actors[0]["y"]), (12, 11))
        self.assertEqual(moved, [(0, 12, 11)])
        self.assertEqual(phases, ["start", "finish"])
        QTest.keyClick(self.canvas, Qt.Key.Key_Left)
        self.assertEqual(self.canvas.actors[0]["x"], 11)

    def test_foreground_actor_is_painted_and_hit_tested_after_the_actor_behind(self):
        actors = [dict(name="front", x=10, y=11, facing=2), dict(name="back", x=10, y=10, facing=2)]
        self.canvas.load(actors, fit=True)
        self.canvas.set_scene_preview(sprites={"front": sheet(("#33aa55",) * 4), "back": sheet(("#4466ee",) * 4)})
        self.assertEqual(self.canvas.paint_order(), [1, 0])
        overlap = self.canvas.actor_rect(0).intersected(self.canvas.actor_rect(1)).center()
        self.assertEqual(self.canvas.actor_at(overlap), 0)
        rendered = self.canvas.grab().toImage()
        self.assertEqual(rendered.pixelColor(overlap.toPoint()).name(), "#33aa55")

    def test_missing_art_marker_is_hit_tested_above_overlapping_real_art(self):
        actors = [dict(name="missing", x=10, y=10, facing=2), dict(name="real", x=10, y=11, facing=2)]
        self.canvas.load(actors, {"missing": "Missing artwork"}, fit=True)
        self.canvas.set_scene_preview(sprites={"real": sheet()})
        position = self.canvas.tile_point(10, 10)
        self.assertTrue(self.canvas.actor_rect(1).contains(position))
        self.assertEqual(self.canvas.actor_at(position), 0)

    def test_local_map_renders_at_its_real_coordinates(self):
        background = QImage(512, 512, QImage.Format.Format_ARGB32_Premultiplied)
        background.fill(QColor("#123456"))
        before = deepcopy(self.canvas.actors)
        self.canvas.set_scene_preview(background, (32, 32), location_label="Forest", source_note="Private local map")
        self.assertIn("Forest", self.canvas.preview_note)
        self.assertNotIn("preview backdrop", self.canvas.preview_note)
        rendered = self.canvas.grab().toImage()
        point = self.canvas.tile_point(8, 8)
        self.assertEqual(rendered.pixelColor(point.toPoint()).name(), "#123456")
        self.assertEqual(self.canvas.actors, before)

    def test_fit_map_reveals_a_small_location_even_when_the_cast_is_elsewhere(self):
        actors = [dict(name="$npc", x=32, y=62, facing=2), dict(name="farmer", x=34, y=62, facing=3)]
        self.canvas.load(actors, fit=True)
        background = QImage(128, 96, QImage.Format.Format_ARGB32_Premultiplied)
        background.fill(QColor("#456789"))
        self.canvas.set_scene_preview(background, (8, 6), location_label="A small room")
        self.canvas.set_zoom(3)
        moved = []
        self.canvas.actorMoved.connect(lambda *args: moved.append(args))
        self.assertFalse(self.canvas.scene_rect().contains(self.canvas.tile_point(0, 0)))
        self.canvas.fit_map()
        self.assertEqual(self.canvas.bounds, (0, 0, 8, 6))
        self.assertEqual(self.canvas.zoom_factor, 1)
        for tile in ((0, 0), (7, 0), (0, 5), (7, 5)):
            point = self.canvas.tile_point(*tile)
            self.assertTrue(self.canvas.scene_rect().contains(point))
            self.assertEqual(self.canvas.tile_at(point), tile)
        self.assertEqual(self.canvas.actors, actors)
        self.assertEqual(moved, [])
        self.canvas.fit()
        self.assertTrue(self.canvas.scene_rect().contains(self.canvas.tile_point(32, 62)))

    def test_fit_map_without_geometry_frames_actors_instead_of_inventing_a_map(self):
        before = deepcopy(self.canvas.actors)
        fitted = self.canvas.bounds
        self.canvas.pan_by(QPointF(200, 300))
        self.canvas.set_zoom(2)
        self.canvas.fit_map()
        self.assertEqual(self.canvas.bounds, fitted)
        self.assertEqual(self.canvas.actors, before)
        self.assertTrue(self.canvas.background.isNull())
        self.assertEqual(self.canvas.zoom_factor, 1)
        self.canvas.set_scene_preview(map_size=(8, 6))
        self.canvas.fit_map()
        self.assertEqual(self.canvas.bounds, (0, 0, 8, 6))
        self.assertTrue(self.canvas.background.isNull())

    def test_pointer_anchored_wheel_zoom_changes_only_the_view(self):
        before = deepcopy(self.canvas.actors)
        edits, views = [], []
        self.canvas.actorMoved.connect(lambda *args: edits.append(args))
        self.canvas.gestureStarted.connect(lambda: edits.append("start"))
        self.canvas.viewChanged.connect(lambda: views.append(self.canvas.zoom_factor))
        position = QPointF(450, 120)
        world_before = self.canvas.world_at(position)
        wheel = QWheelEvent(position, position, QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(self.canvas, wheel)
        world_after = self.canvas.world_at(position)
        self.assertAlmostEqual(world_before.x(), world_after.x())
        self.assertAlmostEqual(world_before.y(), world_after.y())
        self.assertAlmostEqual(self.canvas.zoom_factor, 1.2)
        self.assertEqual(self.canvas.actors, before)
        self.assertEqual(edits, [])
        self.assertEqual(len(views), 1)

    def test_middle_and_space_drag_pan_without_actor_or_history_changes(self):
        original = deepcopy(self.canvas.actors)
        edits = []
        self.canvas.actorMoved.connect(lambda *args: edits.append(args))
        self.canvas.gestureStarted.connect(lambda: edits.append("start"))
        self.canvas.gestureFinished.connect(lambda: edits.append("finish"))
        for use_space in (False, True):
            with self.subTest(space=use_space):
                self.canvas.fit()
                before = self.canvas.tile_point(10, 10)
                start, end = QPointF(300, 190), QPointF(364, 222)
                button = Qt.MouseButton.LeftButton if use_space else Qt.MouseButton.MiddleButton
                if use_space:
                    QTest.keyPress(self.canvas, Qt.Key.Key_Space)
                QTest.mousePress(self.canvas, button, pos=start.toPoint())
                self.assertTrue(self.canvas.panning)
                move = QMouseEvent(QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
                                   button, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(self.canvas, move)
                QTest.mouseRelease(self.canvas, button, pos=end.toPoint())
                if use_space:
                    QTest.keyRelease(self.canvas, Qt.Key.Key_Space)
                after = self.canvas.tile_point(10, 10)
                self.assertEqual(after - before, end - start)
                self.assertFalse(self.canvas.panning)
                self.assertFalse(self.canvas.dragging)
                self.assertEqual(self.canvas.actors, original)
                self.assertEqual(edits, [])

    def test_focus_loss_clears_pan_mode(self):
        QTest.keyPress(self.canvas, Qt.Key.Key_Space)
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 190))
        QApplication.sendEvent(self.canvas, QEvent(QEvent.Type.FocusOut))
        self.assertFalse(self.canvas.panning)
        self.assertFalse(self.canvas._space_pressed)
        self.assertEqual(self.canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_foreground_map_layer_occludes_art_but_keeps_cast_editable(self):
        background = QImage(320, 320, QImage.Format.Format_ARGB32_Premultiplied)
        background.fill(QColor("#123456"))
        foreground = background.copy()
        foreground.fill(Qt.GlobalColor.transparent)
        painter = QPainter(foreground)
        # A supplied upper tile covers the NPC's head, not its standing tile.
        painter.fillRect(10 * 16, 9 * 16, 16, 16, QColor("#765432"))
        painter.end()
        self.canvas.set_scene_preview(background, (20, 20), {"$npc": sheet()}, foreground=foreground)
        self.canvas.set_facing_markers(False)
        head = self.canvas.tile_point(10, 9)
        foot = self.canvas.tile_point(10, 10)
        rendered = self.canvas.grab().toImage()
        self.assertEqual(rendered.pixelColor(head.toPoint()).name(), "#765432")
        self.assertEqual(rendered.pixelColor(foot.toPoint()).name(), "#ee3344")
        self.assertEqual(self.canvas.actor_at(head), 0)

    def test_expanded_canvas_keeps_readable_native_pixel_scale(self):
        self.canvas.resize(2400, 1600)
        self.canvas.fit()
        self.assertEqual(self.canvas.geometry_grid()[2], 64)
        self.assertEqual(self.canvas.height(), 1600)
        self.assertEqual(self.canvas.scene_rect().width(), 2400)

    def test_default_cast_framing_shows_context_and_survives_compact_resize(self):
        self.canvas.set_scene_preview(sprites={"$npc": sheet(), "farmer": sheet()})
        self.canvas.set_header_visible(False)
        self.canvas.resize(639, 588)
        self.canvas.fit()
        self.assertGreaterEqual(self.canvas.bounds[2], 18)
        self.assertGreaterEqual(self.canvas.bounds[3], 14)
        self.assertEqual(self.canvas.geometry_grid()[2], 32)
        self.canvas.setMinimumHeight(0)
        self.canvas.resize(480, 150)
        for index in range(len(self.actors)):
            self.assertTrue(self.canvas.scene_rect().contains(self.canvas.actor_rect(index)))
        self.assertEqual(self.canvas.actors, self.actors)

    def test_missing_art_names_remain_separate_at_overview_scale(self):
        self.canvas.resize(480, 300)
        first, second = self.canvas.marker_rect(0), self.canvas.marker_rect(1)
        self.assertFalse(first.intersects(second))
        for index in (0, 1):
            self.assertEqual(self.canvas.actor_at(self.canvas.marker_rect(index).center()), index)
            actor = self.actors[index]
            self.assertEqual(self.canvas.actor_at(self.canvas.tile_point(actor["x"], actor["y"])), index)
