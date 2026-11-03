"""Both providers' wire mapping, checked against fakes; nothing here touches the network."""
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from meetcute_designer.anthropic_provider import AnthropicProvider, anthropic_error, anthropic_reply, anthropic_request
from meetcute_designer.providers import (LocalServerProvider, ProviderError, StreamCancelled, Text, ToolCall, ToolResult,
                                           Turn, local_error, local_reply, local_request_body)
from meetcute_designer.tools import tool_specs


class FakeStream:
    """What urlopen hands back: iterable lines, ``read()`` for the rest, and a record of being closed."""

    def __init__(self, lines, content_type="text/event-stream"):
        self._lines = [line.encode() for line in lines]
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def __iter__(self):
        while self._lines:
            yield self._lines.pop(0)

    def read(self):
        data = b"".join(self._lines)
        self._lines = []
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False


class FakeResponse(FakeStream):
    def __init__(self, payload):
        super().__init__([json.dumps(payload)], "application/json")


def chunk(delta=None, finish_reason=None, **extra):
    choice = {"index": 0, "finish_reason": finish_reason, "delta": {} if delta is None else delta}
    return "data: " + json.dumps({"object": "chat.completion.chunk", "choices": [choice], **extra}) + "\n"


def http_error(code, body=b"{}"):
    return HTTPError("http://localhost:8080/v1/chat/completions", code, "err", {}, io.BytesIO(body))


def answer(content):
    """A plain (non-streamed) reply that says ``content`` and nothing else."""
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


class LocalProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = LocalServerProvider(base_url="http://localhost:8080/v1", model="qwen2.5:7b")
        self.turns = [Turn("user", [Text("ctx"), Text("hi")]),
                      Turn("assistant", [Text("ok"), ToolCall("c1", "read_character", {})]),
                      Turn("user", [ToolResult("c1", "summary", False)])]

    def test_request_body_shape(self):
        body = local_request_body("qwen2.5:7b", "SYS", tool_specs(), self.turns)
        self.assertEqual(body["model"], "qwen2.5:7b")
        self.assertIs(body["stream"], False)
        self.assertIs(local_request_body("qwen2.5:7b", "SYS", [], self.turns, stream=True)["stream"], True)
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "SYS"})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "ctx\n\nhi"})
        self.assertEqual(body["messages"][2]["tool_calls"][0],
                         {"id": "c1", "type": "function", "function": {"name": "read_character", "arguments": "{}"}})
        self.assertEqual(body["messages"][3], {"role": "tool", "tool_call_id": "c1", "content": "summary"})
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertIn("parameters", body["tools"][0]["function"])

    def test_reply_mapping_and_invalid_json(self):
        payload = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "Hmm", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "set_gifts", "arguments": "{\"love\": []}"}},
            {"id": "b", "type": "function", "function": {"name": "set_gifts", "arguments": "{oops"}}]}}],
            "usage": {"total_tokens": 9}}
        reply = local_reply(payload, "qwen2.5:7b")
        self.assertEqual(reply.stop, "tool_use")
        self.assertEqual(reply.content[0], Text("Hmm"))
        self.assertEqual(reply.content[1], ToolCall("a", "set_gifts", {"love": []}))
        self.assertEqual(reply.content[2].input, {"_invalid_json": "{oops"})
        self.assertEqual(local_reply({"choices": [{"finish_reason": "length", "message": {"content": "x"}}]}, "m").stop, "length")

    def test_complete_posts_and_replays_raw_assistant_message(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "Hi there"}}]}
        with patch("meetcute_designer.providers.urlopen", return_value=FakeResponse(payload)) as opened:
            reply = self.provider.complete(system="S", tools=[], turns=self.turns[:1])
        self.assertEqual(reply.content, [Text("Hi there")])
        self.assertEqual(reply.raw["provider"], "local")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:8080/v1/chat/completions")
        self.assertEqual(opened.call_args.kwargs["timeout"], 300.0)
        turns = self.turns[:1] + [Turn("assistant", reply.content, raw=reply.raw)]
        self.assertEqual(local_request_body("m", "S", [], turns)["messages"][-1], {"role": "assistant", "content": "Hi there"})

    def test_complete_streams_words_and_joins_tool_call_fragments(self):
        stream = FakeStream([": keepalive 1/2\n", "\n",
                             chunk({"role": "assistant", "content": ""}), "\n",
                             chunk({"content": "Hel"}), "\n",
                             chunk({"content": "lo", "reasoning": "hmm"}), "\n",
                             chunk({"tool_calls": [{"index": 0, "id": "a", "type": "function",
                                                    "function": {"name": "set_gifts", "arguments": "{\"lo"}}]}), "\n",
                             chunk({"tool_calls": [{"index": 0, "function": {"arguments": "ve\": []}"}}]}), "\n",
                             chunk({}, finish_reason="tool_calls", usage={"total_tokens": 9}), "\n",
                             "data: [DONE]\n", "\n"])
        heard = []
        with patch("meetcute_designer.providers.urlopen", return_value=stream) as opened:
            reply = self.provider.complete(system="S", tools=tool_specs(), turns=self.turns[:1], on_text=heard.append)
        body = json.loads(opened.call_args.args[0].data)
        self.assertIs(body["stream"], True)
        self.assertEqual([piece for piece in heard if piece], ["Hel", "lo"])
        self.assertEqual(reply.content, [Text("Hello"), ToolCall("a", "set_gifts", {"love": []})])
        self.assertEqual(reply.stop, "tool_use")
        self.assertEqual(reply.usage, {"total_tokens": 9})
        self.assertTrue(stream.closed)
        self.assertEqual(reply.raw["message"]["tool_calls"][0]["function"]["arguments"], '{"love": []}')

    def test_whole_tool_calls_in_one_chunk_and_a_plain_json_answer_are_understood(self):
        stream = FakeStream([chunk({"tool_calls": [{"id": "a", "type": "function", "function": {"name": "read_character", "arguments": "{}"}}]}),
                             chunk({"tool_calls": [{"id": "b", "type": "function", "function": {"name": "list_places", "arguments": {}}}]}),
                             chunk({}, finish_reason="tool_calls"), "data: [DONE]\n"])
        with patch("meetcute_designer.providers.urlopen", return_value=stream):
            reply = self.provider.complete(system="S", tools=tool_specs(), turns=self.turns[:1], on_text=lambda piece: None)
        self.assertEqual(reply.content, [ToolCall("a", "read_character", {}), ToolCall("b", "list_places", {})])
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "Hi there"}}]}
        with patch("meetcute_designer.providers.urlopen", return_value=FakeStream([json.dumps(payload)], "application/json")):
            reply = self.provider.complete(system="S", tools=[], turns=self.turns[:1], on_text=lambda piece: None)
        self.assertEqual((reply.content, reply.stop), ([Text("Hi there")], "done"))

    def test_reasoning_leaked_before_a_bare_closing_tag_is_dropped(self):
        # mlx_lm.server's reasoning parser misses the block when the model skips the opening tag.
        reply = local_reply(answer("The user wants a greeting.\n</think>\n\nHi there"), "m")
        self.assertEqual(reply.content, [Text("Hi there")])
        self.assertEqual(reply.raw["message"]["content"], "Hi there")   # the replayed message loses the reasoning too
        self.assertEqual(local_reply(answer("Wait.\n</think>\nNo, a greeting.\n</think>\nHi there"), "m").content, [Text("Hi there")])

    def test_a_streamed_leak_is_dropped_once_the_words_have_settled(self):
        stream = FakeStream([chunk({"role": "assistant", "content": ""}), chunk({"content": "The user wants"}),
                             chunk({"content": " a greeting.\n</thi"}), chunk({"content": "nk>\n\nHi"}), chunk({"content": " there"}),
                             chunk({}, finish_reason="stop"), "data: [DONE]\n"])
        with patch("meetcute_designer.providers.urlopen", return_value=stream):
            reply = self.provider.complete(system="S", tools=[], turns=self.turns[:1], on_text=lambda piece: None)
        self.assertEqual((reply.content, reply.stop), ([Text("Hi there")], "done"))
        turns = self.turns[:1] + [Turn("assistant", reply.content, raw=reply.raw)]
        self.assertEqual(local_request_body("m", "S", [], turns)["messages"][-1], {"role": "assistant", "content": "Hi there"})

    def test_a_think_pair_is_dropped_with_its_contents(self):
        reply = local_reply(answer("<think>\nThe user wants a greeting.\n</think>\n\nHi there"), "m")
        self.assertEqual(reply.content, [Text("Hi there")])
        self.assertEqual(reply.raw["message"]["content"], "Hi there")

    def test_words_without_a_think_tag_are_left_alone(self):
        reply = local_reply(answer("\nHi there.\n\nI think the gifts are set."), "m")
        self.assertEqual(reply.content, [Text("\nHi there.\n\nI think the gifts are set.")])
        self.assertEqual(reply.raw["message"]["content"], "\nHi there.\n\nI think the gifts are set.")

    def test_a_server_that_refuses_streaming_is_asked_plainly_from_then_on(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "Plain"}}]}
        refused = http_error(400, b'{"error": {"message": "Cannot use tools with stream"}}')
        with patch("meetcute_designer.providers.urlopen", side_effect=[refused, FakeResponse(payload), FakeResponse(payload)]) as opened:
            first = self.provider.complete(system="S", tools=tool_specs(), turns=self.turns[:1], on_text=lambda piece: None)
            second = self.provider.complete(system="S", tools=tool_specs(), turns=self.turns[:1], on_text=lambda piece: None)
        refused.close()
        self.assertEqual((first.content, second.content), ([Text("Plain")], [Text("Plain")]))
        self.assertEqual([json.loads(call.args[0].data)["stream"] for call in opened.call_args_list], [True, False, False])

    def test_stop_mid_stream_closes_the_connection(self):
        stream = FakeStream([chunk({"content": "Hel"}), chunk({"content": "lo"}), "data: [DONE]\n"])

        def stop(piece):
            raise StreamCancelled()

        with patch("meetcute_designer.providers.urlopen", return_value=stream):
            with self.assertRaises(StreamCancelled):
                self.provider.complete(system="S", tools=[], turns=self.turns[:1], on_text=stop)
        self.assertTrue(stream.closed)

    def test_error_mapping(self):
        cases = [(URLError("refused"), "not_running",
                  "Nothing is answering at http://localhost:8080/v1. Start your model server, then try again."),
                 (http_error(401), "auth", "MEETC#TE couldn't sign in to the model server. Check the key in Set up AI designer."),
                 (http_error(404, b'{"error": {"message": "model not found"}}'), "model",
                  "qwen2.5:7b isn't available. Check the model name in Set up AI designer."),
                 (http_error(400, b'{"error": {"message": "does not support tools"}}'), "no_tools",
                  "qwen2.5:7b can't use tools. Choose a model that supports tool calling."),
                 (http_error(500, b'{"error": {"message": "boom"}}'), "server", "The provider returned an error: boom"),
                 (TimeoutError(), "server", "The model server didn't answer within 5 minutes.")]
        for exc, kind, message in cases:
            error = local_error(exc, "http://localhost:8080/v1", "qwen2.5:7b")
            self.assertEqual((error.kind, error.message), (kind, message))
            if hasattr(exc, "close"):
                exc.close()

    def test_wrong_address_is_not_blamed_on_the_model(self):
        exc = http_error(404, b"404 page not found")
        error = local_error(exc, "http://localhost:8080", "qwen2.5:7b")
        exc.close()
        self.assertEqual((error.kind, error.message),
                         ("not_running", "Nothing answered at http://localhost:8080 for that request. Check the address; it usually ends in /v1."))

    def test_check_lists_models(self):
        with patch("meetcute_designer.providers.urlopen", return_value=FakeResponse({"data": [{"id": "qwen2.5:7b"}, {"id": "llama3"}]})) as opened:
            self.assertEqual(self.provider.check(), "Connected · 2 models available")
        self.assertEqual(opened.call_args.kwargs["timeout"], 10.0)   # a check must not hang for five minutes
        with patch("meetcute_designer.providers.urlopen", return_value=FakeResponse({"data": [{"id": "/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf"}]})):
            self.assertEqual(self.provider.check(),
                             "Connected · serving /models/Qwen2.5-7B-Instruct-Q4_K_M.gguf · the model field doesn't match it, "
                             "which llama-server ignores but mlx_lm.server needs.")


class FakeMessageStream:
    """The SDK's stream manager: the request goes out on entering, events iterate, the message comes last."""

    def __init__(self, events, response, error):
        self._events, self._response, self._error = events, response, error
        self.closed = False

    def __enter__(self):
        if self._error:
            raise self._error
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._response


class FakeAnthropic:
    def __init__(self, response=None, error=None, events=(), responses=None):
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.models = SimpleNamespace(retrieve=lambda model: SimpleNamespace(id=model))
        self._responses = list(responses) if responses is not None else [response]
        self._error, self._events = error, list(events)
        self.streams = []

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        stream = FakeMessageStream(self._events, response, self._error)
        self.streams.append(stream)
        return stream

    def with_options(self, **options):
        self.options = options
        return self


def block(**fields):
    return SimpleNamespace(**fields)


class AnthropicProviderTests(unittest.TestCase):
    def test_request_shape(self):
        turns = [Turn("user", [Text("ctx"), Text("hi")]),
                 Turn("assistant", [Text("ok")], raw={"provider": "anthropic", "content": ["RAW"]}),
                 Turn("user", [ToolResult("c1", "r", True)])]
        kwargs = anthropic_request("claude-opus-5-5", "SYS", tool_specs(), turns)
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["max_tokens"], 16000)
        self.assertEqual(kwargs["system"], [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(kwargs["output_config"], {"effort": "medium"})
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertNotIn("thinking", kwargs)
        self.assertIs(kwargs["tools"][0]["strict"], True)
        self.assertIn("input_schema", kwargs["tools"][0])
        self.assertEqual(kwargs["messages"][0], {"role": "user", "content": [{"type": "text", "text": "ctx"}, {"type": "text", "text": "hi"}]})
        self.assertEqual(kwargs["messages"][1], {"role": "assistant", "content": ["RAW"]})
        self.assertEqual(kwargs["messages"][2], {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "r", "is_error": True}]})

    def test_foreign_raw_is_rebuilt(self):
        turns = [Turn("assistant", [Text("ok"), ToolCall("c1", "read_character", {})], raw={"provider": "local", "message": {}})]
        self.assertEqual(anthropic_request("m", "S", [], turns)["messages"][0]["content"],
                         [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "c1", "name": "read_character", "input": {}}])

    def test_reply_mapping(self):
        response = SimpleNamespace(content=[block(type="thinking", thinking=""), block(type="text", text="Hi"),
                                            block(type="tool_use", id="t1", name="set_gifts", input={"love": []})],
                                   stop_reason="tool_use", stop_details=None, usage=SimpleNamespace(input_tokens=5, output_tokens=7))
        reply = anthropic_reply(response, "claude-opus-5-5")
        self.assertEqual(reply.content, [Text("Hi"), ToolCall("t1", "set_gifts", {"love": []})])
        self.assertEqual(reply.stop, "tool_use")
        self.assertEqual(reply.raw, {"provider": "anthropic", "content": response.content})
        self.assertEqual(reply.usage, {"input_tokens": 5, "output_tokens": 7})
        refusal = SimpleNamespace(content=[], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber", explanation="Nope"), usage=None)
        self.assertEqual((anthropic_reply(refusal, "m").stop, anthropic_reply(refusal, "m").detail), ("refusal", "Nope"))
        cut = SimpleNamespace(content=[block(type="text", text="x")], stop_reason="max_tokens", stop_details=None, usage=None)
        self.assertEqual(anthropic_reply(cut, "m").stop, "length")

    def test_complete_streams_text_events_and_check_uses_the_client(self):
        final = SimpleNamespace(content=[block(type="text", text="Hi there")], stop_reason="end_turn", stop_details=None, usage=None)
        client = FakeAnthropic(response=final, events=[block(type="text", text="Hi ", snapshot="Hi "),
                                                       block(type="input_json", partial_json="{"),
                                                       block(type="text", text="there", snapshot="Hi there"),
                                                       block(type="message_stop")])
        provider = AnthropicProvider(model="claude-opus-5-5", client=client)
        heard = []
        reply = provider.complete(system="S", tools=[], turns=[Turn("user", [Text("hi")])], on_text=heard.append)
        self.assertEqual(reply.content, [Text("Hi there")])
        self.assertEqual([piece for piece in heard if piece], ["Hi ", "there"])
        self.assertEqual(client.kwargs["model"], "claude-opus-5-5")
        self.assertNotIn("stream", client.kwargs)
        self.assertTrue(client.streams[0].closed)
        self.assertEqual(provider.complete(system="S", tools=[], turns=[Turn("user", [Text("hi")])]).content, [Text("Hi there")])
        self.assertEqual(provider.check(), "Connected to Anthropic · claude-opus-5-5")
        self.assertEqual(client.options, {"timeout": 10.0})

    def test_pause_turn_is_resumed_once(self):
        paused = SimpleNamespace(content=[block(type="text", text="One sec.")], stop_reason="pause_turn", stop_details=None, usage=None)
        final = SimpleNamespace(content=[block(type="text", text="Done.")], stop_reason="end_turn", stop_details=None, usage=None)
        client = FakeAnthropic(responses=[paused, final])
        reply = AnthropicProvider(model="m", client=client).complete(system="S", tools=[], turns=[Turn("user", [Text("hi")])])
        self.assertEqual(reply.content, [Text("Done.")])
        self.assertEqual(client.kwargs["messages"][-1], {"role": "assistant", "content": paused.content})
        self.assertEqual(len(client.streams), 2)

    def test_stop_mid_stream_passes_through_untouched(self):
        client = FakeAnthropic(events=[block(type="text", text="Hi ")])

        def stop(piece):
            raise StreamCancelled()

        with self.assertRaises(StreamCancelled):
            AnthropicProvider(model="m", client=client).complete(system="S", tools=[], turns=[], on_text=stop)
        self.assertTrue(client.streams[0].closed)

    def test_stream_errors_map_like_before(self):
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        error = anthropic.RateLimitError("msg", response=httpx2.Response(429, request=request), body=None)
        with self.assertRaises(ProviderError) as caught:
            AnthropicProvider(model="m", client=FakeAnthropic(error=error)).complete(system="S", tools=[], turns=[])
        self.assertEqual(caught.exception.kind, "rate_limit")

    def test_error_chain(self):
        import anthropic
        import httpx2
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

        def status(cls, code):
            return cls("msg", response=httpx2.Response(code, request=request), body={"error": {"message": "detail"}})

        cases = [(status(anthropic.AuthenticationError, 401), "auth", "MEETC#TE couldn't sign in to Anthropic. Check the API key in Set up AI designer."),
                 (status(anthropic.NotFoundError, 404), "model", "claude-opus-5-5 isn't available. Check the model name in Set up AI designer."),
                 (status(anthropic.RateLimitError, 429), "rate_limit", "Anthropic asked us to slow down. Try again in a minute."),
                 (status(anthropic.InternalServerError, 500), "server", "The provider returned an error: msg"),
                 (anthropic.APIConnectionError(request=request), "offline", "Couldn't reach the provider. Check your connection.")]
        for exc, kind, message in cases:
            error = anthropic_error(exc, "claude-opus-5-5")
            self.assertEqual((error.kind, error.message), (kind, message))

    def test_unresolved_credentials_map_to_auth(self):
        exc = TypeError("Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set.")
        error = anthropic_error(exc, "claude-opus-5-5")
        self.assertEqual((error.kind, error.message), ("auth", "MEETC#TE couldn't sign in to Anthropic. Check the API key in Set up AI designer."))

    def test_missing_sdk_is_not_configured_at_first_use_not_at_startup(self):
        with patch.dict(sys.modules, {"anthropic": None}):
            provider = AnthropicProvider(model="m")        # the SDK import waits until it is needed
            with self.assertRaises(ProviderError) as caught:
                provider.check()
        self.assertEqual(caught.exception.kind, "not_configured")


if __name__ == "__main__":
    unittest.main()
