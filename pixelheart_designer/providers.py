"""Provider-neutral message model and the contract every model provider meets."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


STOPS = ("done", "tool_use", "length", "refusal")
KINDS = ("not_configured", "auth", "rate_limit", "offline", "not_running", "no_tools", "model", "server")


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResult:
    id: str
    content: str
    is_error: bool = False


@dataclass
class Turn:
    """One side of the conversation. ``raw`` holds the producing provider's own blocks."""

    role: str
    content: list
    raw: dict | None = None


@dataclass(frozen=True)
class Reply:
    content: list
    stop: str
    usage: dict = field(default_factory=dict)
    detail: str = ""
    raw: dict | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


class ProviderError(Exception):
    """A provider could not answer; ``message`` is already worded for the user."""

    def __init__(self, kind, message):
        if kind not in KINDS:
            raise ValueError(f"Unknown provider error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind
        self.message = message


class StreamCancelled(Exception):
    """Raised inside ``on_text`` to abandon a reply midway; providers let it through and close the connection."""


class Provider(Protocol):
    label: str

    def complete(self, *, system: str, tools: list[ToolSpec], turns: list[Turn],
                 on_text: Callable[[str], None] | None = None) -> Reply:
        """Answer the conversation; ``on_text`` hears the assistant's words as they arrive.

        A provider calls ``on_text`` with each fragment of text, and may call it with
        an empty string between other events, so the caller can raise
        ``StreamCancelled`` to stop early. The whole ``Reply`` still comes back at the end.
        """

    def check(self) -> str: ...


# --- Local server (the OpenAI-compatible chat endpoint of llama.cpp's llama-server, MLX's mlx_lm.server and kin) ---

import itertools  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from urllib.error import HTTPError, URLError  # noqa: E402
from urllib.request import Request, urlopen  # noqa: E402


LOCAL_TIMEOUT = 300.0
CHECK_TIMEOUT = 10.0


class StreamRefused(ProviderError):
    """The server answered 400 to a streaming request; the caller may ask again without it."""


def _join_text(blocks):
    return "\n\n".join(block.text for block in blocks if isinstance(block, Text))


_THINK_PAIR = re.compile(r"<think>.*?</think>", re.DOTALL)


def _drop_leaked_reasoning(text: str) -> str:
    """Keep only the answer after reasoning a model emitted as plain content.

    A ``<think>…</think>`` pair goes with its contents. A bare ``</think>`` ends everything
    before it: mlx_lm.server's reasoning parser misses the block when the model skips the
    opening tag, so the thinking arrives as ordinary words.
    """
    text = _THINK_PAIR.sub("", text)
    return text.rsplit("</think>", 1)[-1].lstrip()


def local_request_body(model: str, system: str, tools: list[ToolSpec], turns: list[Turn], *, stream: bool = False) -> dict:
    messages = [{"role": "system", "content": system}]
    for turn in turns:
        if turn.role == "assistant":
            raw = turn.raw if isinstance(turn.raw, dict) else {}
            if raw.get("provider") == "local" and isinstance(raw.get("message"), dict):
                messages.append(dict(raw["message"]))
                continue
            message = {"role": "assistant", "content": _join_text(turn.content)}
            calls = [block for block in turn.content if isinstance(block, ToolCall)]
            if calls:
                message["tool_calls"] = [{"id": call.id, "type": "function",
                                          "function": {"name": call.name, "arguments": json.dumps(call.input, ensure_ascii=False)}}
                                         for call in calls]
            messages.append(message)
        else:
            for block in turn.content:
                if isinstance(block, ToolResult):
                    messages.append({"role": "tool", "tool_call_id": block.id, "content": block.content})
            text = _join_text(turn.content)
            if text:
                messages.append({"role": "user", "content": text})
    body = {"model": model, "messages": messages, "stream": stream}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": tool.name, "description": tool.description,
                                                           "parameters": tool.input_schema}} for tool in tools]
        body["tool_choice"] = "auto"
    return body


def local_reply(payload: dict, model: str) -> Reply:
    choice = payload["choices"][0]
    message = choice.get("message") or {}
    content = []
    text = message.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if isinstance(text, str) and "</think>" in text:
        text = _drop_leaked_reasoning(text)
        message = {**message, "content": text}   # the replayed message loses the reasoning too
    if isinstance(text, str) and text.strip():
        content.append(Text(text))
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        arguments = function.get("arguments", "")
        if isinstance(arguments, dict):
            parsed = arguments
        else:
            try:
                parsed = json.loads(arguments) if str(arguments).strip() else {}
            except (TypeError, ValueError):
                parsed = {"_invalid_json": str(arguments)}
            if not isinstance(parsed, dict):
                parsed = {"_invalid_json": str(arguments)}
        content.append(ToolCall(str(call.get("id") or f"call_{index}"), str(function.get("name", "")), parsed))
    if any(isinstance(block, ToolCall) for block in content):
        stop = "tool_use"
    elif choice.get("finish_reason") == "length":
        stop = "length"
    else:
        stop = "done"
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return Reply(content, stop, usage, raw={"provider": "local", "message": {"role": "assistant", **message}})


def _merge_call(calls: dict, call: dict) -> None:
    """Fold one streamed tool-call fragment into its slot; whole calls and pieces both arrive this way."""
    index = call.get("index")
    if not isinstance(index, int):
        index = max(calls, default=-1) + 1
    slot = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    if call.get("id"):
        slot["id"] = str(call["id"])
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    if function.get("name"):
        slot["function"]["name"] = str(function["name"])
    arguments = function.get("arguments")
    if isinstance(arguments, dict):
        slot["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)
    elif isinstance(arguments, str) and arguments:
        slot["function"]["arguments"] += arguments


def _join_stream(lines, on_text) -> dict:
    """Fold a server-sent event stream back into the one-message payload ``local_reply`` reads."""
    text = []
    calls: dict[int, dict] = {}
    finish_reason = None
    usage = None
    for raw in lines:
        line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)).strip()
        if not line.startswith("data:"):
            # Blank lines, ": keepalive" comments and event names carry nothing, but Stop may land on them.
            if on_text is not None:
                on_text("")
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        choices = event.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else choice.get("message")
        delta = delta if isinstance(delta, dict) else {}
        piece = delta.get("content")
        if isinstance(piece, list):
            piece = "".join(part.get("text", "") for part in piece if isinstance(part, dict))
        if not isinstance(piece, str):
            piece = ""
        if piece:
            text.append(piece)
        for call in delta.get("tool_calls") or []:
            if isinstance(call, dict):
                _merge_call(calls, call)
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        if on_text is not None:
            on_text(piece)
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    payload = {"choices": [{"finish_reason": finish_reason, "message": message}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def _read_payload(response, on_text) -> dict:
    """A whole JSON answer, or an event stream folded into one."""
    first = b""
    for raw in response:
        if raw.strip():
            first = raw
            break
    if first.lstrip().startswith((b"{", b"[")):
        return json.loads((first + response.read()).decode("utf-8"))
    return _join_stream(itertools.chain([first], response), on_text)


def _http_error_message(error):
    try:
        body = json.loads(error.read().decode("utf-8", "replace"))
    except (ValueError, AttributeError, OSError):
        return ""
    detail = body.get("error", body.get("message", "")) if isinstance(body, dict) else ""
    if isinstance(detail, dict):
        detail = detail.get("message", "")
    return str(detail or "")


def local_error(exc: Exception, base_url: str, model: str) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, HTTPError):
        detail = _http_error_message(exc)
        if exc.code in (401, 403):
            return ProviderError("auth", "Pixelheart couldn't sign in to the model server. Check the key in Set up AI designer.")
        if exc.code == 404:
            if "model" in detail.lower():
                return ProviderError("model", f"{model} isn't available. Check the model name in Set up AI designer.")
            return ProviderError("not_running", f"Nothing answered at {base_url} for that request. "
                                                "Check the address; it usually ends in /v1.")
        if exc.code == 400 and re.search(r"stream", detail, re.IGNORECASE):
            # Older llama-server builds answer "Cannot use tools with stream"; the provider asks again plainly.
            return StreamRefused("server", f"The provider returned an error: {detail}")
        if exc.code == 400 and re.search(r"tool|function", detail, re.IGNORECASE):
            return ProviderError("no_tools", f"{model} can't use tools. Choose a model that supports tool calling.")
        return ProviderError("server", f"The provider returned an error: {detail or f'HTTP {exc.code}'}")
    if isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
        return ProviderError("server", "The model server didn't answer within 5 minutes.")
    if isinstance(exc, URLError):
        return ProviderError("not_running", f"Nothing is answering at {base_url}. Start your model server, then try again.")
    return ProviderError("server", f"The provider returned an error: {exc}")


class LocalServerProvider:
    """Talks to an OpenAI-compatible server over urllib; tool calls arrive as function calls.

    Replies are streamed so the words can be shown as they arrive. A server that
    refuses a streaming request is asked plainly from then on.
    """

    def __init__(self, *, base_url: str, model: str, api_key: str = "", timeout: float = LOCAL_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.streaming = True

    @property
    def label(self) -> str:
        return self.model

    def _request(self, path, body=None, *, timeout=None, on_text=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base_url + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                return _read_payload(response, on_text)
        except StreamCancelled:
            raise
        except Exception as exc:
            raise local_error(exc, self.base_url, self.model) from None

    def complete(self, *, system: str, tools: list[ToolSpec], turns: list[Turn], on_text=None) -> Reply:
        body = local_request_body(self.model, system, tools, turns, stream=self.streaming)
        try:
            payload = self._request("/chat/completions", body, on_text=on_text)
        except StreamRefused:
            if not self.streaming:
                raise
            self.streaming = False
            payload = self._request("/chat/completions", local_request_body(self.model, system, tools, turns))
        try:
            return local_reply(payload, self.model)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("server", "The provider returned an error: unexpected reply shape.") from None

    def check(self) -> str:
        payload = self._request("/models", timeout=CHECK_TIMEOUT)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        ids = [str(row.get("id", "")) for row in rows if isinstance(row, dict)]
        if not ids:
            return "Connected, but the server lists no models."
        if self.model and self.model not in ids:
            return (f"Connected · serving {', '.join(ids[:3])}" + (" and more" if len(ids) > 3 else "")
                    + " · the model field doesn't match it, which llama-server ignores but mlx_lm.server needs.")
        return f"Connected · {len(ids)} models available"
