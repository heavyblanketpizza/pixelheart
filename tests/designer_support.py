"""Fixtures shared by the AI designer tests; no network, no settings."""
import re

IDENTITY_FIELDS = ("name", "tagline", "pronouns", "gender", "occupation", "bio", "birthday_season", "birthday_day",
                   "manners", "social_anxiety", "optimism", "romanceable", "home_map")
NULL_IDENTITY = {field: None for field in IDENTITY_FIELDS}
NULL_WHEN = {"occasion": None, "day": None, "season": None, "hearts": None, "date": None,
             "location": None, "item": None, "liked": None}


from pixelheart_designer.providers import ProviderError, Reply, Text, Turn  # noqa: E402


def text_reply(text):
    return Reply([Text(text)], "done")


def tool_reply(*calls, text=""):
    content = ([Text(text)] if text else []) + list(calls)
    return Reply(content, "tool_use")


def fragments(text):
    """Words with their trailing spaces, the way a streaming provider hands text over."""
    return re.findall(r"\S+\s*|\s+", text)


class ScriptedProvider:
    """Plays back queued replies; a queued ProviderError is raised in its turn.

    With ``stream=True`` each reply's words go through ``on_text`` first, word by
    word, and ``on_fragment(provider, fragment)`` runs before each delivery so a
    test can stop or break the stream midway.
    """

    def __init__(self, replies, label="test-model", on_call=None, *, stream=False, on_fragment=None):
        self.replies = list(replies)
        self.label = label
        self.on_call = on_call
        self.stream = stream
        self.on_fragment = on_fragment
        self.calls = []

    def complete(self, *, system, tools, turns, on_text=None):
        self.calls.append((system, list(tools), [Turn(turn.role, list(turn.content), turn.raw) for turn in turns]))
        if self.on_call is not None:
            self.on_call(self)
        if not self.replies:
            raise AssertionError("no scripted reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, ProviderError):
            raise reply
        if self.stream and on_text is not None:
            for block in reply.content:
                if isinstance(block, Text):
                    for fragment in fragments(block.text):
                        if self.on_fragment is not None:
                            self.on_fragment(self, fragment)
                        on_text(fragment)
            on_text("")   # nothing to say, but a chance for Stop to land, as real providers give between events
        return reply

    def check(self):
        return "ok"
