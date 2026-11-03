"""The designer's tools over MCP: a stdio server any agent can drive, one project file at a time."""
import contextlib
import io
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from meetcute_core.projects import load_project, new_project, save_project
from meetcute_designer.tools import tool_specs
from tests.designer_support import NULL_IDENTITY, NULL_WHEN


ROOT = Path(__file__).resolve().parents[1]
INTRODUCTION = {**NULL_WHEN, "occasion": "introduction"}
DEFAULT_INTRODUCTION = "Hello! It's lovely to meet you.$h"
REPLY_TIMEOUT = 60


class ServerProcess:
    """Drives the server over its pipes with plain JSON-RPC lines, the way any MCP client does."""

    def __init__(self, project):
        self.process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "meetcute_designer.mcp", "--project", str(project)],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8")
        self.replies = queue.Queue()
        self.stderr_lines = []
        self.threads = [threading.Thread(target=self._pump, args=(self.process.stdout, self.replies.put), daemon=True),
                        threading.Thread(target=self._pump, args=(self.process.stderr, self.stderr_lines.append), daemon=True)]
        for thread in self.threads:
            thread.start()
        self.next_id = 0

    @staticmethod
    def _pump(stream, sink):
        for line in stream:
            sink(line)
        sink(None)

    @property
    def stderr(self):
        return "".join(line for line in self.stderr_lines if line is not None)

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method, params=None):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            try:
                line = self.replies.get(timeout=REPLY_TIMEOUT)
            except queue.Empty:
                raise AssertionError(f"no reply to {method} within {REPLY_TIMEOUT} s; stderr:\n{self.stderr}") from None
            if line is None:
                raise AssertionError(f"the server closed stdout before answering {method}; stderr:\n{self.stderr}")
            message = json.loads(line)
            if message.get("id") != self.next_id:
                continue   # notifications and requests from the server are not what we are waiting for
            if "error" in message:
                raise AssertionError(f"{method} failed: {message['error']}")
            return message["result"]

    def start(self):
        result = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "meetcute-tests", "version": "1"}})
        self.notify("notifications/initialized")
        return result

    def call(self, name, arguments):
        """(is_error, text) for one tools/call."""
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        text = "".join(block.get("text", "") for block in result["content"] if block.get("type") == "text")
        return bool(result.get("isError", False)), text

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=REPLY_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait()
        for thread in self.threads:
            thread.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        return code


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="meetcute-mcp-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.project = save_project(new_project(), self.root / "Mira")   # a folder stands for its character.json
        self.server = ServerProcess(self.project)
        self.addCleanup(self.server.close)
        self.initialized = self.server.start()

    def test_introduces_itself_and_lists_every_designer_tool_with_its_schema(self):
        self.assertEqual(self.initialized["serverInfo"]["name"], "meetcute-designer")
        self.assertIn("tools", self.initialized["capabilities"])
        listed = {tool["name"]: tool for tool in self.server.request("tools/list")["tools"]}
        self.assertEqual(list(listed), [spec.name for spec in tool_specs()])
        for spec in tool_specs():
            self.assertEqual(listed[spec.name]["description"], spec.description)
            self.assertEqual(listed[spec.name]["inputSchema"], spec.input_schema)

    def test_set_identity_saves_the_project(self):
        is_error, text = self.server.call("set_identity", {**NULL_IDENTITY, "name": "Mira"})
        self.assertFalse(is_error, text)
        self.assertTrue(text.startswith("Done. Set name"), text)
        character = load_project(self.project)["character"]
        self.assertEqual((character["name"], character["internal_name"]), ("Mira", "NewCharacter"))

    def test_an_invalid_line_is_reported_and_never_written(self):
        line = {"when": INTRODUCTION, "text": "Quest time $q 1#yes"}
        is_error, text = self.server.call("write_dialogue", {"lines": [line], "mode": "replace"})
        self.assertTrue(is_error)
        self.assertIn("Use only $h $s $l $a $u feelings", text)
        self.assertEqual([row["text"] for row in load_project(self.project)["character"]["dialogues"]], [DEFAULT_INTRODUCTION])

    def test_written_dialogue_names_the_client_in_its_provenance(self):
        is_error, text = self.server.call("write_dialogue", {"lines": [{"when": INTRODUCTION, "text": "Oh! Hi.$h"}], "mode": "replace"})
        self.assertFalse(is_error, text)
        record = load_project(self.project)["character"]["dialogues"][0]
        self.assertEqual(record["text"], "Oh! Hi.$h")
        self.assertEqual(record["source"]["source_name"], "AI designer · mcp-meetcute-tests")

    def test_edits_made_by_hand_between_calls_are_seen(self):
        document = load_project(self.project)
        document["character"]["name"] = "Edited by hand"
        save_project(document, self.project)
        is_error, text = self.server.call("read_character", {})
        self.assertFalse(is_error, text)
        self.assertIn("Name: Edited by hand", text)

    def test_a_read_only_tool_leaves_the_file_alone(self):
        before = self.project.read_bytes()
        is_error, text = self.server.call("list_places", {})
        self.assertFalse(is_error, text)
        self.assertIn("Town · Pelican Town", text)
        self.assertEqual(self.project.read_bytes(), before)

    def test_banner_warns_to_close_the_character_in_meetcute(self):
        self.server.close()
        self.assertIn("Close this character in MEETC#TE", self.server.stderr)
        self.assertIn(str(self.project), self.server.stderr)


class CallTests(unittest.TestCase):
    """The call path in this process: what reaches the client when a tool or the file misbehaves."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="meetcute-mcp-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.project = save_project(new_project(), self.root / "Mira")

    def test_a_tool_bug_becomes_an_error_result_not_a_crash(self):
        from meetcute_designer import mcp as server
        with mock.patch.object(server, "run_tool", side_effect=RuntimeError("boom")):
            result = server.call_tool(self.project, "read_character", {}, "mcp:test")
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "The tool failed unexpectedly (RuntimeError). Nothing was changed.")

    def test_an_unknown_tool_is_an_error_result(self):
        from meetcute_designer import mcp as server
        result = server.call_tool(self.project, "paint_portrait", {}, "mcp:test")
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "There is no tool called 'paint_portrait'.")

    def test_a_project_that_vanished_is_an_error_result(self):
        from meetcute_designer import mcp as server
        self.project.unlink()
        result = server.call_tool(self.project, "read_character", {}, "mcp:test")
        self.assertTrue(result.is_error)
        self.assertIn("Could not open project", result.content[0].text)


class StartupTests(unittest.TestCase):
    """Argument handling runs in this process; nothing here reaches the server loop."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="meetcute-mcp-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_main(self, *argv):
        from meetcute_designer.mcp import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_a_missing_project_exits_with_a_clear_message(self):
        code, _, err = self.run_main("--project", str(self.root / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("No MEETC#TE project at", err)
        self.assertIn("nowhere", err)

    def test_a_file_that_is_not_a_project_is_refused(self):
        other = self.root / "notes.json"
        other.write_text('{"hello": 1}', encoding="utf-8")
        code, _, err = self.run_main("--project", str(other))
        self.assertEqual(code, 2)
        self.assertIn("does not contain a MEETC#TE character project", err)

    def test_help_prints_usage(self):
        code, out, _ = self.run_main("--help")
        self.assertEqual(code, 0)
        self.assertIn("--project", out)
        self.assertIn("character.json", out)


if __name__ == "__main__":
    unittest.main()
