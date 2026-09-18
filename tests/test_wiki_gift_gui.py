"""Real background-worker/UI integration with synthetic PNGs and no network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from pixelheart.app import MainWindow
from pixelheart.gifts_page import GiftsPage, vanilla_catalog
from pixelheart.item_icons import ItemIconStore, export_filename
from pixelheart_core.wiki_items import WikiItemError, download_item_icons, wiki_image_sources


class WikiGiftGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelheart-wiki-gui-")
        self.root = Path(self.temporary.name)
        self.widgets = []
        self.releases = []
        self.snapshot = vanilla_catalog()
        self.records = {record["id"]: record for record in self.snapshot["items"]}
        self.sources = wiki_image_sources()
        self.local_cache = self.root / "local-textures"
        self.wiki_cache = self.root / "wiki-icons"
        self.store_patch = patch(
            "pixelheart.gifts_page.ItemIconStore",
            side_effect=lambda: ItemIconStore(self.local_cache, self.wiki_cache),
        )
        self.store_patch.start()
        # Any request not explicitly replaced by a test is an immediate error.
        self.network_patch = patch(
            "pixelheart_core.wiki_items._download_png", side_effect=AssertionError("Unexpected network request"),
        )
        self.network_guard = self.network_patch.start()
        output = io.BytesIO()
        Image.new("RGBA", (48, 48), (205, 60, 120, 255)).save(output, format="PNG")
        self.png = output.getvalue()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for widget in reversed(self.widgets):
            page = widget.gifts if isinstance(widget, MainWindow) else widget
            page.cancel_wiki_download()
            self.wait_until(lambda: page.wiki_worker is None)
            if isinstance(widget, MainWindow):
                widget.dirty = False
            widget.close()
            widget.deleteLater()
        self.application.processEvents()
        self.network_patch.stop()
        self.store_patch.stop()
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), "Background GUI operation did not finish")

    def make_widget(self, *, auto=False, identifiers=("66",), window=False):
        snapshot = deepcopy(self.snapshot)
        snapshot["items"] = [deepcopy(self.records[item_id]) for item_id in identifiers]
        with patch("pixelheart.gifts_page.vanilla_catalog", return_value=snapshot):
            widget = MainWindow(auto_download_icons=auto) if window else GiftsPage(auto_download=auto)
        self.widgets.append(widget)
        widget.resize(1200, 850)
        return widget

    def test_disabled_automatic_download_does_not_start_worker_or_network_on_show(self):
        page = self.make_widget(auto=False)
        page.show()
        QTest.qWait(30)
        self.assertIsNone(page.wiki_worker)
        self.assertEqual(page._wiki_attempted, set())
        self.network_guard.assert_not_called()
        self.assertFalse(self.wiki_cache.exists())

    def test_automatic_download_fetches_only_missing_eligible_icons(self):
        page = self.make_widget(auto=True, identifiers=("66", "395", "Book_Horse"))
        with patch("pixelheart_core.wiki_items._download_png", return_value=self.png):
            download_item_icons(["395"], self.wiki_cache)
        page.icon_store.invalidate_wiki_icons()
        exports = self.root / "exports"
        exports.mkdir()
        book = self.records["Book_Horse"]
        Image.new("RGBA", (16, 16 * (book["icon"]["index"] + 1)), "green").save(
            exports / export_filename(book["icon"]["texture"]),
        )
        page.icon_store.import_folder(exports, page.catalog["items"])
        # This mod reuses a vanilla numeric ID but supplies different art.
        override = deepcopy(self.records["72"])
        override["icon"] = {"texture": "Mods/Example/Ruby", "index": 0}
        unknown = {**override, "id": "Example.Mod_Ruby", "name": "Mod Ruby"}
        page.catalog["items"].extend([override, unknown])
        page._index_catalog()
        with patch("pixelheart_core.wiki_items._download_png", return_value=self.png) as download:
            page.show()
            self.wait_until(lambda: page.icon_store.has_icon(self.records["66"]) and page.wiki_worker is None)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(download.call_args.args[0], self.sources["66"]["url"])
        self.assertEqual(page._wiki_attempted, {"66"})
        self.assertTrue(page.icon_store.has_local_icon(book))
        self.assertFalse(page.icon_store.has_icon(override))

    def test_async_progress_updates_icons_in_place_without_changing_selection_or_dirty_state(self):
        window = self.make_widget(auto=True, window=True)
        page = window.gifts
        page.assign_items(["66"], "love")
        window.dirty = False
        love_item = page.lists["love"].item(0)
        library_item = page.library.item(0)
        love_item.setSelected(True)
        before = deepcopy(page.dump())
        before_icon = love_item.icon().cacheKey()
        edits = QSignalSpy(page.changed)
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def synthetic_download(url, *, cancelled=None):
            started.set()
            if not release.wait(3):
                raise WikiItemError("Test download timed out")
            return self.png

        with patch("pixelheart_core.wiki_items._download_png", side_effect=synthetic_download):
            window.show()
            window.navigation.setCurrentRow(3)
            self.wait_until(started.is_set)
            worker = page.wiki_worker
            self.assertIsNotNone(worker)
            self.assertTrue(worker.isRunning())
            ready = QSignalSpy(worker.item_ready)
            progress = QSignalSpy(worker.progress)
            release.set()
            self.wait_until(lambda: page.wiki_worker is None)
            self.assertEqual(ready.count(), 1)
            self.assertEqual(ready.at(0), ["66"])
            self.assertEqual(progress.at(0), [1, 1])
        self.assertIs(page.lists["love"].item(0), love_item)
        self.assertIs(page.library.item(0), library_item)
        self.assertTrue(love_item.isSelected())
        self.assertIs(page.active_list, page.lists["love"])
        self.assertNotEqual(love_item.icon().cacheKey(), before_icon)
        self.assertEqual(love_item.icon().pixmap(48).toImage().pixelColor(24, 24).name(), "#cd3c78")
        self.assertEqual(page.dump(), before)
        self.assertEqual(edits.count(), 0)
        self.assertFalse(window.dirty)
        self.assertIn("1 of 1", page.icon_status.text())

    def test_failed_download_does_not_automatically_retry_but_button_can_retry(self):
        page = self.make_widget(auto=True)
        with patch("pixelheart_core.wiki_items._download_png", side_effect=WikiItemError("Offline")) as download:
            page.show()
            self.wait_until(lambda: "66" in page._wiki_attempted and page.wiki_worker is None)
            page.hide()
            page.show()
            page._index_catalog()
            QTest.qWait(30)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(page.wiki_button.text(), "Retry icons")
            self.assertTrue(page.wiki_button.isEnabled())
        with patch("pixelheart_core.wiki_items._download_png", return_value=self.png) as download:
            QTest.mouseClick(page.wiki_button, Qt.MouseButton.LeftButton)
            self.wait_until(lambda: page.icon_store.has_icon(self.records["66"]) and page.wiki_worker is None)
            self.assertEqual(download.call_count, 1)

    def test_close_cancels_active_worker_and_waits_without_repeating_discard_confirmation(self):
        window = self.make_widget(auto=True, window=True)
        page = window.gifts
        started, interrupted, release = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)

        def synthetic_download(url, *, cancelled=None):
            started.set()
            deadline = time.monotonic() + 3
            while not cancelled() and time.monotonic() < deadline:
                release.wait(0.005)
            if cancelled():
                interrupted.set()
            if not release.wait(3):
                raise WikiItemError("Test cancellation timed out")
            return self.png

        with patch("pixelheart_core.wiki_items._download_png", side_effect=synthetic_download), \
                patch.object(window, "confirm_discard", return_value=True) as confirm:
            window.show()
            window.navigation.setCurrentRow(3)
            self.wait_until(started.is_set)
            window.dirty = True
            self.assertFalse(window.close())
            self.wait_until(interrupted.is_set)
            self.assertTrue(window.isVisible())
            self.assertFalse(window.isEnabled())
            self.assertTrue(page.is_downloading_icons())
            self.assertFalse(window.close())
            self.assertEqual(confirm.call_count, 1)
            release.set()
            self.wait_until(lambda: not window.isVisible() and page.wiki_worker is None)
            self.assertEqual(confirm.call_count, 1)
        self.assertFalse(page.icon_store.has_icon(self.records["66"]))


if __name__ == "__main__":
    unittest.main()
