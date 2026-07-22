"""A fake Anthropic async client for tests — no network, no API key.
Mimics just enough of the SDK's response shape (message.content[i].type /
.input) for fetch.py's tool-use parsing to work against it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeToolUseBlock:
    input: dict
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list


@dataclass
class FakeMessages:
    handler: Callable[[dict], dict]

    async def create(self, **kwargs):
        tool_name = kwargs["tools"][0]["name"]
        result = self.handler(tool_name, kwargs)
        if result is None:
            raise RuntimeError("simulated API failure")
        return FakeResponse(content=[FakeToolUseBlock(input=result)])


@dataclass
class FakeAsyncAnthropic:
    """handler(tool_name, call_kwargs) -> dict to return as tool input, or
    None to simulate a total API failure for that call."""
    handler: Callable[[str, dict], dict]

    def __post_init__(self):
        self.messages = FakeMessages(handler=self.handler)
