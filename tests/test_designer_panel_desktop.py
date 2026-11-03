"""Headless coverage for the AI designer's setup dialog, dock panel and window wiring."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit

from pixelheart.app import MainWindow, SECTION_INDEX
from pixelheart.designer_config import PANEL_VISIBLE_KEY, save_designer_config
from pixelheart.game_import import game_import_settings
from pixelheart import designer_setup
from pixelheart.designer_setup import DesignerSetupDialog
from pixelheart_core.projects import new_project
from pixelheart.theme import apply_theme
from pixelheart_designer.config import DesignerConfig
from pixelheart_designer.providers import ProviderError, ToolCall
from tests.designer_support import NULL_IDENTITY, ScriptedProvider, text_reply, tool_reply
from tests.qt_support import QtTestCase


class SetupDialogTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_values_follow_fields_and_url_rules(self):
        dialog = DesignerSetupDialog(None, DesignerConfig.from_mapping({}), inline=True)
        dialog.provider_buttons["local"].setChecked(True)
        dialog.local_model.setText("qwen")
        dialog.local_url.setText("http://192.168.1.5:11434/v1/")
        self.assertEqual(dialog.values()["designer/provider"], "local")
        self.assertEqual(dialog.values()["designer/local_base_url"], "http://192.168.1.5:11434/v1")
        self.assertFalse(dialog.url_notice.isHidden())
        self.assertIn("isn't encrypted", dialog.url_notice.text())
        dialog.local_url.setText("ftp://x")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.anthropic_key.echoMode(), QLineEdit.EchoMode.Password)

    def test_local_copy_points_at_llama_cpp_and_mlx(self):
        dialog = DesignerSetupDialog(None, DesignerConfig.from_mapping({}), inline=True)
        self.assertEqual(dialog.local_url.text(), "http://localhost:8080/v1")
        texts = " ".join(widget.text() for widget in dialog.findChildren(QLabel))
        self.assertIn("llama-server", texts)
        self.assertIn("mlx_lm.server", texts)
        self.assertIn("--jinja", texts)
        self.assertNotIn("Ollama", texts)

    def test_closing_the_dialog_mid_check_lets_the_check_finish(self):
        dialog = DesignerSetupDialog(None, DesignerConfig.from_mapping({"designer/provider": "local", "designer/local_model": "qwen"}))

        def slow_check():
            time.sleep(0.4)
            return "ok"

        with patch("pixelheart.designer_setup.make_provider", return_value=SimpleNamespace(check=slow_check)):
            dialog.run_check()
            dialog.done(0)
            dialog.deleteLater()
            self.application.processEvents()
            self.assertTrue(designer_setup.pending_checks())
            designer_setup.wait_for_checks(5000)
            self.application.processEvents()
        self.assertFalse(designer_setup.pending_checks())

    def test_check_reports_provider_result(self):
        dialog = DesignerSetupDialog(None, DesignerConfig.from_mapping({"designer/provider": "local", "designer/local_model": "qwen"}), inline=True)
        with patch("pixelheart.designer_setup.make_provider", return_value=SimpleNamespace(check=lambda: "Connected · 2 models available")):
            dialog.run_check()
        self.assertEqual(dialog.check_result.text(), "Connected · 2 models available")
        with patch("pixelheart.designer_setup.make_provider", side_effect=ProviderError("not_running", "Nothing is answering")):
            dialog.run_check()
        self.assertEqual(dialog.check_result.text(), "Nothing is answering")


class PanelTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        save_designer_config({"designer/provider": "off"})
        self.window = MainWindow()
        self.panel = self.window.designer
        self.panel.inline = True

    def tearDown(self):
        if self.panel.turn is not None:
            self.panel.turn.wait(5000)
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()

    def test_setup_card_when_off(self):
        self.assertFalse(self.panel.setup_card.isHidden())
        self.assertIsNone(self.panel.session)
        self.assertFalse(self.panel.new_button.isEnabled())
        card_text = " ".join(widget.text() for widget in self.panel.setup_card.findChildren(QLabel))
        self.assertIn("llama.cpp", card_text)
        self.assertNotIn("Ollama", card_text)

    def test_turn_updates_document_one_undo_step_per_receipt(self):
        provider = ScriptedProvider([tool_reply(ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"}),
                                                ToolCall("2", "set_identity", {**NULL_IDENTITY, "tagline": "Bees"}), text="On it."),
                                     text_reply("Done.")])
        self.panel.set_provider(provider)
        before = self.window.project_history.history.undo_count
        self.panel.send("make her")
        self.assertEqual(self.window.document["character"]["name"], "Mira")
        self.assertEqual(self.window.identity.fields["name"].text(), "Mira")
        self.assertEqual(self.window.project_history.history.undo_count, before + 2)
        self.assertEqual([kind for kind, _ in self.panel.entries], ["notice", "user", "assistant", "receipt", "receipt", "assistant"])
        self.assertTrue(self.window.project_history.undo())
        self.assertEqual(self.window.document["character"]["tagline"], "")
        self.assertTrue(self.window.dirty)

    def test_receipt_link_opens_section(self):
        self.panel._anchor_clicked(QUrl("section:dialogue"))
        self.assertEqual(self.window.navigation.currentRow(), SECTION_INDEX["dialogue"])

    def test_provider_error_shows_message_and_reenables_send(self):
        self.panel.set_provider(ScriptedProvider([ProviderError("auth", "bad key")]))
        self.panel.send("hi")
        self.assertEqual(self.panel.entries[-1], ("error", "bad key"))
        self.assertTrue(self.panel.send_button.isEnabled())

    def test_stop_cancels_between_calls(self):
        holder = {}
        provider = ScriptedProvider([tool_reply(ToolCall("1", "read_character", {})), text_reply("never")],
                                    on_call=lambda p: holder["panel"].stop() if len(p.calls) == 1 else None)
        holder["panel"] = self.panel
        self.panel.set_provider(provider)
        self.panel.send("go")
        self.assertEqual(self.panel.entries[-1], ("notice", "Stopped"))

    def test_threaded_turn_round_trips_through_the_gui_thread(self):
        self.panel.inline = False
        provider = ScriptedProvider([tool_reply(ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"}), text="On it."),
                                     text_reply("Done.")])
        self.panel.set_provider(provider)
        self.panel.send("go")
        self.assertIsNotNone(self.panel.turn)
        self.assertFalse(self.panel.send_button.isEnabled())
        deadline = time.monotonic() + 15
        while self.panel.turn is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.panel.turn, "the worker turn did not finish")
        self.assertEqual(self.window.document["character"]["name"], "Mira")
        self.assertEqual(self.window.identity.fields["name"].text(), "Mira")
        self.assertEqual([kind for kind, _ in self.panel.entries], ["notice", "user", "assistant", "receipt", "assistant"])
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertEqual(self.panel.status.text(), "test-model")

    def test_chat_box_keeps_its_own_undo_instead_of_the_projects(self):
        self.assertTrue(self.panel.input.property("projectHistoryTransient"))
        self.assertFalse(self.window.project_history._routes_undo(self.panel.input))

    def test_closing_the_window_mid_turn_finishes_the_worker_first(self):
        self.panel.inline = False
        provider = ScriptedProvider([text_reply("Slow hello.")], on_call=lambda p: time.sleep(0.4))
        self.panel.set_provider(provider)
        self.panel.send("hi")
        self.assertIsNotNone(self.panel.turn)
        self.window.dirty = False
        self.window.close()                      # deferred: the worker is still running
        deadline = time.monotonic() + 15
        while self.panel.turn is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.panel.turn, "the window closed while the worker was still running")
        self.assertIn(("assistant", "Slow hello."), self.panel.entries)

    def _drain(self, seconds=15):
        deadline = time.monotonic() + seconds
        while self.panel.turn is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def test_stop_shows_stopping_until_the_worker_ends(self):
        self.panel.inline = False
        provider = ScriptedProvider([text_reply("Late.")], on_call=lambda p: time.sleep(0.4))
        self.panel.set_provider(provider)
        self.panel.send("go")
        self.panel.stop()
        self.assertEqual(self.panel.status.text(), "Stopping…")
        self._drain()
        self.assertEqual(self.panel.status.text(), "test-model")

    def test_opening_another_project_retires_the_running_turn(self):
        self.panel.inline = False
        provider = ScriptedProvider([tool_reply(ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"}), text="Late."), text_reply("never")],
                                    on_call=lambda p: time.sleep(0.4) if len(p.calls) == 1 else None)
        self.panel.set_provider(provider)
        self.panel.send("go")
        deadline = time.monotonic() + 5
        while not provider.calls and time.monotonic() < deadline:   # the provider call is now in flight
            self.application.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(provider.calls), 1)
        self.window.load_document(new_project())          # the user opened another character mid-turn
        self._drain()
        self.assertEqual(self.window.document["character"]["name"], "New character")
        self.assertEqual([kind for kind, _ in self.panel.entries], ["notice"])   # a fresh conversation, no stale receipt
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertEqual(len(provider.calls), 1)

    def test_welcome_hides_dock_and_workspace_restores_it(self):
        self.panel.show()
        self.window.show_home()
        self.assertTrue(self.panel.isHidden())
        self.window.show_workspace()
        self.assertFalse(self.panel.isHidden())

    def test_words_show_as_they_arrive_and_settle_once(self):
        seen = []
        provider = ScriptedProvider([text_reply("Tell me\nabout them.")], stream=True,
                                    on_fragment=lambda p, fragment: seen.append(self.panel.transcript.toPlainText()))
        self.panel.set_provider(provider)
        self.panel.send("A beekeeper")
        self.assertTrue(seen[2].endswith("Tell me\n"), seen[2])      # the third fragment found the first two on screen
        self.assertEqual([kind for kind, _ in self.panel.entries], ["notice", "user", "assistant"])
        shown = self.panel.transcript.toPlainText()
        self.assertEqual(shown.count("Tell me"), 1, shown)
        self.assertTrue(shown.endswith("Tell me\nabout them."), shown)
        self.assertNotIn("\n\n", shown)                                   # the live paragraph left no empty one behind

    def test_an_error_mid_stream_keeps_the_words_shown(self):
        def drop(provider, fragment):
            if fragment == "me":
                raise ProviderError("offline", "dropped")

        self.panel.set_provider(ScriptedProvider([text_reply("Tell me")], stream=True, on_fragment=drop))
        self.panel.send("go")
        self.assertEqual(self.panel.entries[-2:], [("assistant", "Tell "), ("error", "dropped")])
        self.assertEqual(self.panel.transcript.toPlainText().count("Tell "), 1)
        self.assertTrue(self.panel.send_button.isEnabled())

    def test_new_conversation_starts_afresh_with_the_same_provider(self):
        provider = ScriptedProvider([text_reply("Hello."), text_reply("Again.")])
        self.panel.set_provider(provider)
        self.panel.send("hi")
        first = self.panel.session
        self.assertTrue(self.panel.new_button.isEnabled())
        self.panel.new_button.click()
        self.assertEqual(self.panel.entries, [("notice", "Now chatting with test-model")])
        self.assertNotIn("Hello.", self.panel.transcript.toPlainText())
        self.assertIsNot(self.panel.session, first)
        self.assertIs(self.panel.session.provider, provider)
        self.panel.send("again")
        self.assertEqual(len(provider.calls[1][2]), 1)                    # the old conversation is not sent along
        self.assertEqual(provider.calls[1][2][0].content[1].text, "again")

    def test_new_conversation_mid_turn_stops_it(self):
        self.panel.inline = False
        provider = ScriptedProvider([tool_reply(ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"}), text="Late."), text_reply("never")],
                                    on_call=lambda p: time.sleep(0.4) if len(p.calls) == 1 else None)
        self.panel.set_provider(provider)
        self.panel.send("go")
        deadline = time.monotonic() + 5
        while not provider.calls and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.panel.new_button.click()
        self.assertFalse(self.panel.input.isEnabled())
        self._drain()
        self.assertEqual(self.window.document["character"]["name"], "New character")
        self.assertEqual([kind for kind, _ in self.panel.entries], ["notice"])
        self.assertTrue(self.panel.send_button.isEnabled())
        self.assertTrue(self.panel.input.isEnabled())
        self.assertEqual(len(provider.calls), 1)


class IntegrationTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        save_designer_config({"designer/provider": "off"})

    def tearDown(self):
        # Clicking "Design with AI" remembers the dock as open; later tests in this
        # process build windows at the minimum size and must not inherit that.
        settings = game_import_settings()
        settings.remove(PANEL_VISIBLE_KEY)
        settings.sync()

    def test_menu_overview_and_remembered_visibility(self):
        window = MainWindow()
        self.assertEqual(window.designer_action.text(), "&AI designer")
        self.assertTrue(window.designer_action.isCheckable())
        self.assertEqual(window.designer_setup_action.text(), "Set up AI designer…")
        self.assertEqual(window.overview.designer_button.text(), "Design with AI")
        window.overview.designer_button.click()
        self.assertFalse(window.designer.isHidden())
        window.close()
        window.deleteLater()
        QApplication.instance().processEvents()
        reopened = MainWindow()
        self.assertFalse(reopened.designer.isHidden())
        reopened.close()
        reopened.deleteLater()
        QApplication.instance().processEvents()


if __name__ == "__main__":
    unittest.main()
