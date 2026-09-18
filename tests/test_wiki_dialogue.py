"""Synthetic reference downloads only: never bundle game dialogue fixtures."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.request import Request

from pixelheart_core import wiki_dialogue as wiki


class Response(io.BytesIO):
    def __init__(self, payload, url, headers=None):
        super().__init__(payload)
        self.url = url
        self.headers = headers if headers is not None else {"Content-Length": str(len(payload))}

    def geturl(self):
        return self.url


class WikiDialogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cache = self.directory / "cache"
        self.templates = deepcopy(wiki.DIALOGUE_TEMPLATES)
        self.payloads = {}
        for template_id, template in self.templates.items():
            # A fifth entry proves that every source entry is returned even
            # when it has no dedicated teaching notes.
            entries = {example["trigger"]: f"Synthetic {template_id} {index}.#$b#A test continuation.$h" for index, example in enumerate(template["examples"])}
            entries["ExtraEntry"] = "Synthetic text from the rest of the archived file."
            payload = json.dumps(entries).encode()
            template["sha256"] = hashlib.sha256(payload).hexdigest()
            self.payloads[template["download_url"]] = payload
        self.enterContext(patch.dict(wiki.DIALOGUE_TEMPLATES, self.templates, clear=True))
        self.opener = Mock()
        self.opener.open.side_effect = self.response
        self.build_opener = self.enterContext(patch.object(wiki, "build_opener", return_value=self.opener))

    def response(self, request, timeout):
        self.assertEqual(timeout, wiki.NETWORK_TIMEOUT_SECONDS)
        return Response(self.payloads[request.full_url], request.full_url)

    def download(self, template_id="abigail", **kwargs):
        return wiki.download_dialogue_examples(template_id, self.cache, **kwargs)

    def use_payload(self, payload, template_id="abigail"):
        template = self.templates[template_id]
        template["sha256"] = hashlib.sha256(payload).hexdigest()
        self.payloads[template["download_url"]] = payload

    def test_requested_download_returns_full_annotated_archive_in_source_order(self):
        self.build_opener.assert_not_called()
        for template_id, template in self.templates.items():
            with self.subTest(template_id=template_id):
                result = self.download(template_id)
                self.assertEqual(result["id"], template_id)
                self.assertEqual(result["name"], template["name"])
                self.assertEqual(result["source_url"], template["source_url"])
                self.assertIn("ConcernedApe", result["attribution"])
                source = json.loads(self.payloads[template["download_url"]])
                self.assertEqual(len(result["examples"]), 5)
                self.assertEqual([(e["trigger"], e["text"]) for e in result["examples"]], list(source.items()))
                self.assertEqual(result["examples"][-1]["trigger"], "ExtraEntry")
                for example in result["examples"]:
                    self.assertEqual(set(example), {"trigger", "text", "title", "when", "lesson"})
                    self.assertTrue(all(isinstance(value, str) and value for value in example.values()))
                self.assertTrue((self.cache / template_id / "dialogue.json").is_file())
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertFalse(list(self.cache.rglob("*.tmp")))

    def test_verified_cache_is_reused_offline_and_results_do_not_mutate_catalogue(self):
        first = self.download()
        first["examples"][0]["text"] = "An edit in the learning window"
        first["examples"][0]["title"] = "A local edit"
        self.build_opener.reset_mock()
        self.opener.open.side_effect = URLError("offline")
        result = self.download()
        self.assertNotEqual(first["examples"][0], result["examples"][0])
        self.assertEqual(result["examples"][0]["title"], self.templates["abigail"]["examples"][0]["title"])
        self.build_opener.assert_not_called()

    def test_corrupt_cache_is_refetched_and_other_character_is_unchanged(self):
        self.download()
        self.download("elliott")
        elliott = self.cache / "elliott" / "dialogue.json"
        original_time = elliott.stat().st_mtime_ns
        path = self.cache / "abigail" / "dialogue.json"
        path.write_bytes(b"broken")
        self.opener.open.reset_mock()
        self.download()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(elliott.stat().st_mtime_ns, original_time)
        self.assertEqual(path.read_bytes(), self.payloads[self.templates["abigail"]["download_url"]])

    def test_unknown_ids_are_rejected_before_cache_or_network(self):
        for template_id in ("", "../abigail", "Abigail", "leah", [], None):
            with self.subTest(template_id=template_id):
                with self.assertRaisesRegex(wiki.WikiDialogueError, "available"):
                    self.download(template_id)
        self.assertFalse(self.cache.exists())
        self.build_opener.assert_not_called()

    def test_failure_and_cancellation_do_not_leave_a_partial_download(self):
        with self.assertRaisesRegex(wiki.WikiDialogueError, "cancelled"):
            self.download(cancelled=lambda: True)
        self.assertFalse(self.cache.exists())
        self.build_opener.assert_not_called()
        self.opener.open.side_effect = URLError("offline")
        with self.assertRaisesRegex(wiki.WikiDialogueError, "connection"):
            self.download()
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])
        stopped = [False]

        def cancelled_response(request, timeout):
            stopped[0] = True
            return self.response(request, timeout)

        self.opener.open.side_effect = cancelled_response
        with self.assertRaisesRegex(wiki.WikiDialogueError, "cancelled"):
            self.download(cancelled=lambda: stopped[0])
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])

    def test_cancelled_before_cache_commit_removes_temporary_file(self):
        stopped = [False]
        original = wiki.tempfile.NamedTemporaryFile

        def temporary_file(**kwargs):
            output = original(**kwargs)
            stopped[0] = True
            return output

        with patch.object(wiki.tempfile, "NamedTemporaryFile", side_effect=temporary_file):
            with self.assertRaisesRegex(wiki.WikiDialogueError, "cancelled"):
                self.download(cancelled=lambda: stopped[0])
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])

    def test_cache_rejects_repository_paths_and_symlinks_into_repositories(self):
        checkout = self.directory / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: elsewhere")
        alias = self.directory / "alias"
        alias.symlink_to(checkout, target_is_directory=True)
        for path in (checkout / "cache", alias / "cache", wiki._SOURCE_ROOT / "dialogue-cache"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(wiki.WikiDialogueError, "repositor"):
                    wiki.download_dialogue_examples("abigail", path)
        self.build_opener.assert_not_called()
        self.assertFalse((checkout / "cache").exists())

    def test_cache_rejects_linked_or_nonregular_files_and_character_folders(self):
        target = self.directory / "target"
        target.mkdir()
        self.cache.mkdir()
        destination = self.cache / "abigail"
        destination.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(wiki.WikiDialogueError, "regular directory"):
            self.download()
        destination.unlink()
        destination.mkdir()
        source = target / "original.json"
        source.write_bytes(b"untouched")
        cached = destination / "dialogue.json"
        cached.symlink_to(source)
        with self.assertRaisesRegex(wiki.WikiDialogueError, "linked"):
            self.download()
        self.assertEqual(source.read_bytes(), b"untouched")
        cached.unlink()
        cached.mkdir()
        with self.assertRaisesRegex(wiki.WikiDialogueError, "regular file"):
            self.download()
        self.build_opener.assert_not_called()

    def test_character_cache_cannot_itself_be_a_repository(self):
        (self.cache / "abigail" / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(wiki.WikiDialogueError, "repositor"):
            self.download()
        self.build_opener.assert_not_called()

    def test_download_and_redirect_require_exact_pinned_https_url(self):
        url = self.templates["abigail"]["download_url"]
        invalid = (
            url.replace("https:", "http:"),
            url.replace(wiki._COMMIT, "main"),
            url.replace("Abigail.json", "Other.json"),
            url.replace("raw.githubusercontent.com", "raw.githubusercontent.com.evil.example"),
            url.replace("https://", "https://user:password@"),
            url.replace("raw.githubusercontent.com", "raw.githubusercontent.com:444"),
            url + "?raw=true", url + "#fragment", "file:///etc/passwd",
        )
        for destination in invalid:
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(wiki.WikiDialogueError, "verified"):
                    wiki._download_json(destination)
                with self.assertRaisesRegex(wiki.WikiDialogueError, "verified"):
                    wiki._DialogueRedirectHandler().redirect_request(Request(url), None, 302, "Found", {}, destination)
        self.build_opener.assert_not_called()

    def test_redirect_limit_and_final_response_url_are_checked(self):
        url = self.templates["abigail"]["download_url"]
        handler = wiki._DialogueRedirectHandler()
        for _ in range(3):
            handler.redirect_request(Request(url), None, 302, "Found", {}, url)
        with self.assertRaisesRegex(wiki.WikiDialogueError, "too many"):
            handler.redirect_request(Request(url), None, 302, "Found", {}, url)
        self.opener.open.side_effect = [Response(self.payloads[url], "https://evil.example/data.json")]
        with self.assertRaisesRegex(wiki.WikiDialogueError, "verified"):
            self.download()

    def test_declared_streamed_and_cache_payload_size_limits(self):
        url = self.templates["abigail"]["download_url"]
        for payload, headers in ((b"", {"Content-Length": "129"}), (b"x" * 129, {})):
            with self.subTest(headers=headers):
                self.opener.open.side_effect = [Response(payload, url, headers)]
                with patch.object(wiki, "MAX_DOWNLOAD_BYTES", 128):
                    with self.assertRaisesRegex(wiki.WikiDialogueError, "limit"):
                        self.download()
        for value in ("invalid", "-1"):
            self.opener.open.side_effect = [Response(b"", url, {"Content-Length": value})]
            with self.assertRaises(wiki.WikiDialogueError):
                self.download()
        with patch.object(wiki, "MAX_DOWNLOAD_BYTES", 128):
            with self.assertRaisesRegex(wiki.WikiDialogueError, "limit"):
                wiki._validate_payload(b"x" * 129, self.templates["abigail"])

    def test_deadline_stops_streaming(self):
        with patch.object(wiki.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(wiki.WikiDialogueError, "timed out"):
                self.download()

    def test_changed_source_is_rejected_and_not_cached(self):
        url = self.templates["abigail"]["download_url"]
        self.payloads[url] += b" "
        with self.assertRaisesRegex(wiki.WikiDialogueError, "verified version"):
            self.download()
        self.assertFalse(list(self.cache.rglob("*.json")))

    def test_malformed_json_collections_and_selected_text_are_rejected(self):
        good = json.loads(self.payloads[self.templates["abigail"]["download_url"]])
        payloads = [b"<html>error</html>", b"\xff", b"[]", b"{}", b'{"duplicate": 1, "duplicate": 2}']
        for value in (None, [], "", "   ", "x" * (wiki.MAX_TEXT_LENGTH + 1), "bad\x00text", "\ud800"):
            payloads.append(json.dumps({**good, "Introduction": value}).encode())
        for payload in payloads:
            with self.subTest(payload_size=len(payload)):
                self.use_payload(payload)
                with self.assertRaises(wiki.WikiDialogueError):
                    self.download()
                self.assertFalse(list(self.cache.rglob("*.json")))

    def test_notes_do_not_inject_missing_curated_keys(self):
        data = json.loads(self.payloads[self.templates["abigail"]["download_url"]])
        del data["Tue4"]
        self.use_payload(json.dumps(data).encode())
        result = self.download()
        self.assertEqual([(e["trigger"], e["text"]) for e in result["examples"]], list(data.items()))

    def test_all_entries_must_fit_the_editor_import_format(self):
        data = json.loads(self.payloads[self.templates["abigail"]["download_url"]])
        invalid_entries = (
            ("ExtraEntry", "x" * (wiki.MAX_TEXT_LENGTH + 1)),
            ("ExtraEntry", ""), ("ExtraEntry", "  \t"),
            ("ExtraEntry", "{{ContentPatcherToken}}"),
            ("ExtraEntry", "bad\x00text"), ("ExtraEntry", "\ud800"),
            ("Invalid Key", "Synthetic test text"),
            (" Mon", "Synthetic test text"),
            ("x" * 121, "Synthetic test text"),
        )
        for key, value in invalid_entries:
            with self.subTest(key=key, value_length=len(value)):
                self.use_payload(json.dumps({**data, key: value}).encode())
                with self.assertRaisesRegex(wiki.WikiDialogueError, "import format"):
                    self.download()
                self.assertFalse(list(self.cache.rglob("*.json")))

    def test_large_archive_preserves_all_entries_commands_text_and_order(self):
        from pixelheart_core.dialogue_templates import apply_dialogue_examples

        data = {f"event_{index}": f" Synthetic entry {index}.#$q 1/2 event_reply#Test choice?#$r 1 0 event_yes#Yes.$h  " for index in range(118)}
        data["-winter_Wed"] = "Unusual source key preserved exactly."
        data["summer_Tue6"] = "Synthetic weekday test."
        data["winter_24"] = "Synthetic calendar test."
        self.use_payload(json.dumps(data).encode())
        result = self.download()
        self.assertEqual(len(result["examples"]), 121)
        self.assertEqual([(e["trigger"], e["text"]) for e in result["examples"]], list(data.items()))
        imported = apply_dialogue_examples([], result["examples"])
        self.assertEqual([(e["trigger"], e["text"]) for e in imported], list(data.items()))
        notes = {e["trigger"]: e for e in result["examples"]}
        self.assertIn("Tuesday in summer with at least 6 hearts", notes["summer_Tue6"]["when"])
        self.assertIn("first year", notes["winter_24"]["when"])
        self.assertIn("specific context", notes["-winter_Wed"]["when"])

    def test_archive_cannot_exceed_the_projects_entry_capacity(self):
        self.use_payload(json.dumps({f"event_{i}": "Synthetic" for i in range(wiki.MAX_ENTRIES + 1)}).encode())
        with self.assertRaisesRegex(wiki.WikiDialogueError, "invalid dialogue collection"):
            self.download()
        self.assertFalse(list(self.cache.rglob("*.json")))

    def test_cache_write_failure_cleans_pending_file(self):
        with patch.object(wiki.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaisesRegex(wiki.WikiDialogueError, "writable"):
                self.download()
        self.assertEqual(list((self.cache / "abigail").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
