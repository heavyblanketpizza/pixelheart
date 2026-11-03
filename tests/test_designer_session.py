"""The designer's turn loop: context, tool calls, receipts, limits and cancel."""
from copy import deepcopy
import unittest

from pixelheart_core.projects import new_project
from pixelheart_designer.prompts import SYSTEM_PROMPT, context_block
from pixelheart_designer.providers import ProviderError, Reply, Text, ToolCall
from pixelheart_designer.session import DesignerSession
from pixelheart_designer.tools import tool_specs
from tests.designer_support import NULL_IDENTITY, ScriptedProvider, text_reply, tool_reply


class MemoryPort:
    def __init__(self, document):
        self.document = document
        self.applied = []

    def snapshot(self):
        return deepcopy(self.document)

    def apply(self, document, receipt):
        self.document = document
        self.applied.append(receipt)


def events_of(session, text):
    events = []
    session.run_turn(text, lambda kind, payload: events.append((kind, payload)))
    return events


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryPort(new_project())

    def test_text_reply_finishes_with_context_block_first(self):
        provider = ScriptedProvider([text_reply("Tell me about them.")])
        session = DesignerSession(provider, self.port)
        self.assertEqual(events_of(session, "A beekeeper"), [("assistant", "Tell me about them."), ("done", None)])
        system, tools, turns = provider.calls[0]
        self.assertEqual(system, SYSTEM_PROMPT)
        self.assertEqual([spec.name for spec in tools], [spec.name for spec in tool_specs()])
        self.assertEqual(turns[0].content[0].text, context_block(self.port.document))
        self.assertEqual(turns[0].content[1].text, "A beekeeper")

    def test_prompt_teaches_only_portrait_codes_the_app_knows(self):
        for code in ("$h", "$s", "$l", "$a", "$u"):
            self.assertIn(code, SYSTEM_PROMPT)
        self.assertNotIn("$n", SYSTEM_PROMPT)

    def test_tool_call_applies_receipt_and_batches_results(self):
        call_a = ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"})
        call_b = ToolCall("2", "set_identity", {**NULL_IDENTITY, "home_map": "9 bad"})
        provider = ScriptedProvider([tool_reply(call_a, call_b, text="Working."), text_reply("Done.")])
        session = DesignerSession(provider, self.port)
        events = events_of(session, "go")
        self.assertEqual(events[0], ("assistant", "Working."))
        self.assertEqual(events[1][0], "receipt")
        self.assertEqual(events[1][1]["title"], "Updated About them")
        self.assertEqual(events[-2:], [("assistant", "Done."), ("done", None)])
        self.assertEqual(self.port.document["character"]["name"], "Mira")
        results = provider.calls[1][2][-1].content
        self.assertEqual([(r.id, r.is_error) for r in results], [("1", False), ("2", True)])
        self.assertIn("Start the map ID with a letter", results[1].content)
        self.assertEqual(provider.calls[1][2][-2].raw, None)   # assistant turn keeps whatever raw the reply carried

    def test_hand_edits_show_in_next_context_block(self):
        provider = ScriptedProvider([text_reply("ok"), text_reply("ok")])
        session = DesignerSession(provider, self.port)
        events_of(session, "one")
        self.port.document["character"]["name"] = "Edited"
        events_of(session, "two")
        self.assertIn("Name: Edited", provider.calls[1][2][-1].content[0].text)

    def test_repeated_failures_stop_with_notice(self):
        bad = ToolCall("x", "set_identity", {**NULL_IDENTITY, "home_map": "9 bad"})
        provider = ScriptedProvider([tool_reply(bad), tool_reply(bad), tool_reply(bad), text_reply("never")])
        events = events_of(DesignerSession(provider, self.port), "go")
        self.assertEqual(events[-2][0], "notice")
        self.assertTrue(events[-2][1].startswith("I couldn't make that change. "))
        self.assertEqual(len(provider.calls), 3)

    def test_tool_budget_notice(self):
        call = ToolCall("x", "read_character", {})
        provider = ScriptedProvider([tool_reply(*[call] * 6), tool_reply(*[call] * 6), tool_reply(call), text_reply("never")])
        events = events_of(DesignerSession(provider, self.port), "go")
        self.assertIn(("notice", "That's a lot of changes for one turn. Tell me what to do next."), events)
        self.assertEqual(len(provider.calls), 2)

    def test_length_refusal_and_invalid_json(self):
        self.assertIn(("notice", "The reply was cut short. Ask me to continue."),
                      events_of(DesignerSession(ScriptedProvider([Reply([Text("partial")], "length")]), self.port), "go"))
        self.assertIn(("notice", "Declined: no."),
                      events_of(DesignerSession(ScriptedProvider([Reply([], "refusal", detail="Declined: no.")]), self.port), "go"))
        provider = ScriptedProvider([tool_reply(ToolCall("j", "set_gifts", {"_invalid_json": "{oops"})), text_reply("ok")])
        events_of(DesignerSession(provider, self.port), "go")
        result = provider.calls[1][2][-1].content[0]
        self.assertTrue(result.is_error)
        self.assertEqual(result.content, "The tool input wasn't valid JSON. Send it again.")

    def test_stop_drops_a_pending_replys_tool_calls(self):
        session = None
        call = ToolCall("x", "set_identity", {**NULL_IDENTITY, "name": "Mira"})
        provider = ScriptedProvider([tool_reply(call, text="Working."), text_reply("never")],
                                    on_call=lambda p: session.cancel() if len(p.calls) == 1 else None)
        session = DesignerSession(provider, self.port)
        events = events_of(session, "go")
        self.assertEqual(events, [("assistant", "Working."), ("notice", "Stopped"), ("done", None)])
        self.assertEqual(self.port.document["character"]["name"], "New character")
        self.assertEqual(session.turns[-1].content, [Text("Working.")])   # no dangling tool_use

    def test_apply_failure_becomes_an_error_event(self):
        class BrokenPort(MemoryPort):
            def apply(self, document, receipt):
                raise RuntimeError("boom")

        provider = ScriptedProvider([tool_reply(ToolCall("1", "set_identity", {**NULL_IDENTITY, "name": "Mira"})), text_reply("never")])
        session = DesignerSession(provider, BrokenPort(new_project()))
        events = events_of(session, "go")
        self.assertEqual(events[-2:], [("error", "Something went wrong in the designer (RuntimeError). Undo if anything looks off."), ("done", None)])
        self.assertEqual(session.turns, [])

    def test_cancel_between_calls_and_provider_error(self):
        session = None
        provider = ScriptedProvider([tool_reply(ToolCall("x", "read_character", {})), text_reply("never")],
                                    on_call=lambda p: session.cancel() if len(p.calls) == 1 else None)
        session = DesignerSession(provider, self.port)
        events = events_of(session, "go")
        self.assertEqual(events[-2:], [("notice", "Stopped"), ("done", None)])
        self.assertEqual(len(provider.calls), 1)
        failing = DesignerSession(ScriptedProvider([ProviderError("auth", "bad key")]), self.port)
        self.assertEqual(events_of(failing, "go"), [("error", "bad key"), ("done", None)])
        self.assertEqual(failing.turns, [])

    def test_words_arrive_before_the_full_reply(self):
        provider = ScriptedProvider([text_reply("Tell me about them.")], stream=True)
        events = events_of(DesignerSession(provider, self.port), "A beekeeper")
        self.assertEqual(events, [("assistant_delta", "Tell "), ("assistant_delta", "me "), ("assistant_delta", "about "),
                                  ("assistant_delta", "them."), ("assistant", "Tell me about them."), ("done", None)])

    def test_stop_mid_stream_keeps_the_words_so_far(self):
        session = None
        provider = ScriptedProvider([tool_reply(ToolCall("x", "read_character", {}), text="Tell me about them."), text_reply("never")],
                                    stream=True, on_fragment=lambda p, fragment: session.cancel() if fragment == "about " else None)
        session = DesignerSession(provider, self.port)
        events = events_of(session, "go")
        self.assertEqual(events, [("assistant_delta", "Tell "), ("assistant_delta", "me "), ("assistant", "Tell me "),
                                  ("notice", "Stopped"), ("done", None)])
        self.assertEqual((session.turns[-1].role, session.turns[-1].content), ("assistant", [Text("Tell me ")]))
        self.assertEqual(len(provider.calls), 1)

    def test_provider_error_mid_stream_clears_the_turn(self):
        def drop(provider, fragment):
            if fragment == "me":
                raise ProviderError("offline", "dropped")

        provider = ScriptedProvider([text_reply("Tell me")], stream=True, on_fragment=drop)
        session = DesignerSession(provider, self.port)
        self.assertEqual(events_of(session, "go"), [("assistant_delta", "Tell "), ("error", "dropped"), ("done", None)])
        self.assertEqual(session.turns, [])


if __name__ == "__main__":
    unittest.main()
