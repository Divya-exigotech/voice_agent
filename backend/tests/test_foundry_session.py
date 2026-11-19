from types import SimpleNamespace
from typing import Any

import pytest

from app.services.foundry_session import (
    AgentConversation,
    AgentSessionError,
    normalize_event,
)

pytestmark = pytest.mark.anyio


class _Recorder:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    async def create(self, **kwargs: Any) -> None:
        self._log.append(("create", kwargs))


class FakeConnection:
    """Replays scripted server events and records what the client sends."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.log: list[tuple[str, Any]] = []
        self._events = list(events)
        self.conversation = SimpleNamespace(item=_Recorder(self.log))
        self.response = _Recorder(self.log)

    async def recv(self) -> dict[str, Any]:
        return self._events.pop(0)


def _turn(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "response.output_text.delta", "delta": "x"},
        {"type": "response.output_text.done", "text": text},
        {"type": "response.done"},
    ]


async def test_ask_returns_text_and_latency_and_sends_user_item() -> None:
    conn = FakeConnection(_turn("Your invoice is 2500 INR."))
    result = await AgentConversation(conn).ask("hello")
    assert result.agent_text == "Your invoice is 2500 INR."
    assert result.first_output_s is not None
    assert result.total_s >= result.first_output_s
    kinds = [entry[0] for entry in conn.log]
    assert kinds == ["create", "create"]  # user item, then response.create


async def test_audio_transcript_done_is_treated_as_agent_text() -> None:
    events = [
        {"type": "response.output_audio_transcript.done", "transcript": "Hi there"},
        {"type": "response.done"},
    ]
    result = await AgentConversation(FakeConnection(events)).ask("hi")
    assert result.agent_text == "Hi there"


async def test_session_created_records_conversation_id() -> None:
    events = [{"type": "session.created", "conversation_id": "conv_1"}, *_turn("ok")]
    conversation = AgentConversation(FakeConnection(events))
    await conversation.ask("hi")
    assert conversation.conversation_id == "conv_1"


async def test_error_event_raises_without_echoing_raw_event() -> None:
    events = [{"type": "error", "error": {"message": "boom"}}]
    with pytest.raises(AgentSessionError, match="boom"):
        await AgentConversation(FakeConnection(events)).ask("hi")


async def test_timeout_raises() -> None:
    class Silent(FakeConnection):
        async def recv(self) -> dict[str, Any]:
            import asyncio

            await asyncio.sleep(10)
            return {}

    with pytest.raises(AgentSessionError, match="Timed out"):
        await AgentConversation(Silent([])).ask("hi", response_timeout_s=0.05)


async def test_send_context_creates_system_item() -> None:
    conn = FakeConnection([])
    await AgentConversation(conn).send_context("facts")
    ((_, kwargs),) = conn.log
    assert kwargs["item"].as_dict()["role"] == "system"


def test_unknown_event_is_other() -> None:
    assert normalize_event({"type": "rate_limits.updated"}).kind == "other"
