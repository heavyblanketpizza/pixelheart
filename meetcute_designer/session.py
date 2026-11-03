"""The designer's turn loop: one user message in, provider calls and tool calls until the model is done."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .prompts import SYSTEM_PROMPT, context_block
from .providers import Provider, ProviderError, StreamCancelled, Text, ToolCall, ToolResult, Turn
from .tools import ToolContext, ToolError, run_tool, tool_specs


BUDGET_NOTICE = "That's a lot of changes for one turn. Tell me what to do next."
LENGTH_NOTICE = "The reply was cut short. Ask me to continue."
INVALID_JSON = "The tool input wasn't valid JSON. Send it again."
STOPPED = "Stopped"
PROJECT_CHANGED = "The project changed, so I stopped."


class PortClosed(RuntimeError):
    """The document port was retired (another project opened) while a turn was running."""


class DocumentPort(Protocol):
    def snapshot(self) -> dict: ...

    def apply(self, document: dict, receipt: dict) -> None: ...


@dataclass(frozen=True)
class Limits:
    tool_calls: int = 12
    repeated_failures: int = 3


class DesignerSession:
    """Holds the transcript; ``run_turn`` blocks and reports through ``on_event``.

    Events: ``assistant_delta`` (a fragment of the reply as it arrives), ``assistant``
    (a whole text block, once the reply has settled), ``receipt`` (dict), ``notice``
    (text), ``error`` (text) and, last in every turn, ``done`` (None).
    """

    def __init__(self, provider: Provider, port: DocumentPort, *, limits: Limits = Limits()):
        self.provider = provider
        self.port = port
        self.limits = limits
        self.turns: list[Turn] = []
        self._cancelled = False
        self._stop_notice = STOPPED

    def cancel(self) -> None:
        self._cancelled = True

    def run_turn(self, text: str, on_event: Callable[[str, object], None]) -> None:
        self._cancelled = False
        self._stop_notice = STOPPED
        start = len(self.turns)
        try:
            self._run(text, on_event, start)
        except Exception as exc:  # a port, summary or tool bug must not kill the worker thread silently
            del self.turns[start:]
            on_event("error", f"Something went wrong in the designer ({type(exc).__name__}). Undo if anything looks off.")
        finally:
            on_event("done", None)

    def _run(self, text, on_event, start):
        self.turns.append(Turn("user", [Text(context_block(self.port.snapshot())), Text(text)]))
        calls_made = 0
        failures: dict[str, int] = {}
        last_failure = ""
        while True:
            if self._cancelled:
                on_event("notice", self._stop_notice)
                return
            streamed = []

            def hear(fragment):
                if self._cancelled:
                    raise StreamCancelled()
                if fragment:
                    streamed.append(fragment)
                    on_event("assistant_delta", fragment)

            try:
                reply = self.provider.complete(system=SYSTEM_PROMPT, tools=tool_specs(), turns=list(self.turns), on_text=hear)
            except StreamCancelled:
                # Stop landed mid-reply: keep the words that arrived, drop whatever was still coming.
                partial = "".join(streamed)
                if partial.strip():
                    on_event("assistant", partial)
                    self.turns.append(Turn("assistant", [Text(partial)]))
                on_event("notice", self._stop_notice)
                return
            except ProviderError as exc:
                del self.turns[start:]
                on_event("error", exc.message)
                return
            texts = [block for block in reply.content if isinstance(block, Text) and block.text.strip()]
            for block in texts:
                on_event("assistant", block.text)
            calls = [block for block in reply.content if isinstance(block, ToolCall)]
            if reply.stop in ("length", "refusal") or (self._cancelled and calls):
                # Unfinished or unwanted tool calls have no results; keep only the words.
                if texts:
                    self.turns.append(Turn("assistant", list(texts)))
                if self._cancelled and calls:
                    on_event("notice", self._stop_notice)
                elif reply.stop == "length":
                    on_event("notice", LENGTH_NOTICE)
                else:
                    on_event("notice", reply.detail or "The provider declined to answer.")
                return
            if reply.content:
                self.turns.append(Turn("assistant", list(reply.content), raw=reply.raw))
            if not calls:
                return
            results = []
            for call in calls:
                result = self._run_call(call, on_event)
                if result.is_error:
                    failures[call.name] = failures.get(call.name, 0) + 1
                    last_failure = result.content
                else:
                    failures[call.name] = 0
                results.append(result)
            self.turns.append(Turn("user", results))
            calls_made += len(calls)
            if calls_made >= self.limits.tool_calls:
                on_event("notice", BUDGET_NOTICE)
                return
            if any(count >= self.limits.repeated_failures for count in failures.values()):
                on_event("notice", "I couldn't make that change. " + last_failure)
                return

    def _run_call(self, call: ToolCall, on_event) -> ToolResult:
        if not isinstance(call.input, dict) or "_invalid_json" in call.input:
            return ToolResult(call.id, INVALID_JSON, True)
        try:
            document = self.port.snapshot()
        except PortClosed as exc:
            return self._project_changed(call, exc)
        try:
            outcome = run_tool(call.name, document, call.input, ToolContext(self.provider.label))
        except ToolError as exc:
            return ToolResult(call.id, str(exc), True)
        except Exception as exc:  # a tool bug must not take the panel down with it
            return ToolResult(call.id, f"The tool failed unexpectedly ({type(exc).__name__}). Try a different change.", True)
        if outcome.receipt is not None:
            try:
                self.port.apply(outcome.document, outcome.receipt)
            except PortClosed as exc:
                return self._project_changed(call, exc)
            # Any other failure here is the window's, not the tool's: it ends the turn (see run_turn).
            on_event("receipt", outcome.receipt)
        return ToolResult(call.id, outcome.message)

    def _project_changed(self, call, exc):
        self._cancelled = True
        self._stop_notice = PROJECT_CHANGED
        return ToolResult(call.id, str(exc), True)
