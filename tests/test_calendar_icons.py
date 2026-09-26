"""Read local calendar sprites using synthetic atlases, never bundled game art."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSettings, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from pixelheart.birthday_calendar import BirthdayCalendar, CalendarLegend
from pixelheart.calendar_icons import (
    CalendarIconStore, MAX_ATLAS_BYTES, MAX_ATLAS_PIXELS, SOURCE_RECTS, _read_atlas,
)
from tests.qt_support import QtTestCase


class CalendarIconTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pixelheart-calendar-icons-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)

    def atlas(self, path, color=None):
        width = max(x + w for x, y, w, h in SOURCE_RECTS.values()) + 1
        height = max(y + h for x, y, w, h in SOURCE_RECTS.values()) + 1
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(color) if color else QColor("#173b65"))
        if color is None:
            for x, y, w, h in SOURCE_RECTS.values():
                for dy in range(h):
                    for dx in range(w):
                        # Global coordinates keep overlapping source regions consistent.
                        px, py = x + dx, y + dy
                        image.setPixelColor(px, py, QColor(px % 256, py % 256, (px + py) % 256,
                                                          0 if (px + py) % 7 == 0 else 255))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(image.save(str(path), "PNG"))
        return image

    def library(self, name="library", color=None):
        folder = self.root / name
        source = folder / "library.json"
        image = self.atlas(folder / "source" / "LooseSprites" / "Cursors.png", color)
        source.write_text("{}", encoding="utf-8")
        return source, image

    def assert_crops(self, store, atlas):
        for kind, source_rect in SOURCE_RECTS.items():
            with self.subTest(kind=kind):
                actual = store.image(kind)
                self.assertIsNotNone(actual)
                expected = atlas.copy(*source_rect).convertToFormat(actual.format())
                self.assertEqual(actual, expected)

    def test_library_source_crops_every_sprite_without_changing_files(self):
        source, atlas = self.library()
        self.settings.setValue("interiors/librarySource", str(source))
        self.settings.sync()
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}

        store = CalendarIconStore(settings=self.settings)

        self.assert_crops(store, atlas)
        self.assertFalse(store.reload())
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_library_folder_setting_is_supported(self):
        source, atlas = self.library()
        self.settings.setValue("interiors/libraryFolder", str(source.parent))

        self.assert_crops(CalendarIconStore(settings=self.settings), atlas)

    def test_flat_nested_and_content_patcher_export_layouts_are_supported(self):
        layouts = (
            Path("LooseSprites_Cursors.png"),
            Path("LooseSprites/Cursors.png"),
            Path("patch export/LooseSprites_Cursors.png"),
        )
        for index, relative in enumerate(layouts):
            with self.subTest(layout=str(relative)):
                folder = self.root / f"exports-{index}"
                atlas = self.atlas(folder / relative)
                self.settings.setValue("localGame/contentPatcherExportFolder", str(folder))

                self.assert_crops(CalendarIconStore(settings=self.settings), atlas)

    def test_content_patcher_export_takes_priority_over_library(self):
        source, _ = self.library(color="#193b75")
        exports = self.root / "exports"
        atlas = self.atlas(exports / "LooseSprites_Cursors.png", "#a16132")
        self.settings.setValue("interiors/librarySource", str(source))
        self.settings.setValue("localGame/contentPatcherExportFolder", str(exports))

        self.assert_crops(CalendarIconStore(settings=self.settings), atlas)

    def test_invalid_export_falls_back_to_connected_library(self):
        source, atlas = self.library()
        exports = self.root / "exports"
        exports.mkdir()
        (exports / "LooseSprites_Cursors.png").write_bytes(b"not a PNG")
        self.settings.setValue("interiors/librarySource", str(source))
        self.settings.setValue("localGame/contentPatcherExportFolder", str(exports))

        self.assert_crops(CalendarIconStore(settings=self.settings), atlas)

    def test_partial_export_keeps_available_sprites_and_uses_library_for_missing_regions(self):
        source, library_atlas = self.library()
        exports = self.root / "exports"
        path = exports / "LooseSprites_Cursors.png"
        full_atlas = self.atlas(path, "#a16132")
        height = min(y + h for x, y, w, h in SOURCE_RECTS.values())
        partial_atlas = full_atlas.copy(0, 0, full_atlas.width(), height)
        self.assertTrue(partial_atlas.save(str(path), "PNG"))
        self.settings.setValue("interiors/librarySource", str(source))
        self.settings.setValue("localGame/contentPatcherExportFolder", str(exports))

        store = CalendarIconStore(settings=self.settings)

        for kind, rect in SOURCE_RECTS.items():
            with self.subTest(kind=kind):
                atlas = partial_atlas if partial_atlas.rect().contains(QRect(*rect)) else library_atlas
                actual = store.image(kind)
                self.assertIsNotNone(actual)
                self.assertEqual(actual, atlas.copy(*rect).convertToFormat(actual.format()))

    def test_missing_unknown_and_invalid_images_have_no_sprite(self):
        exports = self.root / "exports"
        path = exports / "LooseSprites_Cursors.png"
        self.settings.setValue("localGame/contentPatcherExportFolder", str(exports))
        self.atlas(path)
        valid_png = path.read_bytes()
        for contents in (None, b"not a PNG", valid_png[:40]):
            with self.subTest(contents="missing" if contents is None else len(contents)):
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(contents)
                store = CalendarIconStore(settings=self.settings)
                for kind in SOURCE_RECTS:
                    self.assertIsNone(store.image(kind))
                self.assertIsNone(store.image("unknown"))

    def test_atlas_too_small_for_source_rectangles_has_no_sprite(self):
        exports = self.root / "exports"
        exports.mkdir()
        image = QImage(1, 1, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        self.assertTrue(image.save(str(exports / "LooseSprites_Cursors.png"), "PNG"))
        self.settings.setValue("localGame/contentPatcherExportFolder", str(exports))

        store = CalendarIconStore(settings=self.settings)

        for kind in SOURCE_RECTS:
            self.assertIsNone(store.image(kind))

    def test_oversized_input_is_rejected_before_reading_or_decoding(self):
        path = self.root / "large.png"
        with path.open("wb") as stream:
            stream.truncate(MAX_ATLAS_BYTES + 1)

        with patch.object(Path, "open") as open_file, patch("pixelheart.calendar_icons.QImageReader") as reader:
            self.assertIsNone(_read_atlas(path))

        open_file.assert_not_called()
        reader.assert_not_called()

    def test_oversized_decoded_dimensions_are_rejected_before_decode(self):
        path = self.root / "dimensions.png"
        self.atlas(path)
        with patch("pixelheart.calendar_icons.QImageReader") as reader:
            reader.return_value.size.return_value = QSize(MAX_ATLAS_PIXELS + 1, 1)

            self.assertIsNone(_read_atlas(path))

            reader.return_value.read.assert_not_called()

    def test_reload_switches_sources_and_clears_removed_sprites(self):
        first, first_atlas = self.library("first", "#113377")
        second, second_atlas = self.library("second", "#bb7744")
        self.settings.setValue("interiors/librarySource", str(first))
        store = CalendarIconStore(settings=self.settings)
        self.assert_crops(store, first_atlas)

        self.settings.setValue("interiors/librarySource", str(second))
        self.assertTrue(store.reload())
        self.assert_crops(store, second_atlas)
        self.assertFalse(store.reload())

        (second.parent / "source" / "LooseSprites" / "Cursors.png").unlink()
        self.assertTrue(store.reload())
        for kind in SOURCE_RECTS:
            self.assertIsNone(store.image(kind))

    def test_reload_detects_an_atlas_replaced_at_the_same_path(self):
        source, _ = self.library(color="#123456")
        path = source.parent / "source" / "LooseSprites" / "Cursors.png"
        self.settings.setValue("interiors/librarySource", str(source))
        store = CalendarIconStore(settings=self.settings)
        previous_mtime = path.stat().st_mtime_ns

        atlas = self.atlas(path, "#654321")
        os.utime(path, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))

        self.assertTrue(store.reload())
        self.assert_crops(store, atlas)
        self.assertFalse(store.reload())

    def test_paint_centers_integer_scale_without_smoothing_and_restores_hint(self):
        source, _ = self.library()
        self.settings.setValue("interiors/librarySource", str(source))
        store = CalendarIconStore(settings=self.settings)
        kind = next(iter(SOURCE_RECTS))
        sprite = store.image(kind)
        paper = QColor("#c6a583")
        for scale in (1, 2, 3):
            for smooth in (False, True):
                with self.subTest(scale=scale, smooth=smooth):
                    rect = QRect(3, 5, sprite.width() * scale + 6, sprite.height() * scale + 4)
                    canvas = QImage(rect.right() + 4, rect.bottom() + 4, QImage.Format.Format_ARGB32)
                    canvas.fill(paper)
                    painter = QPainter(canvas)
                    try:
                        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
                        self.assertTrue(store.paint(painter, kind, rect))
                        self.assertEqual(painter.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform), smooth)
                    finally:
                        painter.end()
                    left, top = rect.x() + 3, rect.y() + 2
                    for y in range(canvas.height()):
                        for x in range(canvas.width()):
                            expected = paper
                            if left <= x < left + sprite.width() * scale and top <= y < top + sprite.height() * scale:
                                pixel = sprite.pixelColor((x - left) // scale, (y - top) // scale)
                                if pixel.alpha():
                                    expected = pixel
                            self.assertEqual(canvas.pixelColor(x, y), expected, (x, y))

    def test_missing_sprite_paint_leaves_surface_and_hint_unchanged(self):
        store = CalendarIconStore(settings=self.settings)
        canvas = QImage(48, 48, QImage.Format.Format_ARGB32)
        canvas.fill(QColor("#c6a583"))
        before = canvas.copy()
        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self.assertFalse(store.paint(painter, next(iter(SOURCE_RECTS)), QRect(0, 0, 48, 48)))
            self.assertTrue(painter.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform))
        finally:
            painter.end()
        self.assertEqual(canvas, before)

    def test_calendar_shares_icons_and_refreshes_sources_without_changing_birthday(self):
        first, first_atlas = self.library("first", "#193b75")
        second, second_atlas = self.library("second", "#a16132")
        with patch("pixelheart.calendar_icons.game_import_settings", return_value=self.settings):
            calendar = BirthdayCalendar()
        self.addCleanup(calendar.deleteLater)
        self.addCleanup(calendar.close)
        signals = QSignalSpy(calendar.date_changed)
        birthday = (calendar.selected_season, calendar.selected_day)
        self.assertIs(calendar.season_picker.icons, calendar.icons)
        for widget in (*calendar.day_buttons.values(), *calendar.findChildren(CalendarLegend)):
            self.assertIs(widget.icons, calendar.icons)

        self.settings.setValue("interiors/librarySource", str(first))
        calendar.resize(780, 650)
        painted_kinds = set()
        original_paint = calendar.icons.paint

        def record_paint(painter, kind, bounds):
            # Do not retain the event's QPainter in mock call arguments.
            painted_kinds.add(kind)
            return original_paint(painter, kind, bounds)

        with patch.object(calendar.icons, "paint", record_paint):
            calendar.show()
            self.app.processEvents()
            self.assertTrue({"spring", "gift", "heart", "festival"}.issubset(painted_kinds))
        self.assert_crops(calendar.icons, first_atlas)

        self.settings.setValue("interiors/librarySource", str(second))
        calendar._render()

        self.assert_crops(calendar.icons, second_atlas)
        self.assertEqual((calendar.selected_season, calendar.selected_day), birthday)
        self.assertEqual(signals.count(), 0)


if __name__ == "__main__":
    unittest.main()
