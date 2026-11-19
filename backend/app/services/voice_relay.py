"""Relay between a browser WebSocket and a live Foundry agent conversation.

Protocol with the browser
  browser -> backend  binary: caller audio, 24 kHz PCM16 mono
                      text:   {"type": "end"} to finish the session
  backend -> browser  binary: agent audio, 24 kHz PCM16 mono
                      text:   {"type": "status", "state": "active"}
                              {"type": "clear_playback"}            (caller barged in)
                              {"type": "transcript", "turn", "speaker", "text", "timestamp"}
                              {"type": "error", "message"}

Conversation content is never logged.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.schemas.customer import Customer
from app.schemas.transcript import TranscriptTurn
from app.services.agent_context import build_customer_context
from app.services.foundry_session import AgentConversation, AgentSessionError

MAX_AUDIO_FRAME_BYTES = 64 * 1024


class BrowserSocket(Protocol):
    """The subset of the Starlette WebSocket the relay uses."""

    async def receive(self) -> dict[str, Any]: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def send_json(self, data: Any) -> None: ...


def _is_end_message(text: str) -> bool:
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(message, dict) and message.get("type") == "end"


class VoiceRelay:
    def __init__(
        self,
        websocket: BrowserSocket,
        conversation: AgentConversation,
        customer: Customer,
        *,
        turns: list[TranscriptTurn],
        max_seconds: float,
        request_greeting: bool,
    ) -> None:
        self._ws = websocket
        self._conversation = conversation
        self._customer = customer
        self._turns = turns  # filled in place so a partial transcript survives failures
        self._max_seconds = max_seconds
        self._request_greeting = request_greeting

    async def run(self) -> None:
        await self._conversation.send_context(build_customer_context(self._customer))
        if self._request_greeting:
            await self._conversation.request_greeting()
        await self._ws.send_json({"type": "status", "state": "active"})

        upstream = asyncio.create_task(self._browser_to_agent())
        downstream = asyncio.create_task(self._agent_to_browser())
        done: set[asyncio.Task[None]] = set()
        try:
            async with asyncio.timeout(self._max_seconds):
                done, _ = await asyncio.wait(
                    {upstream, downstream}, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            for task in (upstream, downstream):
                task.cancel()
            await asyncio.gather(upstream, downstream, return_exceptions=True)
        for task in done:
            task.result()  # re-raise a failure from either direction

    async def _browser_to_agent(self) -> None:
        while True:
            message = await self._ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is not None:
                if len(data) > MAX_AUDIO_FRAME_BYTES:
                    msg = "Audio frame too large"
                    raise AgentSessionError(msg)
                await self._conversation.append_audio(data)
            elif (text := message.get("text")) is not None and _is_end_message(text):
                return

    async def _agent_to_browser(self) -> None:
        while True:
            event = await self._conversation.next_event()
            if event.kind == "agent_audio":
                await self._ws.send_bytes(event.audio)
            elif event.kind == "speech_started":
                await self._ws.send_json({"type": "clear_playback"})
            elif event.kind in ("user_transcript", "agent_text"):
                if event.text.strip():
                    speaker = "user" if event.kind == "user_transcript" else "agent"
                    await self._record_turn(speaker, event.text)
            elif event.kind == "error":
                msg = f"Foundry session error: {event.message}"
                raise AgentSessionError(msg)

    async def _record_turn(self, speaker: Literal["agent", "user"], text: str) -> None:
        turn = TranscriptTurn(
            turn=len(self._turns) + 1,
            speaker=speaker,
            text=text,
            timestamp=datetime.now(UTC),
        )
        self._turns.append(turn)
        await self._ws.send_json({"type": "transcript", **turn.model_dump(mode="json")})
