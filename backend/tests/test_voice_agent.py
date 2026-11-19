"""LiveAgentSession against a fake voice agent connection (no network)."""

import asyncio
import base64
import enum
import json
from types import SimpleNamespace

import pytest

from app.services.voice_agent import (
    AgentError,
    LiveAgentSession,
    audio_bytes,
    event_type,
    event_value,
    summarize_session,
)

from .conftest import FakeConnector, agent_audio, agent_calls_tool, agent_says, user_speaks


def recorded(*_args) -> str:
    return json.dumps({"recorded": True})


async def open_session(connector: FakeConnector, *, on_tool_call=recorded, turns=None, events=None, times=None):
    turns = turns if turns is not None else []
    times = times if times is not None else []
    session = LiveAgentSession(
        connector.connect,
        context="CONTEXT",
        on_turn=lambda speaker, text, at: (turns.append((speaker, text)), times.append(at)),
        on_tool_call=on_tool_call,
        on_event=None if events is None else events.append,
        timeout_seconds=1.0,
    )
    await session.start()
    return session, turns


def test_start_injects_context_as_system_message() -> None:
    async def scenario():
        connector = FakeConnector()
        session, _ = await open_session(connector)
        await session.close()
        return session, connector.connections[0]

    session, conn = asyncio.run(scenario())
    assert conn.sent[0] == {
        "type": "conversation.item.create",
        "item": {"type": "message", "role": "system", "content": [{"type": "input_text", "text": "CONTEXT"}]},
    }
    assert session.conversation_id == "conv_1"
    assert session.session_info["tools"] == ["record_outcome"]
    assert conn.exited is True


def test_opening_and_typed_reply() -> None:
    async def scenario():
        connector = FakeConnector([agent_says("Hello."), agent_says("When can you pay?")])
        session, turns = await open_session(connector)
        opening = await session.request_reply(None)
        reply = await session.request_reply("Not yet")
        await session.close()
        return opening, reply, turns, connector.connections[0]

    opening, reply, turns, conn = asyncio.run(scenario())
    assert (opening, reply) == ("Hello.", "When can you pay?")
    assert turns == [("agent", "Hello."), ("agent", "When can you pay?")]
    assert conn.items("message")[1] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Not yet"}],
    }
    assert conn.creates == 2


def test_duplicate_text_and_audio_transcripts_are_merged() -> None:
    events = [
        {"type": "response.output_text.done", "text": "Hi there."},
        {"type": "response.output_audio_transcript.done", "transcript": "Hi there."},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ]

    async def scenario():
        session, turns = await open_session(FakeConnector([events]))
        reply = await session.request_reply(None)
        await session.close()
        return reply, turns

    assert asyncio.run(scenario()) == ("Hi there.", [("agent", "Hi there.")])


def test_falls_back_to_response_output_message_text() -> None:
    events = [{
        "type": "response.done",
        "response": {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_audio", "transcript": "From output."}]}
        ]},
    }]

    async def scenario():
        session, _ = await open_session(FakeConnector([events]))
        reply = await session.request_reply(None)
        await session.close()
        return reply

    assert asyncio.run(scenario()) == "From output."


def test_tool_call_round_trip() -> None:
    calls = []

    def handler(name, args):
        calls.append((name, json.loads(args)))
        return json.dumps({"recorded": True})

    async def scenario():
        connector = FakeConnector([
            agent_calls_tool({"outcome": "reported_paid"}, call_id="call_9"),
            agent_says("Thanks, noted."),
        ])
        session, turns = await open_session(connector, on_tool_call=handler)
        reply = await session.request_reply("I paid already")
        await session.close()
        return reply, turns, connector.connections[0]

    reply, turns, conn = asyncio.run(scenario())
    assert reply == "Thanks, noted."
    assert calls == [("record_outcome", {"outcome": "reported_paid"})]
    assert conn.items("function_call_output") == [
        {"type": "function_call_output", "call_id": "call_9", "output": '{"recorded": true}'}
    ]
    assert conn.creates == 2  # follow-up response requested after the tool output
    assert turns == [("agent", "Thanks, noted.")]


def test_tool_handler_exception_is_returned_to_agent() -> None:
    def boom(name, args):
        raise RuntimeError("db down")

    async def scenario():
        connector = FakeConnector([agent_calls_tool({"outcome": "reported_paid"}), agent_says("OK.")])
        session, _ = await open_session(connector, on_tool_call=boom)
        reply = await session.request_reply("hi")
        await session.close()
        return reply, connector.connections[0]

    reply, conn = asyncio.run(scenario())
    assert reply == "OK."
    output = json.loads(conn.items("function_call_output")[0]["output"])
    assert output["recorded"] is False and "db down" in output["error"]


def test_endless_tool_calls_are_bounded() -> None:
    async def scenario():
        connector = FakeConnector([agent_calls_tool({}, call_id=f"c{i}") for i in range(10)])
        session, _ = await open_session(connector)
        try:
            await session.request_reply("hi")
        finally:
            await session.close()

    with pytest.raises(AgentError, match="kept calling tools"):
        asyncio.run(scenario())


def test_user_audio_transcription_becomes_user_turn() -> None:
    async def scenario():
        connector = FakeConnector()
        session, turns = await open_session(connector)
        await connector.connections[0].emit(
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": " I paid "}
        )
        await asyncio.sleep(0.05)
        await session.close()
        return turns

    assert asyncio.run(scenario()) == [("user", "I paid")]


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([{"type": "error", "error": {"message": "rate limited"}}], "rate limited"),
        ([{"type": "response.done", "response": {"status": "failed", "status_details": "quota"}}], "failed"),
        ([ConnectionResetError("socket closed")], "socket closed"),
        ([], "did not respond"),
    ],
    ids=["error-event", "failed-response", "connection-drop", "timeout"],
)
def test_reply_failures_raise_agent_error(events, message) -> None:
    async def scenario():
        session, _ = await open_session(FakeConnector([events]))
        try:
            await session.request_reply("hi")
        finally:
            await session.close()

    with pytest.raises(AgentError, match=message):
        asyncio.run(scenario())


def test_connection_drop_closes_session() -> None:
    async def scenario():
        session, _ = await open_session(FakeConnector([[ConnectionResetError("gone")]]))
        with pytest.raises(AgentError):
            await session.request_reply("hi")
        is_open = session.is_open
        with pytest.raises(AgentError, match="gone"):
            await session.request_reply("again")
        await session.close()
        return is_open

    assert asyncio.run(scenario()) is False


@pytest.mark.parametrize(
    ("configure", "message"),
    [
        (lambda c: setattr(c, "fail_connect", PermissionError("401 Unauthorized")), "Could not connect"),
        (lambda c: setattr(c, "emit_session_created", False), "Timed out waiting"),
    ],
    ids=["auth-failure", "no-session-created"],
)
def test_start_failures(configure, message) -> None:
    connector = FakeConnector()
    configure(connector)
    with pytest.raises(AgentError, match=message):
        asyncio.run(open_session(connector))


def test_voice_audio_in_and_out() -> None:
    """Mic audio is appended; agent audio and speech events reach the listener."""

    async def scenario():
        connector = FakeConnector()
        connector.audio_script = [
            [*user_speaks("I will pay on Friday"), agent_audio(b"\x05\x00"), *agent_says("Thank you.")]
        ]
        events, turns, times = [], [], []
        session, _ = await open_session(connector, events=events, turns=turns, times=times)
        await session.append_audio(b"\x00\x01" * 10)
        await asyncio.sleep(0.05)
        await session.close()
        return connector.connections[0], events, turns, times

    conn, events, turns, times = asyncio.run(scenario())
    assert conn.audio == [b"\x00\x01" * 10]
    assert {"type": "speech_started"} in events
    assert {"type": "speech_stopped"} in events
    assert b"\x05\x00" in events  # base64 delta decoded to raw PCM
    assert {"type": "response_done"} in events
    assert turns == [("user", "I will pay on Friday"), ("agent", "Thank you.")]
    assert times[0] is not None and times[0].tzinfo is not None  # placed at commit time
    assert times[1] is None


def test_late_user_transcription_keeps_commit_time() -> None:
    """Transcription that completes after the agent reply still carries the commit time."""

    async def scenario():
        connector = FakeConnector()
        connector.audio_script = [[
            {"type": "input_audio_buffer.committed", "item_id": "u1"},
            *agent_says("Sure."),
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Call me later"},
        ]]
        turns, times = [], []
        session, _ = await open_session(connector, turns=turns, times=times)
        await session.append_audio(b"\x00\x00")
        await asyncio.sleep(0.05)
        await session.close()
        return turns, times

    turns, times = asyncio.run(scenario())
    assert turns == [("agent", "Sure."), ("user", "Call me later")]
    assert times[1] is not None  # SessionManager uses it to order the user turn first


def test_create_response_triggers_opening_without_waiting() -> None:
    async def scenario():
        connector = FakeConnector([agent_says("Hello there.")])
        turns = []
        session, _ = await open_session(connector, turns=turns)
        await session.create_response()
        await asyncio.sleep(0.05)
        await session.close()
        return turns

    assert asyncio.run(scenario()) == [("agent", "Hello there.")]


def test_tool_round_limit_resets_between_voice_responses() -> None:
    async def scenario():
        connector = FakeConnector()
        # Four separate voice exchanges, each with one tool call then a reply.
        connector.script = [agent_says(f"Reply {i}.") for i in range(4)]
        connector.audio_script = [agent_calls_tool({"outcome": "reported_paid"}, call_id=f"c{i}") for i in range(4)]
        turns = []
        session, _ = await open_session(connector, turns=turns)
        for _ in range(4):
            await session.append_audio(b"\x00\x00")
            await asyncio.sleep(0.03)
        still_open = session.is_open
        await session.close()
        return turns, still_open

    turns, still_open = asyncio.run(scenario())
    assert still_open
    assert [t for _, t in turns] == ["Reply 0.", "Reply 1.", "Reply 2.", "Reply 3."]


def test_unexpected_disconnect_notifies_listener() -> None:
    async def scenario():
        connector = FakeConnector()
        events = []
        session, _ = await open_session(connector, events=events)
        await connector.connections[0].emit(ConnectionResetError("network"))
        await asyncio.sleep(0.05)
        is_open = session.is_open
        with pytest.raises(AgentError):
            await session.append_audio(b"\x00\x00")
        await session.close()
        return events, is_open

    events, is_open = asyncio.run(scenario())
    assert is_open is False
    assert events[-1]["type"] == "agent_closed"
    assert "network" in events[-1]["reason"]


def test_requested_close_does_not_notify_listener() -> None:
    async def scenario():
        events = []
        session, _ = await open_session(FakeConnector(), events=events)
        await session.close()
        return events

    assert all(e.get("type") != "agent_closed" for e in asyncio.run(scenario()) if isinstance(e, dict))


def test_bad_audio_chunk_is_dropped_without_killing_session() -> None:
    async def scenario():
        connector = FakeConnector()
        events = []
        session, _ = await open_session(connector, events=events)
        await connector.connections[0].emit({"type": "response.output_audio.delta", "delta": 123})
        await asyncio.sleep(0.05)
        is_open = session.is_open
        await session.close()
        return is_open

    assert asyncio.run(scenario()) is True


def test_audio_bytes_normalization() -> None:
    assert audio_bytes(b"\x01\x02") == b"\x01\x02"
    assert audio_bytes(base64.b64encode(b"\x03\x04").decode()) == b"\x03\x04"
    with pytest.raises(AgentError):
        audio_bytes(42)


def test_event_helpers_handle_typed_and_mapping_events() -> None:
    class Kind(str, enum.Enum):
        DONE = "response.done"

    typed = SimpleNamespace(type=Kind.DONE, response=SimpleNamespace(status="completed"))
    assert event_type(typed) == "response.done"
    assert event_value(typed, "response").status == "completed"
    assert event_type({"type": "error"}) == "error"
    assert event_value(None, "x") is None
    assert summarize_session(None) == {}
    assert summarize_session({"model": "m", "modalities": ["audio"], "tools": [SimpleNamespace(name="t")]}) == {
        "model": "m",
        "output_modalities": ["audio"],
        "tools": ["t"],
    }


# --- Agent that starts speaking by itself (greeting configured in Foundry) -------

ALREADY_RESPONDING = {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "code": "conversation_already_has_active_response",
        "message": "Conversation already has an active response.",
    },
}


def greeting(text: str = "Hi from Foundry.") -> list:
    return [{"type": "response.created"}, *agent_says(text)]


def reject_first_create(conn) -> None:
    """The first response.create arrives while the agent's own greeting is running."""

    async def create() -> None:
        conn.creates += 1
        if conn.creates == 1:
            await conn.emit(ALREADY_RESPONDING, *greeting())
        else:
            await conn.emit(*agent_says(f"Reply {conn.creates}"))

    conn.response = SimpleNamespace(create=create)


def test_opening_reuses_greeting_the_agent_already_started() -> None:
    async def scenario():
        connector = FakeConnector([agent_says("Should not be requested")])
        session, turns = await open_session(connector)
        conn = connector.connections[0]
        await conn.emit(*greeting())
        await asyncio.sleep(0.05)
        opening = await session.request_reply(None)
        await session.close()
        return opening, turns, conn

    opening, turns, conn = asyncio.run(scenario())
    assert opening == "Hi from Foundry."
    assert conn.creates == 0
    assert turns == [("agent", "Hi from Foundry.")]


def test_opening_rejected_while_agent_greets_uses_the_greeting() -> None:
    async def scenario():
        connector = FakeConnector()
        session, turns = await open_session(connector)
        conn = connector.connections[0]
        reject_first_create(conn)
        opening = await session.request_reply(None)
        await session.close()
        return opening, turns, conn

    opening, turns, conn = asyncio.run(scenario())
    assert opening == "Hi from Foundry."
    assert conn.creates == 1
    assert turns == [("agent", "Hi from Foundry.")]


def test_typed_message_rejected_while_agent_greets_is_answered_after() -> None:
    async def scenario():
        connector = FakeConnector()
        session, turns = await open_session(connector)
        conn = connector.connections[0]
        reject_first_create(conn)
        reply = await session.request_reply("Who is this?")
        await session.close()
        return reply, turns, conn

    reply, turns, conn = asyncio.run(scenario())
    assert reply == "Reply 2"
    assert conn.creates == 2
    assert turns == [("agent", "Hi from Foundry."), ("agent", "Reply 2")]


def test_voice_opening_not_requested_when_agent_already_greeted() -> None:
    async def scenario():
        connector = FakeConnector()
        session, _ = await open_session(connector)
        conn = connector.connections[0]
        await conn.emit(*greeting())
        await asyncio.sleep(0.05)
        await session.create_response()
        await session.close()
        return conn

    assert asyncio.run(scenario()).creates == 0


# --- Response cancelled before any text (microphone barge-in / echo) -------------


def cancelled_response(reason: str = "turn_detected") -> list:
    return [
        {"type": "response.created"},
        {"type": "response.done", "response": {"status": "cancelled", "status_details": {"type": "cancelled", "reason": reason}, "output": []}},
    ]


def test_typed_reply_waits_past_a_cancelled_response(monkeypatch) -> None:
    import app.services.voice_agent as voice_agent

    monkeypatch.setattr(voice_agent, "EMPTY_REPLY_RETRY_SECONDS", 0.01)

    async def scenario():
        connector = FakeConnector([cancelled_response(), agent_says("When can you pay?")])
        session, _ = await open_session(connector)
        reply = await session.request_reply("Hello?")
        await session.close()
        return reply, connector.connections[0]

    reply, conn = asyncio.run(scenario())
    assert reply == "When can you pay?"
    assert conn.creates == 2  # asked again after the cancelled response


def test_cancelled_response_followed_by_agent_answer_is_not_requested_again(monkeypatch) -> None:
    import app.services.voice_agent as voice_agent

    monkeypatch.setattr(voice_agent, "EMPTY_REPLY_RETRY_SECONDS", 0.2)

    async def scenario():
        connector = FakeConnector([[*cancelled_response(), {"type": "response.created"}, *agent_says("Sorry, go ahead.")]])
        session, _ = await open_session(connector)
        reply = await session.request_reply("Hello?")
        await asyncio.sleep(0.3)
        await session.close()
        return reply, connector.connections[0]

    reply, conn = asyncio.run(scenario())
    assert reply == "Sorry, go ahead."
    assert conn.creates == 1


def test_repeatedly_empty_responses_fail_with_reason(monkeypatch) -> None:
    import app.services.voice_agent as voice_agent

    monkeypatch.setattr(voice_agent, "EMPTY_REPLY_RETRY_SECONDS", 0.01)

    async def scenario():
        connector = FakeConnector([cancelled_response()] * 3)
        session, _ = await open_session(connector)
        try:
            await session.request_reply("Hello?")
        finally:
            await session.close()

    with pytest.raises(AgentError, match="empty reply \\(response cancelled: turn_detected\\)"):
        asyncio.run(scenario())
