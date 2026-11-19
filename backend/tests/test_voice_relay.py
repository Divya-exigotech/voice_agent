import asyncio
import json

import pytest
from azure.ai.projects.models import RealtimeServerEventType as T

from app.schemas.customer import Customer
from app.schemas.transcript import TranscriptTurn
from app.services.foundry_session import AgentConversation, AgentSessionError
from app.services.voice_relay import MAX_AUDIO_FRAME_BYTES, VoiceRelay
from tests.fakes import FakeSocket, QueueConnection

pytestmark = pytest.mark.anyio

END = {"type": "websocket.receive", "text": json.dumps({"type": "end"})}


def _setup(
    customers: list[Customer], *, max_seconds: float = 5.0, greeting: bool = True
) -> tuple[QueueConnection, FakeSocket, list[TranscriptTurn], VoiceRelay]:
    conn, ws, turns = QueueConnection(), FakeSocket(), []
    relay = VoiceRelay(
        ws,
        AgentConversation(conn),
        customers[0],
        turns=turns,
        max_seconds=max_seconds,
        request_greeting=greeting,
    )
    return conn, ws, turns, relay


async def test_relays_audio_and_captures_both_speakers(customers: list[Customer]) -> None:
    conn, ws, turns, relay = _setup(customers)
    task = asyncio.create_task(relay.run())
    await conn.events.put({"type": T.RESPONSE_OUTPUT_AUDIO_DELTA.value, "delta": b"\x01\x02"})
    await conn.events.put(
        {"type": T.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE.value, "transcript": "Hello Aarav"}
    )
    await ws.incoming.put({"type": "websocket.receive", "bytes": b"\x09\x08"})
    await conn.events.put(
        {
            "type": T.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED.value,
            "transcript": "I already paid",
        }
    )
    await asyncio.sleep(0.05)
    await ws.incoming.put(END)
    await task

    kinds = [entry[0] for entry in conn.log]
    assert kinds == ["create", "create", "append"]  # context item, greeting, caller audio
    assert conn.log[2][1] == {"audio": b"\x09\x08"}
    assert ws.sent_bytes == [b"\x01\x02"]
    assert ws.sent_json[0] == {"type": "status", "state": "active"}
    assert [(t.turn, t.speaker, t.text) for t in turns] == [
        (1, "agent", "Hello Aarav"),
        (2, "user", "I already paid"),
    ]
    assert all(t.timestamp.tzinfo is not None for t in turns)


async def test_no_greeting_when_disabled(customers: list[Customer]) -> None:
    conn, ws, _, relay = _setup(customers, greeting=False)
    task = asyncio.create_task(relay.run())
    await asyncio.sleep(0.02)
    await ws.incoming.put(END)
    await task
    assert [entry[0] for entry in conn.log] == ["create"]  # only the context item


async def test_barge_in_tells_browser_to_clear_playback(customers: list[Customer]) -> None:
    conn, ws, _, relay = _setup(customers)
    task = asyncio.create_task(relay.run())
    await conn.events.put({"type": T.INPUT_AUDIO_BUFFER_SPEECH_STARTED.value})
    await asyncio.sleep(0.05)
    await ws.incoming.put(END)
    await task
    assert {"type": "clear_playback"} in ws.sent_json


async def test_browser_disconnect_ends_cleanly(customers: list[Customer]) -> None:
    _, ws, _, relay = _setup(customers)
    task = asyncio.create_task(relay.run())
    await ws.incoming.put({"type": "websocket.disconnect"})
    await task


async def test_agent_error_raises_but_keeps_partial_transcript(customers: list[Customer]) -> None:
    conn, _, turns, relay = _setup(customers)
    task = asyncio.create_task(relay.run())
    await conn.events.put(
        {"type": T.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE.value, "transcript": "Hi"}
    )
    await conn.events.put({"type": T.ERROR.value, "error": {"message": "boom"}})
    with pytest.raises(AgentSessionError):
        await task
    assert [t.text for t in turns] == ["Hi"]


async def test_session_time_limit(customers: list[Customer]) -> None:
    _, _, _, relay = _setup(customers, max_seconds=0.05)
    with pytest.raises(TimeoutError):
        await relay.run()


async def test_oversized_audio_frame_rejected(customers: list[Customer]) -> None:
    _, ws, _, relay = _setup(customers)
    task = asyncio.create_task(relay.run())
    await ws.incoming.put(
        {"type": "websocket.receive", "bytes": b"x" * (MAX_AUDIO_FRAME_BYTES + 1)}
    )
    with pytest.raises(AgentSessionError):
        await task
