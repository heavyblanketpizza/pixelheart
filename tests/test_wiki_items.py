import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.request import Request

from PIL import Image

from pixelheart_core import wiki_items as wiki


def png_bytes(size=(48, 48), color=(120, 80, 190, 255)):
    output = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(output, "PNG")
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, payload, url, headers=None):
        super().__init__(payload)
        self.url = url
        self.headers = {"Content-Length": str(len(payload))} if headers is None else headers

    def geturl(self):
        return self.url


class WikiItemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cache = self.directory / "cache"
        self.sources = {
            "24": {"url": "https://stardewvalleywiki.com/mediawiki/images/d/db/Parsnip.png",
                   "source_url": "https://stardewvalleywiki.com/File:Parsnip.png"},
            "72": {"url": "https://stardewvalleywiki.com/mediawiki/images/e/ea/Diamond.png",
                   "source_url": "https://stardewvalleywiki.com/File:Diamond.png"},
        }
        self.metadata = self.directory / "sources.json"
        self.metadata.write_text(json.dumps({"version": 1, "source_url": "https://stardewvalleywiki.com/", "items": self.sources}))
        self.addCleanup(patch.stopall)
        patch.object(wiki, "_SOURCES_PATH", self.metadata).start()
        self.payload = png_bytes()
        self.opener = Mock()
        self.opener.open.side_effect = lambda request, timeout: Response(self.payload, request.full_url)
        self.build_opener = patch.object(wiki, "build_opener", return_value=self.opener).start()

    def download(self, ids=("24", "72"), **kwargs):
        return wiki.download_item_icons(ids, self.cache, **kwargs)

    def test_metadata_and_cache_lookup_make_no_directory_or_network_request(self):
        sources = wiki.wiki_image_sources()
        self.assertEqual(sources, self.sources)
        sources["24"]["url"] = "modified"
        self.assertEqual(wiki.wiki_image_sources(), self.sources)
        self.assertIsNone(wiki.cached_item_icon("24", self.cache))
        self.assertIsNone(wiki.cached_item_icon("Mod.Item", self.cache))
        self.assertFalse(self.cache.exists())
        self.build_opener.assert_not_called()

    def test_downloads_valid_pngs_atomically_with_progress_on_calling_thread(self):
        observed = []
        caller = threading.get_ident()
        result = self.download(progress=lambda item_id, path: observed.append((item_id, path, threading.get_ident())))
        self.assertEqual(result, {"downloaded": 2, "cached": 0, "failed": {}, "cancelled": False, "total": 2})
        self.assertEqual({item_id for item_id, _, _ in observed}, {"24", "72"})
        for item_id, path, callback_thread in observed:
            self.assertEqual(callback_thread, caller)
            self.assertEqual(path.parent, self.cache.resolve())
            self.assertEqual(path.read_bytes(), self.payload)
            self.assertEqual(wiki.cached_item_icon(item_id, self.cache), path)
        self.assertEqual(len(list(self.cache.iterdir())), 2)
        self.assertFalse(list(self.cache.glob("*.tmp")))

    def test_reuses_cached_icons_offline_without_network(self):
        self.download()
        self.build_opener.reset_mock()
        self.opener.open.side_effect = URLError("offline")
        result = self.download()
        self.assertEqual(result["cached"], 2)
        self.assertEqual(result["failed"], {})
        self.build_opener.assert_not_called()

    def test_duplicate_ids_download_once_and_unknown_ids_fail_without_request(self):
        progress = []
        result = self.download(["24", "24", "Mod.Item"], progress=lambda *args: progress.append(args))
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["downloaded"], 1)
        self.assertIn("Mod.Item", result["failed"])
        self.assertIn(("Mod.Item", None), progress)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_invalid_id_containers_have_clear_errors(self):
        for ids in ("24", None, [["24"]], [24]):
            with self.subTest(ids=ids), self.assertRaises(wiki.WikiItemError):
                self.download(ids)
        self.build_opener.assert_not_called()

    def test_corrupt_cache_is_not_returned_and_can_be_replaced(self):
        self.download(["24"])
        path = wiki.cached_item_icon("24", self.cache)
        path.write_bytes(b"not an image")
        self.assertIsNone(wiki.cached_item_icon("24", self.cache))
        self.opener.open.reset_mock()
        result = self.download(["24"])
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(path.read_bytes(), self.payload)

    def test_failure_for_one_icon_keeps_successful_icons_and_reports_retryable_error(self):
        def response(request, timeout):
            if request.full_url == self.sources["24"]["url"]:
                raise URLError("offline")
            return Response(self.payload, request.full_url)
        self.opener.open.side_effect = response
        result = self.download()
        self.assertEqual(result["downloaded"], 1)
        self.assertIn("24", result["failed"])
        self.assertIsNone(wiki.cached_item_icon("24", self.cache))
        self.assertIsNotNone(wiki.cached_item_icon("72", self.cache))

    def test_cancel_before_start_makes_no_cache_or_request(self):
        result = self.download(cancelled=lambda: True)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["downloaded"], 0)
        self.assertFalse(self.cache.exists())
        self.build_opener.assert_not_called()

    def test_cancel_during_download_does_not_save_partial_image_or_schedule_more(self):
        stopped = threading.Event()
        def response(request, timeout):
            stopped.set()
            return Response(self.payload, request.full_url)
        self.opener.open.side_effect = response
        with patch.object(wiki, "MAX_WORKERS", 1):
            result = self.download(cancelled=stopped.is_set)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["downloaded"], 0)
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.opener.open.call_count, 1)

    def test_cancel_after_first_saved_icon_preserves_it_for_retry(self):
        stopped = threading.Event()
        with patch.object(wiki, "MAX_WORKERS", 1):
            result = self.download(cancelled=stopped.is_set, progress=lambda *_: stopped.set())
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["downloaded"], 1)
        self.assertIsNotNone(wiki.cached_item_icon("24", self.cache))
        self.assertIsNone(wiki.cached_item_icon("72", self.cache))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_source_checkout_git_worktree_and_link_into_repository_are_refused(self):
        checkout = self.directory / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: /elsewhere")
        alias = self.directory / "alias"
        alias.symlink_to(checkout, target_is_directory=True)
        for cache in (wiki._SOURCE_ROOT / "icons", checkout / "icons", alias / "icons"):
            with self.subTest(cache=cache):
                with self.assertRaisesRegex(wiki.WikiItemError, "repositor"):
                    wiki.download_item_icons(["24"], cache)
                with self.assertRaisesRegex(wiki.WikiItemError, "repositor"):
                    wiki.cached_item_icon("24", cache)
        self.build_opener.assert_not_called()
        self.assertFalse((checkout / "icons").exists())

    def test_linked_cache_root_is_refused(self):
        target = self.directory / "target"
        target.mkdir()
        self.cache.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(wiki.WikiItemError, "linked folder"):
            self.download()
        self.assertEqual(list(target.iterdir()), [])

    def test_linked_and_nonregular_cached_files_are_not_read_or_replaced(self):
        self.cache.mkdir()
        path = wiki._icon_path(self.cache, self.sources["24"])
        target = self.directory / "original.png"
        target.write_bytes(self.payload)
        path.symlink_to(target)
        result = self.download(["24"])
        self.assertIn("24", result["failed"])
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_bytes(), self.payload)
        path.unlink()
        path.mkdir()
        result = self.download(["24"])
        self.assertIn("24", result["failed"])
        self.build_opener.assert_not_called()

    def test_bad_images_do_not_create_cache(self):
        jpeg = io.BytesIO()
        Image.new("RGB", (16, 16)).save(jpeg, "JPEG")
        animated = io.BytesIO()
        Image.new("RGBA", (16, 16), "red").save(animated, "PNG", save_all=True,
            append_images=[Image.new("RGBA", (16, 16), "blue")], duration=100, loop=0)
        for payload in (b"<html>rate limited</html>", self.payload[:-12], jpeg.getvalue(), png_bytes((257, 1)), animated.getvalue()):
            with self.subTest(length=len(payload)):
                self.opener.open.side_effect = lambda request, timeout: Response(payload, request.full_url)
                result = self.download(["24"])
                self.assertIn("24", result["failed"])
                self.assertFalse(self.cache.exists())

    def test_declared_streaming_and_invalid_length_limits(self):
        for payload, headers in ((b"", {"Content-Length": str(wiki.MAX_DOWNLOAD_BYTES + 1)}),
                                 (b"x" * 129, {}), (b"", {"Content-Length": "garbage"})):
            with self.subTest(headers=headers):
                self.opener.open.side_effect = lambda request, timeout: Response(payload, request.full_url, headers)
                with patch.object(wiki, "MAX_DOWNLOAD_BYTES", 128):
                    result = self.download(["24"])
                self.assertIn("24", result["failed"])
                self.assertFalse(self.cache.exists())

    def test_external_redirects_and_final_urls_are_rejected(self):
        handler = wiki._WikiRedirectHandler()
        request = Request(self.sources["24"]["url"])
        for url in ("https://example.com/image.png", "http://stardewvalleywiki.com/mediawiki/images/x.png"):
            with self.subTest(url=url), self.assertRaises(wiki.WikiItemError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        self.opener.open.side_effect = lambda request, timeout: Response(self.payload, "https://example.com/a.png")
        self.assertIn("24", self.download(["24"])["failed"])
        self.assertFalse(self.cache.exists())

    def test_only_known_https_png_urls_can_be_requested(self):
        for url in ("file:///tmp/a.png", "https://stardewvalleywiki.com.evil/mediawiki/images/a.png",
                    "https://user:secret@stardewvalleywiki.com/mediawiki/images/a.png",
                    "https://stardewvalleywiki.com:444/mediawiki/images/a.png",
                    "https://stardewvalleywiki.com/mediawiki/images/a.png?q=secret",
                    "https://stardewvalleywiki.com/mediawiki/images/a.gif"):
            with self.subTest(url=url), self.assertRaises(wiki.WikiItemError):
                wiki._download_png(url)
        self.build_opener.assert_not_called()

    def test_broken_or_unsafe_metadata_is_rejected_without_network(self):
        for metadata in ("{}", "not JSON", json.dumps({"version": 1, "items": {"24": {"url": "https://example.com/a.png"}}})):
            self.metadata.write_text(metadata)
            wiki._read_sources.cache_clear()
            with self.assertRaisesRegex(wiki.WikiItemError, "bundled"):
                wiki.wiki_image_sources()
        self.build_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
