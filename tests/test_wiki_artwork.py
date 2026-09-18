import hashlib
import io
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.request import Request

from PIL import Image

from pixelheart_core import wiki_artwork as wiki


def png_bytes(size, color=(180, 80, 150, 255)):
    buffer = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class Response(io.BytesIO):
    def __init__(self, payload, url, headers=None):
        super().__init__(payload)
        self.url = url
        self.headers = headers if headers is not None else {"Content-Length": str(len(payload))}

    def geturl(self):
        return self.url


class WikiArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cache = self.directory / "cache"
        self.template = dict(wiki.WIKI_TEMPLATES["abigail"])
        self.payloads = {
            "portrait": png_bytes(self.template["portrait_size"]),
            "sprite": png_bytes(self.template["sprite_size"]),
        }
        for kind, payload in self.payloads.items():
            self.template[f"{kind}_sha256"] = hashlib.sha256(payload).hexdigest()
        self.addCleanup(patch.stopall)
        patch.dict(wiki.WIKI_TEMPLATES, {"abigail": self.template}, clear=True).start()
        self.opener = Mock()
        self.opener.open.side_effect = self.response
        self.build_opener = patch.object(wiki, "build_opener", return_value=self.opener).start()

    def response(self, request, timeout):
        self.assertEqual(timeout, wiki.NETWORK_TIMEOUT_SECONDS)
        for kind in self.payloads:
            if request.full_url == self.template[f"{kind}_url"]:
                return Response(self.payloads[kind], request.full_url)
        self.fail("Unexpected download URL")

    def download(self, **kwargs):
        return wiki.download_npc_template("abigail", self.cache, **kwargs)

    def test_fetches_complete_pair_and_returns_paths_with_attribution(self):
        self.assertFalse(self.build_opener.called)
        result = self.download()
        self.assertEqual(result["id"], "abigail")
        self.assertEqual(result["name"], "Abigail")
        self.assertEqual(result["source_url"], self.template["source_url"])
        self.assertIn("ConcernedApe", result["attribution"])
        for kind, payload in self.payloads.items():
            self.assertEqual(result[kind], self.cache.resolve() / "abigail" / f"{kind}.png")
            self.assertEqual(result[kind].read_bytes(), payload)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertFalse(list(self.cache.rglob("*.tmp")))

    def test_validated_cached_pair_is_reused_without_network(self):
        first = self.download()
        self.build_opener.reset_mock()
        self.opener.open.side_effect = URLError("offline")
        self.assertEqual(self.download(), first)
        self.build_opener.assert_not_called()

    def test_corrupt_cache_is_replaced_and_valid_other_sheet_preserved(self):
        result = self.download()
        result["portrait"].write_bytes(b"broken")
        original_sprite = result["sprite"].stat().st_mtime_ns
        self.opener.open.reset_mock()
        self.download()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(result["portrait"].read_bytes(), self.payloads["portrait"])
        self.assertEqual(result["sprite"].stat().st_mtime_ns, original_sprite)

    def test_failure_on_second_download_leaves_no_partial_pair(self):
        self.opener.open.side_effect = [
            Response(self.payloads["portrait"], self.template["portrait_url"]),
            URLError("offline"),
        ]
        with self.assertRaisesRegex(wiki.WikiArtworkError, "connection"):
            self.download()
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])

    def test_cancelled_before_start_makes_no_directory_or_request(self):
        with self.assertRaisesRegex(wiki.WikiArtworkError, "cancelled"):
            self.download(cancelled=lambda: True)
        self.assertFalse(self.cache.exists())
        self.build_opener.assert_not_called()

    def test_cancelled_between_downloads_removes_pending_files(self):
        stopped = [False]

        def response(request, timeout):
            if request.full_url == self.template["sprite_url"]:
                stopped[0] = True
            return self.response(request, timeout)

        self.opener.open.side_effect = response
        with self.assertRaisesRegex(wiki.WikiArtworkError, "cancelled"):
            self.download(cancelled=lambda: stopped[0])
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])

    def test_unknown_template_and_path_traversal_make_no_request(self):
        for template_id in ("leah", "../abigail", "", None, [], "Abigail"):
            with self.subTest(template_id=template_id):
                with self.assertRaisesRegex(wiki.WikiArtworkError, "available"):
                    wiki.download_npc_template(template_id, self.cache)
        self.build_opener.assert_not_called()

    def test_cache_in_git_checkout_and_symlink_into_checkout_are_rejected(self):
        checkout = self.directory / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: elsewhere")
        alias = self.directory / "alias"
        alias.symlink_to(checkout, target_is_directory=True)
        for cache in (checkout / "cache", alias / "cache", wiki._SOURCE_ROOT / "wiki-cache"):
            with self.subTest(cache=cache):
                with self.assertRaisesRegex(wiki.WikiArtworkError, "repositor"):
                    wiki.download_npc_template("abigail", cache)
        self.build_opener.assert_not_called()
        self.assertFalse((checkout / "cache").exists())

    def test_linked_template_folder_and_image_are_rejected_without_modifying_target(self):
        self.cache.mkdir()
        target = self.directory / "elsewhere"
        target.mkdir()
        destination = self.cache / "abigail"
        destination.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(wiki.WikiArtworkError, "regular directory"):
            self.download()
        destination.unlink()
        destination.mkdir()
        source = target / "original.png"
        source.write_bytes(self.payloads["portrait"])
        (destination / "portrait.png").symlink_to(source)
        with self.assertRaisesRegex(wiki.WikiArtworkError, "linked image"):
            self.download()
        self.assertEqual(source.read_bytes(), self.payloads["portrait"])
        self.build_opener.assert_not_called()

    def test_template_subdirectory_cannot_itself_be_a_git_checkout(self):
        directory = self.cache / "abigail"
        directory.mkdir(parents=True)
        (directory / ".git").mkdir()
        with self.assertRaisesRegex(wiki.WikiArtworkError, "repositor"):
            self.download()
        self.build_opener.assert_not_called()

    def test_nonregular_cached_image_is_rejected(self):
        (self.cache / "abigail" / "portrait.png").mkdir(parents=True)
        with self.assertRaisesRegex(wiki.WikiArtworkError, "regular file"):
            self.download()
        self.build_opener.assert_not_called()

    def test_invalid_url_is_rejected_before_opening_connection(self):
        urls = (
            "http://stardewvalleywiki.com/mediawiki/images/test.png",
            "https://stardewvalleywiki.com.evil.example/mediawiki/images/test.png",
            "https://example.com/mediawiki/images/test.png",
            "file:///etc/passwd",
            "https://user:secret@stardewvalleywiki.com/mediawiki/images/test.png",
            "https://stardewvalleywiki.com:444/mediawiki/images/test.png",
            "https://stardewvalleywiki.com/not-an-image",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(wiki.WikiArtworkError, "verified"):
                    wiki._download_png(url)
        self.build_opener.assert_not_called()

    def test_redirects_to_other_hosts_or_non_https_are_rejected(self):
        handler = wiki._WikiRedirectHandler()
        request = Request(self.template["portrait_url"])
        for url in ("https://evil.example/image.png", self.template["sprite_url"].replace("https:", "http:")):
            with self.subTest(url=url):
                with self.assertRaisesRegex(wiki.WikiArtworkError, "verified"):
                    handler.redirect_request(request, None, 302, "Found", {}, url)
        self.assertEqual(handler.redirects, 0)

    def test_verified_redirects_are_limited(self):
        handler = wiki._WikiRedirectHandler()
        request = Request(self.template["portrait_url"])
        for _ in range(3):
            redirected = handler.redirect_request(request, None, 302, "Found", {}, self.template["sprite_url"])
            self.assertEqual(redirected.full_url, self.template["sprite_url"])
        with self.assertRaisesRegex(wiki.WikiArtworkError, "too many"):
            handler.redirect_request(request, None, 302, "Found", {}, self.template["sprite_url"])

    def test_unverified_final_url_is_rejected(self):
        self.opener.open.side_effect = [Response(self.payloads["portrait"], "https://evil.example/image.png")]
        with self.assertRaisesRegex(wiki.WikiArtworkError, "verified"):
            self.download()

    def test_declared_and_streamed_byte_limits_are_enforced(self):
        for payload, headers in ((b"", {"Content-Length": str(wiki.MAX_DOWNLOAD_BYTES + 1)}), (b"x" * 129, {})):
            with self.subTest(headers=headers):
                self.opener.open.side_effect = [Response(payload, self.template["portrait_url"], headers)]
                with patch.object(wiki, "MAX_DOWNLOAD_BYTES", 128):
                    with self.assertRaisesRegex(wiki.WikiArtworkError, "limit"):
                        self.download()

    def test_invalid_content_length_is_actionable(self):
        self.opener.open.side_effect = [Response(b"", self.template["portrait_url"], {"Content-Length": "invalid"})]
        with self.assertRaisesRegex(wiki.WikiArtworkError, "invalid image length"):
            self.download()

    def test_deadline_stops_download(self):
        with patch.object(wiki.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(wiki.WikiArtworkError, "timed out"):
                self.download()

    def test_invalid_truncated_and_single_portrait_images_are_rejected(self):
        for payload in (b"<html>error</html>", self.payloads["portrait"][:-20], png_bytes((128, 128))):
            with self.subTest(length=len(payload)):
                self.opener.open.side_effect = [Response(payload, self.template["portrait_url"])]
                with self.assertRaises(wiki.WikiArtworkError):
                    self.download()
                self.assertFalse(list(self.cache.rglob("*.png")))

    def test_changed_sheet_is_not_silently_accepted(self):
        self.payloads["portrait"] = png_bytes(self.template["portrait_size"], (0, 0, 0, 255))
        with self.assertRaisesRegex(wiki.WikiArtworkError, "changed since"):
            self.download()

    def test_pixel_limit_and_animated_sheets_are_rejected(self):
        with patch.object(wiki, "MAX_IMAGE_PIXELS", 1):
            with self.assertRaisesRegex(wiki.WikiArtworkError, "safe image size"):
                self.download()
        buffer = io.BytesIO()
        with Image.new("RGBA", self.template["portrait_size"], (255, 0, 0, 255)) as first:
            with Image.new("RGBA", first.size, (0, 0, 255, 255)) as second:
                first.save(buffer, format="PNG", save_all=True, append_images=[second])
        self.payloads["portrait"] = buffer.getvalue()
        with self.assertRaisesRegex(wiki.WikiArtworkError, "static PNG"):
            self.download()

    def test_save_failure_leaves_no_pending_download_files(self):
        with patch.object(wiki.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(wiki.WikiArtworkError, "writable"):
                self.download()
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])


class MultiSourceArtworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "cache"
        self.templates = deepcopy(wiki.WIKI_TEMPLATES)
        self.payloads = {}
        for index, template in enumerate(self.templates.values()):
            for kind in ("portrait", "sprite"):
                payload = png_bytes(template[f"{kind}_size"], (40 + index * 80, 100, 160, 255))
                self.payloads[template[f"{kind}_url"]] = payload
                template[f"{kind}_sha256"] = hashlib.sha256(payload).hexdigest()
        self.enterContext(patch.dict(wiki.WIKI_TEMPLATES, self.templates, clear=True))
        self.opener = Mock()
        self.opener.open.side_effect = lambda request, timeout: Response(self.payloads[request.full_url], request.full_url)
        self.build_opener = self.enterContext(patch.object(wiki, "build_opener", return_value=self.opener))

    def test_elliott_download_and_offline_cache_are_separate_from_abigail(self):
        abigail = wiki.download_npc_template("abigail", self.cache)
        elliott = wiki.download_npc_template("elliott", self.cache)
        self.assertEqual(self.opener.open.call_count, 4)
        self.assertEqual(elliott["provider"], "stardew-data")
        self.assertIn("GitHub", elliott["source_name"])
        self.assertIn(elliott["source_name"], elliott["attribution"])
        self.assertEqual(abigail["provider"], "stardew-wiki")
        for kind in ("portrait", "sprite"):
            self.assertNotEqual(abigail[kind], elliott[kind])
            self.assertNotEqual(abigail[kind].read_bytes(), elliott[kind].read_bytes())
            self.assertEqual(elliott[kind].read_bytes(), self.payloads[self.templates["elliott"][f"{kind}_url"]])
        self.build_opener.reset_mock()
        self.opener.open.side_effect = URLError("offline")
        self.assertEqual(wiki.download_npc_template("elliott", self.cache), elliott)
        self.assertEqual(wiki.download_npc_template("abigail", self.cache), abigail)
        self.build_opener.assert_not_called()

    def test_github_downloads_and_redirects_require_exact_pinned_catalog_url(self):
        url = self.templates["elliott"]["portrait_url"]
        wiki._validate_url(url)
        commit = url.split("/")[6]
        for destination in (
            url.replace(commit, "main"),
            url.replace("elliott.png", "other.png"),
            url + "?raw=true",
            url.replace("media.githubusercontent.com", "raw.githubusercontent.com"),
        ):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(wiki.WikiArtworkError, "verified"):
                    wiki._download_png(destination)
                with self.assertRaisesRegex(wiki.WikiArtworkError, "verified"):
                    wiki._WikiRedirectHandler().redirect_request(Request(url), None, 302, "Found", {}, destination)
        self.build_opener.assert_not_called()

    def test_different_npc_image_cannot_be_accepted_as_elliott(self):
        elliott_url = self.templates["elliott"]["portrait_url"]
        self.payloads[elliott_url] = self.payloads[self.templates["abigail"]["portrait_url"]]
        with self.assertRaisesRegex(wiki.WikiArtworkError, "changed since"):
            wiki.download_npc_template("elliott", self.cache)
        self.assertFalse(list(self.cache.rglob("*.png")))


if __name__ == "__main__":
    unittest.main()
