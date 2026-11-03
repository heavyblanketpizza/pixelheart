"""Claude through the official Anthropic SDK, imported only when this provider is chosen."""
from __future__ import annotations

from .providers import ProviderError, Reply, StreamCancelled, Text, ToolCall, ToolResult, ToolSpec, Turn


DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
CHECK_TIMEOUT = 10.0
EFFORT = "medium"
BETAS = ("server-side-fallback-2026-07-01",)
NOT_INSTALLED = "The Anthropic SDK isn't installed with this copy of MEETC#TE."
AUTH_MESSAGE = "MEETC#TE couldn't sign in to Anthropic. Check the API key in Set up AI designer."
_STOPS = {"end_turn": "done", "stop_sequence": "done", "pause_turn": "done", "tool_use": "tool_use",
          "max_tokens": "length", "refusal": "refusal"}


def _blocks(turn: Turn) -> list[dict]:
    blocks = []
    for block in turn.content:
        if isinstance(block, Text):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolCall):
            blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif isinstance(block, ToolResult):
            result = {"type": "tool_result", "tool_use_id": block.id, "content": block.content}
            if block.is_error:
                result["is_error"] = True
            blocks.append(result)
    return blocks


def anthropic_request(model: str, system: str, tools: list[ToolSpec], turns: list[Turn]) -> dict:
    """Keyword arguments for ``client.beta.messages.create``."""
    messages = []
    for turn in turns:
        raw = turn.raw if isinstance(turn.raw, dict) else {}
        if turn.role == "assistant" and raw.get("provider") == "anthropic" and raw.get("content"):
            messages.append({"role": "assistant", "content": list(raw["content"])})
        else:
            messages.append({"role": turn.role, "content": _blocks(turn)})
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema, "strict": True}
                  for tool in tools],
        "messages": messages,
        "output_config": {"effort": EFFORT},
        "betas": list(BETAS),
        "fallbacks": "default",
    }


def anthropic_reply(response, model: str) -> Reply:
    content = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "text" and getattr(block, "text", ""):
            content.append(Text(block.text))
        elif kind == "tool_use":
            input = block.input if isinstance(block.input, dict) else {"_invalid_json": str(block.input)}
            content.append(ToolCall(str(block.id), str(block.name), input))
    stop = _STOPS.get(getattr(response, "stop_reason", None), "done")
    if stop == "done" and any(isinstance(block, ToolCall) for block in content):
        stop = "tool_use"
    detail = ""
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        detail = getattr(details, "explanation", None) or "The provider declined to answer."
    usage = {}
    counts = getattr(response, "usage", None)
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = getattr(counts, field, None)
        if isinstance(value, int):
            usage[field] = value
    return Reply(content, stop, usage, detail, raw={"provider": "anthropic", "content": list(response.content or [])})


def anthropic_error(exc: Exception, model: str) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    try:
        import anthropic
    except ImportError:
        return ProviderError("not_configured", NOT_INSTALLED)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderError("auth", AUTH_MESSAGE)
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError("model", f"{model} isn't available. Check the model name in Set up AI designer.")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError("rate_limit", "Anthropic asked us to slow down. Try again in a minute.")
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderError("server", f"The provider returned an error: {exc.message}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("offline", "Couldn't reach the provider. Check your connection.")
    if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
        # The SDK builds a client without credentials and only complains at request time.
        return ProviderError("auth", AUTH_MESSAGE)
    return ProviderError("server", f"The provider returned an error: {exc}")


class AnthropicProvider:
    """Claude Opus by default; the key comes from settings or the SDK's own credential lookup."""

    def __init__(self, *, model: str, api_key: str = "", client=None):
        self.model = model or DEFAULT_MODEL
        self.api_key = api_key
        self._client = client

    @property
    def client(self):
        """Built on first use: importing the SDK costs over a second and must not delay the window."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ProviderError("not_configured", NOT_INSTALLED) from None
            try:
                self._client = anthropic.Anthropic(api_key=self.api_key or None)
            except Exception:  # the SDK refuses to build a client without any credential
                raise ProviderError("auth", AUTH_MESSAGE) from None
        return self._client

    @property
    def label(self) -> str:
        return self.model

    def complete(self, *, system: str, tools: list[ToolSpec], turns: list[Turn], on_text=None) -> Reply:
        request = anthropic_request(self.model, system, tools, turns)
        try:
            response = self._stream(request, on_text)
            if getattr(response, "stop_reason", None) == "pause_turn":
                request["messages"] = request["messages"] + [{"role": "assistant", "content": list(response.content)}]
                response = self._stream(request, on_text)
        except StreamCancelled:
            raise
        except Exception as exc:
            raise anthropic_error(exc, self.model) from None
        return anthropic_reply(response, self.model)

    def _stream(self, request, on_text):
        """Stream the reply so its words can be shown as they arrive; the whole message comes back at the end."""
        with self.client.beta.messages.stream(**request) as stream:
            for event in stream:
                if on_text is not None:
                    text = getattr(event, "text", "") if getattr(event, "type", None) == "text" else ""
                    on_text(text if isinstance(text, str) else "")
            return stream.get_final_message()

    def check(self) -> str:
        try:
            self.client.with_options(timeout=CHECK_TIMEOUT).models.retrieve(self.model)
        except Exception as exc:
            raise anthropic_error(exc, self.model) from None
        return f"Connected to Anthropic · {self.model}"
