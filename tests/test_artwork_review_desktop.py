"""Detailed artwork review integration using synthetic, local PNG sheets only."""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

from pixelheart.app import MainWindow
from pixelheart.theme import apply_theme
from pixelheart_core.projects import import_artwork, new_project
from tests.qt_support import QtTestCase


class ArtworkReviewDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-review-test-")
        self.root = Path(self.temporary.name)
        self.project_file = self.root / "project" / "character.json"
        self.window = MainWindow()
        self.dialogs = []
        self.errors = self.enterContext(patch.object(MainWindow, "show_error"))
        self.assertTrue(self.window.save_to(self.project_file))

    def tearDown(self):
        for dialog in reversed(self.dialogs):
            if isValid(dialog):
                dialog.close()
                dialog.deleteLater()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    def sheet(self, name, kind, rows, *, scale=1, seed=0):
        columns = 2 if kind == "portrait" else 4
        width, height = (64, 64) if kind == "portrait" else (16, 32)
        width, height = width * scale, height * scale
        colors = [((index * 29 + seed) % 256, (index * 61 + seed + 19) % 256,
                   (index * 107 + seed + 43) % 256, 255)
                  for index in range(columns * rows)]
        path = self.root / name
        with Image.new("RGBA", (columns * width, rows * height)) as image:
            for index, color in enumerate(colors):
                left, top = index % columns * width, index // columns * height
                image.paste(color, (left, top, left + width, top + height))
            image.save(path)
        return path, colors

    def assign(self, kind, path, *, variant=None, prepared=None, selected="original"):
        artwork = self.window.document["artwork"]
        if variant:
            artwork = artwork.setdefault("variants", {}).setdefault(variant, {})
        artwork[kind] = {
            "original": import_artwork(path, self.project_file, kind),
            "prepared": import_artwork(prepared, self.project_file, kind) if prepared else None,
            "selected": selected,
        }
        self.window.artwork.refresh()

    def dialog(self, kind=None):
        dialog = self.window.artwork.create_review(kind)
        self.assertIsNotNone(dialog)
        self.dialogs.append(dialog)
        return dialog

    def project_snapshot(self):
        return {
            "document": deepcopy(self.window.document),
            "dirty": self.window.dirty,
            "files": {path.relative_to(self.project_file.parent): path.read_bytes()
                      for path in self.project_file.parent.rglob("*") if path.is_file()},
        }

    def assert_color(self, dialog, sheet, index, color, *, span=1):
        if dialog.kind != sheet.kind:
            dialog.set_kind(sheet.kind)
        pixmap = dialog.grid.frame_image(sheet, index, span=span)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.toImage().pixelColor(0, 0).getRgb(), color)
        return pixmap

    def dialog_with_reference_pair(self):
        portrait, _ = self.sheet("previous-portrait.png", "portrait", 3, seed=13)
        sprite, _ = self.sheet("previous-sprite.png", "sprite", 13, seed=29)
        self.assign("portrait", portrait)
        self.assign("sprite", sprite)
        dialog = self.dialog("sprite")
        self.assertTrue(dialog.load_reference("portrait", portrait, label="Previous portrait"))
        self.assertTrue(dialog.load_reference("sprite", sprite, label="Previous sprite"))
        return dialog

    def test_selected_prepared_sheet_and_current_frame_are_reviewed_without_mutation(self):
        original, _ = self.sheet("original.png", "portrait", 3, scale=2, seed=11)
        prepared, colors = self.sheet("prepared.png", "portrait", 3, seed=97)
        self.assign("portrait", original, prepared=prepared, selected="prepared")
        browser = self.window.artwork.cards["portrait"]["preview"]
        browser.select_frame(4)
        before = self.project_snapshot()

        dialog = self.dialog("portrait")
        self.assertEqual(dialog.kind_tabs.currentIndex(), 0)
        self.assertEqual(dialog.frame.value(), 4)
        self.assertEqual(dialog.grid.selected_index, 4)
        self.assert_color(dialog, dialog.sheets["portrait"], 4, colors[4])
        dialog.select_frame(1)
        dialog.zoom.setCurrentIndex(dialog.zoom.findData(4))
        dialog.background.setCurrentIndex(dialog.background.findData("dark"))
        dialog.close()

        self.assertEqual(browser.current_frame, 4)
        self.assertEqual(self.project_snapshot(), before)
        self.errors.assert_not_called()

    def test_seasonal_review_resolves_each_kind_independently_with_default_fallback(self):
        default_portrait, portrait_colors = self.sheet("default-portrait.png", "portrait", 3, seed=17)
        default_sprite, sprite_colors = self.sheet("default-sprite.png", "sprite", 13, seed=37)
        winter_portrait, winter_colors = self.sheet("winter-portrait.png", "portrait", 3, seed=79)
        summer_sprite, summer_colors = self.sheet("summer-sprite.png", "sprite", 13, seed=127)
        self.assign("portrait", default_portrait)
        self.assign("sprite", default_sprite)
        self.assign("portrait", winter_portrait, variant="winter")
        self.assign("sprite", summer_sprite, variant="summer")

        for variant, expected_portrait, expected_sprite in (
            ("winter", winter_colors, sprite_colors),
            ("summer", portrait_colors, summer_colors),
        ):
            with self.subTest(variant=variant):
                self.window.artwork.select_appearance(variant)
                before = self.project_snapshot()
                dialog = self.dialog()
                self.assert_color(dialog, dialog.sheets["portrait"], 2, expected_portrait[2])
                self.assert_color(dialog, dialog.sheets["sprite"], 18, expected_sprite[18])
                dialog.close()
                self.assertEqual(self.project_snapshot(), before)

    def test_short_reference_keeps_missing_frames_empty_without_clamping(self):
        current, colors = self.sheet("current.png", "sprite", 13, seed=31)
        reference, reference_colors = self.sheet("short-reference.png", "sprite", 4, scale=2, seed=53)
        self.assign("sprite", current)
        dialog = self.dialog("sprite")
        before = self.project_snapshot()
        reference_bytes = reference.read_bytes()

        self.assertTrue(dialog.load_reference("sprite", reference, label="Synthetic reference"))
        self.assert_color(dialog, dialog.references["sprite"], 15, reference_colors[15])
        self.assertTrue(dialog.grid.frame_image(dialog.references["sprite"], 16).isNull())
        self.assert_color(dialog, dialog.sheets["sprite"], 51, colors[51])
        dialog.select_frame(51)
        self.assertEqual(dialog.frame.value(), 51)
        self.assertEqual(dialog.grid.selected_index, 51)
        self.assertEqual(dialog.grid.frame_count, 52)
        self.assertEqual(reference.read_bytes(), reference_bytes)
        self.assertEqual(self.project_snapshot(), before)

    def test_adjacent_frame_pair_stays_within_its_sheet_row(self):
        current, colors = self.sheet("paired.png", "sprite", 13, seed=43)
        self.assign("sprite", current)
        dialog = self.dialog("sprite")
        dialog.paired.setChecked(True)
        dialog.select_frame(40)

        pair = self.assert_color(dialog, dialog.sheets["sprite"], 40, colors[40], span=2)
        self.assertEqual(pair.width(), pair.height())
        self.assertEqual(pair.toImage().pixelColor(pair.width() - 1, 0).getRgb(), colors[41])
        dialog.select_frame(43)
        self.assertEqual(dialog.frame.value(), 43)
        self.assertTrue(dialog.grid.frame_image(dialog.sheets["sprite"], 43, span=2).isNull())

    def test_rejected_reference_retains_last_valid_reference_and_reports_error(self):
        current, _ = self.sheet("current-reference-test.png", "portrait", 3)
        reference, colors = self.sheet("reference.png", "portrait", 3, seed=97)
        invalid = self.root / "broken.png"
        invalid.write_bytes(b"not a PNG")
        self.assign("portrait", current)
        dialog = self.dialog("portrait")
        self.assertTrue(dialog.load_reference("portrait", reference))
        previous = dialog.references["portrait"]
        before = self.project_snapshot()

        self.assertFalse(dialog.load_reference("portrait", invalid))
        self.assertIs(dialog.references["portrait"], previous)
        self.assert_color(dialog, dialog.references["portrait"], 2, colors[2])
        self.assertTrue(dialog.status.text())
        self.assertIn("PNG", dialog.status.text())
        self.assertEqual(self.project_snapshot(), before)

    def test_reference_clear_applies_only_to_current_kind(self):
        portrait, _ = self.sheet("clear-portrait.png", "portrait", 3)
        sprite, _ = self.sheet("clear-sprite.png", "sprite", 13)
        self.assign("portrait", portrait)
        self.assign("sprite", sprite)
        dialog = self.dialog("portrait")
        self.assertTrue(dialog.load_reference("portrait", portrait))
        self.assertTrue(dialog.load_reference("sprite", sprite))

        dialog.clear_reference()
        self.assertNotIn("portrait", dialog.references)
        self.assertIn("sprite", dialog.references)
        dialog.set_kind("sprite")
        dialog.clear_reference()
        self.assertFalse(dialog.references)

    def test_grid_click_and_frame_controls_stay_synchronized_at_changed_zoom(self):
        current, _ = self.sheet("navigation.png", "sprite", 13)
        self.assign("sprite", current)
        dialog = self.dialog("sprite")
        dialog.show()
        self.application.processEvents()
        dialog.zoom.setCurrentIndex(dialog.zoom.findData(4))
        dialog.background.setCurrentIndex(dialog.background.findData("light"))
        self.application.processEvents()
        target = dialog.grid.frame_rect(5)
        self.assertFalse(target.isEmpty())
        QTest.mouseClick(dialog.grid, Qt.MouseButton.LeftButton, pos=target.center())
        self.assertEqual(dialog.frame.value(), 5)
        self.assertEqual(dialog.grid.selected_index, 5)
        self.assertEqual(dialog.detail.selected_index, 5)
        dialog.frame.setValue(48)
        self.assertEqual(dialog.grid.selected_index, 48)
        self.assertEqual(dialog.detail.selected_index, 48)

    def test_walking_playback_uses_selected_direction_and_stops_on_close(self):
        current, _ = self.sheet("walking.png", "sprite", 13)
        self.assign("sprite", current)
        dialog = self.dialog("sprite")
        dialog.show()
        self.application.processEvents()
        dialog.direction.setCurrentIndex(2)
        dialog.speed.setValue(10)
        QTest.mouseClick(dialog.play_button, Qt.MouseButton.LeftButton)
        self.assertTrue(dialog.timer.isActive())
        self.assertEqual(dialog.timer.interval(), 100)
        dialog.advance_frame()
        self.assertIn(dialog.frame.value(), range(8, 12))
        dialog.close()
        self.assertFalse(dialog.timer.isActive())
        self.assertFalse(dialog.play_button.isChecked())

    def test_html_export_is_self_contained_and_does_not_modify_project_or_reference(self):
        current, _ = self.sheet("export-current.png", "sprite", 13, seed=31)
        reference, _ = self.sheet("export-reference.png", "sprite", 13, seed=83)
        self.assign("sprite", current)
        dialog = self.dialog("sprite")
        self.assertTrue(dialog.load_reference("sprite", reference, label="Synthetic comparison"))
        before = self.project_snapshot()
        reference_bytes = reference.read_bytes()
        path = dialog.export_to(self.root / "review.txt")

        self.assertEqual(Path(path).suffix, ".html")
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Synthetic comparison", html)
        self.assertNotIn(str(self.root), html)
        self.assertEqual(reference.read_bytes(), reference_bytes)
        self.assertEqual(self.project_snapshot(), before)
        self.errors.assert_not_called()

    def test_empty_unsaved_project_review_does_not_prompt_to_save_or_create_a_variant(self):
        self.window.load_document(new_project())
        self.window.artwork.select_appearance("winter")
        before = self.project_snapshot()

        with patch.object(self.window, "ensure_saved") as ensure_saved:
            dialog = self.dialog()
            self.assertFalse(dialog.sheets)
            self.assertEqual(dialog.grid.frame_count, 0)
            self.assertFalse(dialog.frame.isEnabled())
            self.assertFalse(dialog.save_button.isEnabled())
            dialog.close()

        ensure_saved.assert_not_called()
        self.assertIsNone(self.window.project_file)
        self.assertEqual(self.project_snapshot(), before)
        self.errors.assert_not_called()

    def test_invalid_current_sheet_is_explained_while_other_kind_remains_reviewable(self):
        invalid = self.root / "partial-row.png"
        with Image.new("RGBA", (128, 191), (31, 63, 97, 255)) as image:
            image.save(invalid)
        sprite, colors = self.sheet("valid-other-kind.png", "sprite", 13, seed=59)
        self.assign("portrait", invalid)
        self.assign("sprite", sprite)
        before = self.project_snapshot()

        dialog = self.dialog("sprite")
        self.assertNotIn("portrait", dialog.sheets)
        self.assert_color(dialog, dialog.sheets["sprite"], 50, colors[50])
        self.assertIn("Portrait:", dialog.status.text())
        self.assertIn("partial rows", dialog.status.text())
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.set_kind("portrait")
        self.assertEqual(dialog.grid.frame_count, 0)
        self.assertFalse(dialog.frame.isEnabled())
        self.assertEqual(self.project_snapshot(), before)

    def test_default_zoom_comparisons_fit_grid_width_at_normal_window_size(self):
        portrait, _ = self.sheet("layout-portrait.png", "portrait", 3)
        sprite, _ = self.sheet("layout-sprite.png", "sprite", 13)
        self.assign("portrait", portrait)
        self.assign("sprite", sprite)
        dialog = self.dialog("sprite")
        dialog.resize(1120, 840)
        dialog.show()
        self.application.processEvents()

        for kind, path in (("sprite", sprite), ("portrait", portrait)):
            with self.subTest(kind=kind):
                dialog.set_kind(kind)
                self.assertTrue(dialog.load_reference(kind, path))
                self.application.processEvents()
                self.assertEqual(dialog.grid_scroll.horizontalScrollBar().maximum(), 0)
                card = dialog.grid.frame_rect(0)
                self.assertLessEqual(card.right(), dialog.grid_scroll.viewport().width())

    def test_accepted_game_reference_replaces_both_review_sheets_without_importing(self):
        dialog = self.dialog_with_reference_pair()
        portrait, portrait_colors = self.sheet("game-portrait.png", "portrait", 5, seed=101)
        sprite, sprite_colors = self.sheet("game-sprite.png", "sprite", 14, seed=151)
        before = self.project_snapshot()
        source_bytes = {path: path.read_bytes() for path in (portrait, sprite)}

        with patch("pixelheart.artwork_review.ArtworkTemplateDialog") as template_dialog, \
                patch.object(self.window.artwork, "apply_template") as apply_template:
            picker = template_dialog.return_value
            picker.exec.return_value = QDialog.DialogCode.Accepted
            picker.loaded = {"portrait": portrait, "sprite": sprite, "name": "Synthetic NPC"}
            dialog.compare_game()

        template_dialog.assert_called_once_with(dialog.appearance, dialog, comparison=True)
        picker.deleteLater.assert_called_once_with()
        apply_template.assert_not_called()
        self.assertEqual(set(dialog.references), {"portrait", "sprite"})
        self.assertEqual(dialog.references["portrait"].label, "Synthetic NPC")
        self.assertEqual(dialog.references["sprite"].label, "Synthetic NPC")
        self.assert_color(dialog, dialog.references["portrait"], 9, portrait_colors[9])
        self.assert_color(dialog, dialog.references["sprite"], 55, sprite_colors[55])
        self.assertEqual(self.project_snapshot(), before)
        self.assertEqual({path: path.read_bytes() for path in source_bytes}, source_bytes)
        self.errors.assert_not_called()

    def test_canceled_game_reference_keeps_both_previous_review_sheets(self):
        dialog = self.dialog_with_reference_pair()
        previous = dict(dialog.references)
        before = self.project_snapshot()

        with patch("pixelheart.artwork_review.ArtworkTemplateDialog") as template_dialog:
            picker = template_dialog.return_value
            picker.exec.return_value = QDialog.DialogCode.Rejected
            picker.loaded = None
            dialog.compare_game()

        picker.deleteLater.assert_called_once_with()
        for kind, sheet in previous.items():
            self.assertIs(dialog.references[kind], sheet)
        self.assertEqual(self.project_snapshot(), before)
        self.errors.assert_not_called()

    def test_invalid_second_game_sheet_preserves_previous_reference_pair_atomically(self):
        dialog = self.dialog_with_reference_pair()
        portrait, _ = self.sheet("new-game-portrait.png", "portrait", 4, seed=179)
        invalid_sprite = self.root / "bad-game-sprite.png"
        invalid_sprite.write_bytes(b"invalid synthetic PNG")
        previous = dict(dialog.references)
        before = self.project_snapshot()

        with patch("pixelheart.artwork_review.ArtworkTemplateDialog") as template_dialog:
            picker = template_dialog.return_value
            picker.exec.return_value = QDialog.DialogCode.Accepted
            picker.loaded = {"portrait": portrait, "sprite": invalid_sprite, "name": "Invalid pair"}
            dialog.compare_game()

        picker.deleteLater.assert_called_once_with()
        for kind, sheet in previous.items():
            self.assertIs(dialog.references[kind], sheet)
        self.assertIn("Could not use reference", dialog.status.text())
        self.assertIn("PNG", dialog.status.text())
        self.assertFalse(dialog.status.isHidden())
        self.assertEqual(self.project_snapshot(), before)
        self.errors.assert_not_called()


if __name__ == "__main__":
    unittest.main()
