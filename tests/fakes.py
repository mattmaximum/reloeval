"""A fake Anthropic async client for tests — no network, no API key.
Mimics just enough of the SDK's response shape for fetch.py's two request
styles: forced tool_choice (normalize_city) and output_config.format
structured output + web_search (fetch_category)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeToolUseBlock:
    input: dict
    type: str = "tool_use"


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list
    stop_reason: str = "end_turn"


@dataclass
class FakeMessages:
    handler: Callable[[str, dict], dict]

    async def create(self, **kwargs):
        tool_choice = kwargs.get("tool_choice")
        if tool_choice and tool_choice.get("type") == "tool":
            # e.g. normalize_city's forced resolve_city call
            tool_name = tool_choice["name"]
            result = self.handler(tool_name, kwargs)
            if result is None:
                raise RuntimeError("simulated API failure")
            return FakeResponse(content=[FakeToolUseBlock(input=result)], stop_reason="tool_use")
        # Structured-output mode (fetch_category): handler returns a dict
        # matching output_config.format's schema; web_search is available
        # as a tool but tool_choice is left auto, same as the real request.
        result = self.handler("report_category_data", kwargs)
        if result is None:
            raise RuntimeError("simulated API failure")
        return FakeResponse(content=[FakeTextBlock(text=json.dumps(result))], stop_reason="end_turn")


@dataclass
class FakeAsyncAnthropic:
    """handler(tool_name, call_kwargs) -> dict to return, or None to
    simulate a total API failure for that call."""
    handler: Callable[[str, dict], dict]

    def __post_init__(self):
        self.messages = FakeMessages(handler=self.handler)
