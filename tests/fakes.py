"""A fake OpenAI-compatible async client for tests — no network, no API
key. Mimics just enough of the openai SDK's chat.completions response
shape for fetch.py's two request styles: forced tool_choice (normalize_city)
and response_format json_schema + web plugin (fetch_category)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class FakeFunctionCall:
    name: str
    arguments: str  # JSON string, matching the real SDK's shape


@dataclass
class FakeToolCall:
    function: FakeFunctionCall
    id: str = "call_1"
    type: str = "function"


@dataclass
class FakeMessage:
    content: Optional[str] = None
    tool_calls: Optional[list] = None


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeChatCompletion:
    choices: list


@dataclass
class FakeChatCompletions:
    handler: Callable[[str, dict], dict]

    async def create(self, **kwargs):
        tool_choice = kwargs.get("tool_choice")
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            # e.g. normalize_city's forced resolve_city call
            tool_name = tool_choice["function"]["name"]
            result = self.handler(tool_name, kwargs)
            if result is None:
                raise RuntimeError("simulated API failure")
            tool_call = FakeToolCall(function=FakeFunctionCall(name=tool_name, arguments=json.dumps(result)))
            return FakeChatCompletion(choices=[FakeChoice(message=FakeMessage(tool_calls=[tool_call]))])
        # response_format json_schema mode (fetch_category): handler returns
        # a dict matching the schema; the web plugin is in extra_body but
        # tool_choice is not forced, same as the real request.
        result = self.handler("category_data", kwargs)
        if result is None:
            raise RuntimeError("simulated API failure")
        return FakeChatCompletion(choices=[FakeChoice(message=FakeMessage(content=json.dumps(result)))])


@dataclass
class FakeChat:
    handler: Callable[[str, dict], dict]

    def __post_init__(self):
        self.completions = FakeChatCompletions(handler=self.handler)


@dataclass
class FakeAsyncOpenAI:
    """handler(name, call_kwargs) -> dict to return, or None to simulate a
    total API failure for that call. `name` is the forced tool's name for
    normalize_city, or the literal "category_data" for fetch_category."""
    handler: Callable[[str, dict], dict]

    def __post_init__(self):
        self.chat = FakeChat(handler=self.handler)
