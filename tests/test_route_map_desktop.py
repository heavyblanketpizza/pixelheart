"""Daily routine: see each stop's place and click where the character stands."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from pixelheart.editors import SchedulePage
from pixelheart.route_map import RouteCanvas, RouteMapPanel
from tests.qt_support import QtTestCase


def stop(time, location, x, y, facing="down"):
    return {"id": f"{location}-{time}", "time": time, "location": location, "x": x, "y": y, "facing": facing, "activity": ""}


class RouteMapTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.requested = []
        self.page = SchedulePage()
        self.page.set_map_source(self.source)
        self.page.resize(900, 900)
        self.page.show()
        self.addCleanup(self.page.deleteLater)
        self.panel = self.page.map_panel
        self.canvas = self.panel.canvas
        self.changes = QSignalSpy(self.page.changed)

    def source(self, location):
        self.requested.append(location)
        if location == "Nowhere":
            return {"image": None, "foreground": None, "map_size": None, "label": "Nowhere",
                    "note": "Nowhere isn't in your game's maps."}
        size = {"Town": (20, 15), "Saloon": (10, 8)}[location]
        return {"image": Image.new("RGBA", (size[0] * 16, size[1] * 16), (80, 120, 80, 255)), "foreground": None,
                "map_size": size, "label": {"Town": "Pelican Town", "Saloon": "Stardrop Saloon"}[location],
                "note": "Stardew Valley game map"}

    def load(self, stops, row=0):
        self.page.load(stops)
        self.page.table.selectRow(row)
        self.app.processEvents()

    def click_tile(self, x, y):
        QTest.mouseClick(self.canvas, Qt.MouseButton.LeftButton, pos=self.canvas.tile_point(x, y).toPoint())
        self.app.processEvents()

    def test_selecting_a_stop_shows_its_place_and_the_stops_there(self):
        self.load([stop("600", "Town", 3, 4), stop("900", "Saloon", 2, 2), stop("1200", "Town", 8, 9)])
        self.assertEqual(self.panel.place_label.text(), "Pelican Town")
        self.assertEqual([actor["name"] for actor in self.canvas.actors], ["stop0", "stop2"])
        self.assertEqual(self.canvas.map_size, (20, 15))
        self.page.table.selectRow(1)
        self.assertEqual(self.panel.place_label.text(), "Stardrop Saloon")
        self.assertEqual([actor["name"] for actor in self.canvas.actors], ["stop1"])
        self.assertEqual(self.requested, ["Town", "Saloon"])
        self.page.table.selectRow(2)
        self.assertEqual(self.requested, ["Town", "Saloon", "Town"])

    def test_clicking_a_tile_moves_the_selected_stop(self):
        self.load([stop("600", "Town", 3, 4), stop("1200", "Town", 8, 9)])
        self.canvas.fit_map()
        self.click_tile(10, 7)
        record = self.page.dump()[0]
        self.assertEqual((record["x"], record["y"]), (10, 7))
        self.assertEqual((self.page.table.cellWidget(0, 2).value(), self.page.table.cellWidget(0, 3).value()), (10, 7))
        self.assertEqual(self.changes.count(), 1)
        self.assertEqual(self.page.dump()[1]["x"], 8)

    def test_clicking_another_stop_selects_its_row(self):
        self.load([stop("600", "Town", 3, 4), stop("1200", "Town", 8, 9)])
        self.canvas.fit_map()
        self.click_tile(8, 9)
        self.assertEqual(self.page.table.currentRow(), 1)
        self.assertEqual(self.changes.count(), 0)

    def test_facing_buttons_turn_the_selected_stop(self):
        self.load([stop("600", "Town", 3, 4)])
        self.panel.facing_buttons["left"].click()
        self.assertEqual(self.page.dump()[0]["facing"], "left")
        self.assertEqual(self.page.table.cellWidget(0, 4).currentData(), "left")
        self.assertEqual(self.canvas.actors[0]["facing"], "left")

    def test_editing_the_table_updates_the_map(self):
        self.load([stop("600", "Town", 3, 4)])
        self.page.table.cellWidget(0, 2).setValue(12)
        self.assertEqual(self.canvas.actors[0]["x"], 12)

    def test_no_map_message(self):
        self.load([stop("600", "Nowhere", 1, 1)])
        self.assertIn("isn't in your game", self.panel.note.text())
        self.assertTrue(self.canvas.background.isNull())
        self.assertEqual(len(self.canvas.actors), 1)

    def test_click_without_selection(self):
        self.page.load([])
        self.click_tile(2, 2)
        self.assertEqual(self.page.dump(), [])
        self.assertEqual(self.changes.count(), 0)

    def test_remove_selected_stop_keeps_panel_valid(self):
        self.load([stop("600", "Town", 3, 4), stop("900", "Saloon", 2, 2)], row=1)
        self.page.remove()
        self.assertEqual(self.panel.place_label.text(), "Pelican Town")
        self.assertEqual([actor["name"] for actor in self.canvas.actors], ["stop0"])

    def test_selected_stop_wins_a_shared_tile(self):
        self.load([stop("600", "Town", 5, 5), stop("1200", "Town", 5, 5)], row=0)
        self.canvas.fit_map()
        index = self.canvas.actor_at(self.canvas.tile_point(5, 5))
        self.assertEqual(self.canvas.actors[index]["name"], "stop0")

    def test_one_drag_is_one_gesture(self):
        self.load([stop("600", "Town", 3, 4)])
        self.canvas.fit_map()
        started, finished = QSignalSpy(self.panel.gestureStarted), QSignalSpy(self.panel.gestureFinished)
        start = self.canvas.tile_point(3, 4).toPoint()
        QTest.mousePress(self.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.canvas, self.canvas.tile_point(6, 4).toPoint())
        QTest.mouseMove(self.canvas, self.canvas.tile_point(7, 5).toPoint())
        QTest.mouseRelease(self.canvas, Qt.MouseButton.LeftButton, pos=self.canvas.tile_point(7, 5).toPoint())
        self.assertEqual((started.count(), finished.count()), (1, 1))
        self.assertEqual((self.page.dump()[0]["x"], self.page.dump()[0]["y"]), (7, 5))

    def test_the_canvas_draws_numbers_and_route_lines(self):
        self.load([stop("600", "Town", 3, 4), stop("900", "Town", 8, 9)])
        self.assertIsInstance(self.canvas, RouteCanvas)
        self.assertEqual(self.canvas.route_labels(), {"stop0": "1", "stop1": "2"})
        self.canvas.grab()

    def test_compact_routines_have_the_map_too(self):
        compact = SchedulePage(compact=True)
        self.addCleanup(compact.deleteLater)
        self.assertIsInstance(compact.map_panel, RouteMapPanel)
        compact.load([stop("600", "Town", 3, 4)])
        compact.table.selectRow(0)
        self.assertIn("Connect your game", compact.map_panel.note.text())


class RouteMapWindowTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from PySide6.QtCore import QSettings
        from pixelheart.app import MainWindow
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.game_import.game_import_settings", return_value=settings))
        self.enterContext(patch("pixelheart.story_page.EventsPage.update_scene_preview"))
        self.window = MainWindow(auto_download_icons=False)

    def tearDown(self):
        self.window.autosave.timer.stop()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_both_routine_editors_read_maps_through_the_project(self):
        pages = (self.window.schedule, self.window.life.editors["routines"].schedule)
        for page in pages:
            self.assertIsNotNone(page.map_panel.map_source)
        result = self.window.route_map("Town")
        self.assertIn("Connect your game", result["note"])

    def test_a_drag_is_one_undo_step(self):
        panel = self.window.schedule.map_panel
        panel.gestureStarted.emit()
        self.assertTrue(self.window.project_history.gesture_open)
        panel.gestureFinished.emit()
        self.assertFalse(self.window.project_history.gesture_open)
