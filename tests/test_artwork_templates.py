"""On-demand reference browsing and safe import, without network or real art."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QEventLoop, QThread, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from pixelheart.app import MainWindow
from pixelheart.artwork_templates import ArtworkTemplateDialog
from pixelheart.theme import apply_theme
from pixelheart_core.projects import ProjectError, import_artwork, load_project, resolve_artwork


class ArtworkTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-template-test-")
        self.root = Path(self.temporary.name)
        self.file = self.root / "project" / "character.json"
        self.dialogs = []
        self.release_events = []
        self.window = MainWindow()
        self.errors = self.enterContext(patch.object(MainWindow, "show_error"))
        self.download = self.enterContext(patch("pixelheart.artwork_templates.download_npc_template"))
        self.download.side_effect = AssertionError("Test must explicitly provide a synthetic download")
        self.cache = self.root / "cache"
        self.enterContext(patch("pixelheart.artwork_templates.template_cache_directory", return_value=self.cache))
        self.template = {
            "id": "abigail",
            "name": "Abigail",
            "portrait": self.make_png("reference-portraits.png", (128, 320)),
            "sprite": self.make_png("reference-sprites.png", (64, 448)),
            "source_url": "https://stardewvalleywiki.com/Modding:NPC_data",
        }

    def tearDown(self):
        # Always release synthetic workers, including after a failed assertion.
        for release in self.release_events:
            release.set()
        for dialog in reversed(self.dialogs):
            if dialog.worker is not None:
                dialog.reject()
                self.wait_until(lambda d=dialog: d.worker is None)
            dialog.close()
            dialog.deleteLater()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def wait_until(self, condition, timeout=2500):
        """Process Qt signals until a condition holds, with a bounded deadline."""
        if condition():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if condition() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(condition(), "Qt operation did not finish before the test deadline")

    def make_png(self, name, dimensions, color=(160, 90, 140, 255)):
        path = self.root / name
        with Image.new("RGBA", dimensions, color) as image:
            image.save(path)
        return path

    def make_dialog(self):
        dialog = ArtworkTemplateDialog("Winter", self.window.artwork)
        self.dialogs.append(dialog)
        return dialog

    def click_load(self, dialog):
        QTest.mouseClick(dialog.load_button, Qt.MouseButton.LeftButton)
        self.wait_until(lambda: dialog.worker is None)

    def add_original_artwork(self):
        self.assertTrue(self.window.save_to(self.file))
        for kind, dimensions in (("portrait", (128, 192)), ("sprite", (64, 416))):
            path = self.make_png(f"original-{kind}.png", dimensions, (30, 80, 130, 255))
            relative = import_artwork(path, self.file, kind)
            self.window.document["artwork"][kind] = {
                "original": relative, "prepared": None, "selected": "original",
            }
        self.window.artwork.refresh()
        self.window.dirty = False

    def test_opening_reference_dialog_does_not_download(self):
        before = deepcopy(self.window.document)
        dialog = self.make_dialog()
        self.application.processEvents()
        self.download.assert_not_called()
        self.assertIsNone(dialog.loaded)
        self.assertIsNone(dialog.worker)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIn("ConcernedApe", dialog.credit.text())
        self.assertEqual(self.window.document, before)
        self.assertFalse(self.cache.exists())

    def test_explicit_load_runs_off_gui_thread_and_preview_does_not_change_project(self):
        before = deepcopy(self.window.document)
        before_dirty = self.window.dirty
        threads = []

        def download(*args, **kwargs):
            threads.append(QThread.currentThread())
            return self.template

        self.download.side_effect = download
        dialog = self.make_dialog()
        self.click_load(dialog)
        self.download.assert_called_once()
        self.assertEqual(self.download.call_args.args[1], self.cache)
        self.assertIsNot(threads[0], self.application.thread())
        self.assertTrue(callable(self.download.call_args.kwargs["cancelled"]))
        self.assertEqual(dialog.browsers["portrait"].frame_count, 10)
        self.assertEqual(dialog.browsers["sprite"].frame_count, 56)
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertIn(self.template["source_url"], dialog.sources.text())
        self.assertTrue(dialog.sources.openExternalLinks())
        self.assertIn("ConcernedApe", dialog.credit.text())
        dialog.browsers["portrait"].select_frame(7)
        dialog.browsers["sprite"].select_frame(35)
        dialog.reject()
        self.assertEqual(self.window.document, before)
        self.assertEqual(self.window.dirty, before_dirty)
        self.assertIsNone(self.window.project_file)
        self.errors.assert_not_called()

    def test_switching_to_second_provider_requires_load_and_only_imports_artwork(self):
        self.add_original_artwork()
        self.window.document["character"].update({"name": "Juniper", "gender": "Female", "pronouns": "she/her"})
        self.window.load_document(self.window.document, self.file)
        before = deepcopy(self.window.document)
        elliott = {
            "id": "elliott", "name": "Elliott",
            "portrait": self.make_png("elliott-reference-portraits.png", (128, 256), (60, 120, 170, 255)),
            "sprite": self.make_png("elliott-reference-sprites.png", (64, 416), (60, 120, 170, 255)),
            "provider": "reference-archive", "source_name": "Reference Archive",
            "source_url": "https://reference.example/elliott",
            "attribution": "Artwork © ConcernedApe · sheets from Reference Archive",
        }
        self.enterContext(patch("pixelheart.artwork_templates.WIKI_TEMPLATES", {
            "abigail": self.template, "elliott": elliott,
        }))
        self.download.side_effect = [self.template, elliott]
        dialog = self.make_dialog()
        self.assertEqual(dialog.load_button.text(), "Load template")
        self.assertIn("Stardew Valley Wiki", dialog.source_info.text())
        self.click_load(dialog)
        self.assertTrue(dialog.use_button.isEnabled())

        dialog.character.setCurrentIndex(dialog.character.findData("elliott"))
        self.assertEqual(self.download.call_count, 1)
        self.assertIsNone(dialog.loaded)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertEqual(dialog.browsers["portrait"].frame_count, 0)
        self.assertEqual(dialog.browsers["sprite"].frame_count, 0)
        self.assertEqual(dialog.source_info.text(), "Source: Reference Archive")
        self.assertEqual(dialog.credit.text(), elliott["attribution"])
        self.assertIn(elliott["source_url"], dialog.sources.text())
        self.assertNotIn("Wiki", dialog.sources.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.window.document, before)

        self.click_load(dialog)
        self.assertEqual(self.download.call_count, 2)
        self.assertEqual(self.download.call_args.args[0], "elliott")
        self.assertIs(dialog.loaded, elliott)
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertEqual(dialog.browsers["portrait"].frame_count, 8)
        self.assertEqual(dialog.browsers["sprite"].frame_count, 52)
        self.assertEqual(self.window.document, before)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.window.artwork.apply_template(dialog.loaded))
        self.assertEqual(self.window.document["character"], before["character"])
        for kind in ("portrait", "sprite"):
            record = self.window.document["artwork"][kind]
            self.assertEqual(record["source"]["provider"], "reference-archive")
            self.assertEqual(record["source"]["source_name"], "Reference Archive")
            self.assertEqual(record["source"]["attribution"], elliott["attribution"])
            self.assertEqual(record["source"]["template"], "elliott")
            self.assertEqual(resolve_artwork(self.window.document, self.file, kind).read_bytes(), elliott[kind].read_bytes())
            caption = self.window.artwork.cards[kind]["info"].text()
            self.assertIn("Reference Archive", caption)
            self.assertNotIn("Wiki", caption)
        self.errors.assert_not_called()

    def test_failed_download_can_retry_and_cannot_be_used_until_loaded(self):
        self.download.side_effect = [OSError("network unavailable"), self.template]
        dialog = self.make_dialog()
        self.click_load(dialog)
        self.assertIsNone(dialog.loaded)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertTrue(dialog.load_button.isEnabled())
        self.assertTrue(dialog.character.isEnabled())
        self.assertIn("network unavailable", dialog.status.text())
        self.assertIn("retry", dialog.status.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.click_load(dialog)
        self.assertEqual(self.download.call_count, 2)
        self.assertIs(dialog.loaded, self.template)
        self.assertTrue(dialog.use_button.isEnabled())

    def test_cancel_during_download_waits_for_worker_and_ignores_late_result(self):
        entered = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        cancellations = []

        def download(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("Synthetic worker was not released")
            cancellations.append(kwargs["cancelled"]())
            return self.template

        self.download.side_effect = download
        dialog = self.make_dialog()
        dialog.show()
        QTest.mouseClick(dialog.load_button, Qt.MouseButton.LeftButton)
        self.wait_until(entered.is_set)
        self.assertFalse(dialog.use_button.isEnabled())
        cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
        self.assertTrue(dialog.closing)
        self.assertIsNotNone(dialog.worker)
        self.assertFalse(dialog.buttons.isEnabled())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        release.set()
        self.wait_until(lambda: dialog.worker is None)
        self.assertEqual(cancellations, [True])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(dialog.loaded)
        self.assertEqual(dialog.browsers["portrait"].frame_count, 0)
        self.assertEqual(dialog.browsers["sprite"].frame_count, 0)

    def test_use_imports_both_sheets_into_selected_appearance_and_retains_credit(self):
        self.add_original_artwork()
        original = deepcopy(self.window.document)
        source_bytes = {kind: self.template[kind].read_bytes() for kind in ("portrait", "sprite")}
        self.window.artwork.select_appearance("winter")
        self.assertTrue(self.window.artwork.apply_template(self.template))
        self.assertEqual(self.window.document["character"], original["character"])
        for kind in ("portrait", "sprite"):
            self.assertEqual(self.window.document["artwork"][kind], original["artwork"][kind])
            record = self.window.document["artwork"]["variants"]["winter"][kind]
            self.assertEqual(record["selected"], "original")
            self.assertEqual(record["source"], {
                "provider": "stardew-wiki", "template": "abigail",
                "creator": "ConcernedApe", "url": self.template["source_url"],
                "source_name": "Stardew Valley Wiki", "attribution": "Artwork © ConcernedApe",
            })
            imported = resolve_artwork(self.window.document, self.file, kind, variant="winter")
            self.assertEqual(imported.read_bytes(), source_bytes[kind])
            self.assertNotEqual(imported.resolve(), self.template[kind].resolve())
            self.assertEqual(self.template[kind].read_bytes(), source_bytes[kind])
            self.assertIn("ConcernedApe", self.window.artwork.cards[kind]["info"].text())
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.save())
        saved = load_project(self.file)
        self.assertEqual(saved["artwork"], self.window.document["artwork"])
        self.errors.assert_not_called()

    def test_second_import_failure_keeps_both_existing_records_and_originals(self):
        self.add_original_artwork()
        before = deepcopy(self.window.document)
        originals = {
            kind: resolve_artwork(before, self.file, kind).read_bytes()
            for kind in ("portrait", "sprite")
        }

        def fail_sprite(source, project_file, kind):
            if kind == "sprite":
                raise ProjectError("Synthetic second import failure")
            return import_artwork(source, project_file, kind)

        with patch("pixelheart.artwork_page.import_artwork", side_effect=fail_sprite) as imports:
            self.assertFalse(self.window.artwork.apply_template(self.template))
        self.assertEqual(imports.call_count, 2)
        self.assertEqual(self.window.document, before)
        self.assertFalse(self.window.dirty)
        for kind, payload in originals.items():
            self.assertEqual(resolve_artwork(self.window.document, self.file, kind).read_bytes(), payload)
        self.errors.assert_called_once_with("Could not use template", "Synthetic second import failure")

    def test_cancelling_project_save_does_not_import_or_change_project(self):
        before = deepcopy(self.window.document)
        with patch.object(self.window, "ensure_saved", return_value=False), \
                patch("pixelheart.artwork_page.import_artwork") as imports:
            self.assertFalse(self.window.artwork.apply_template(self.template))
        imports.assert_not_called()
        self.assertEqual(self.window.document, before)
        self.errors.assert_not_called()

    def test_save_png_copy_preserves_source_and_project(self):
        self.add_original_artwork()
        before = deepcopy(self.window.document)
        source = resolve_artwork(before, self.file, "portrait")
        payload = source.read_bytes()
        destination = self.root / "editable portrait"
        with patch("pixelheart.artwork_page.QFileDialog.getSaveFileName", return_value=(str(destination), "PNG artwork (*.png)")):
            self.window.artwork.save_png_copy("portrait")
        self.assertEqual(destination.with_suffix(".png").read_bytes(), payload)
        self.assertEqual(source.read_bytes(), payload)
        self.assertEqual(self.window.document, before)
        self.assertFalse(self.window.dirty)
        self.errors.assert_not_called()

    def test_save_png_copy_refuses_to_overwrite_imported_source(self):
        self.add_original_artwork()
        source = resolve_artwork(self.window.document, self.file, "portrait")
        payload = source.read_bytes()
        with patch("pixelheart.artwork_page.QFileDialog.getSaveFileName", return_value=(str(source), "PNG artwork (*.png)")):
            self.window.artwork.save_png_copy("portrait")
        self.assertEqual(source.read_bytes(), payload)
        self.errors.assert_called_once()
        self.assertIn("separate file", self.errors.call_args.args[1])

    def test_save_png_copy_protects_other_kinds_and_appearance_sources(self):
        self.add_original_artwork()
        self.window.artwork.select_appearance("winter")
        self.assertTrue(self.window.artwork.apply_template(self.template))
        self.window.artwork.select_appearance()
        for variant, kind in ((None, "sprite"), ("winter", "portrait"), ("winter", "sprite")):
            with self.subTest(variant=variant, kind=kind):
                source = resolve_artwork(self.window.document, self.file, kind, variant=variant)
                payload = source.read_bytes()
                with patch("pixelheart.artwork_page.QFileDialog.getSaveFileName", return_value=(str(source), "PNG artwork (*.png)")):
                    self.window.artwork.save_png_copy("portrait")
                self.assertEqual(source.read_bytes(), payload)
                self.assertIn("separate file", self.errors.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
