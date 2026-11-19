"""Async wrapper around the Foundry voice-agent realtime connection.

The connection is duck-typed (``conversation.item.create``, ``response.create``, ``recv``)
so the logic can be tested with a fake and so M3 can reuse it for audio.
Nothing here logs or prints conversation content.
"""

import asyncio
import base64
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import aiohttp
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    RealtimeConversationItemMessageSystem,
    RealtimeConversationItemMessageSystemContent,
    RealtimeConversationItemMessageUser,
    RealtimeConversationItemMessageUserContent,
    RealtimeServerEventType,
)
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from azure.core.pipeline.transport import AioHttpTransport
from azure.identity.aio import DefaultAzureCredential

EventKind = Literal[
    "session_created", "user_transcript", "agent_first_output", "agent_audio", "agent_text",
    "response_done", "speech_started", "error", "other",
]  # fmt: skip

_KIND_BY_TYPE: dict[str, EventKind] = {
    RealtimeServerEventType.SESSION_CREATED: "session_created",
    RealtimeServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED: (
        "user_transcript"
    ),
    RealtimeServerEventType.RESPONSE_OUTPUT_TEXT_DELTA: "agent_first_output",
    RealtimeServerEventType.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DELTA: "agent_first_output",
    RealtimeServerEventType.RESPONSE_OUTPUT_AUDIO_DELTA: "agent_audio",
    RealtimeServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED: "speech_started",
    RealtimeServerEventType.RESPONSE_OUTPUT_TEXT_DONE: "agent_text",
    RealtimeServerEventType.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE: "agent_text",
    RealtimeServerEventType.RESPONSE_DONE: "response_done",
    RealtimeServerEventType.ERROR: "error",
}


class AgentSessionError(RuntimeError):
    """The Foundry session reported an error or timed out."""


class FoundryNotConfiguredError(RuntimeError):
    """Endpoint or agent name is missing from the environment."""


class RealtimeConnection(Protocol):
    """The subset of the SDK connection this module relies on."""

    conversation: Any
    response: Any
    input_audio_buffer: Any

    async def recv(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class AgentEvent:
    kind: EventKind
    text: str = ""
    conversation_id: str | None = None
    message: str = ""
    audio: bytes = b""


def audio_bytes(delta: Any) -> bytes:
    """Normalize typed bytes and base64 audio deltas (24 kHz PCM16 mono)."""
    if isinstance(delta, bytes):
        return delta
    if isinstance(delta, str):
        return base64.b64decode(delta)
    msg = "The service returned an unsupported audio delta"
    raise AgentSessionError(msg)


@dataclass(slots=True)
class TurnResult:
    agent_text: str = ""
    user_transcripts: list[str] = field(default_factory=list)
    first_output_s: float | None = None
    total_s: float = 0.0


def event_value(event: Any, name: str) -> Any:
    """Read a field from a typed event or an open mapping."""
    value = getattr(event, name, None)
    if value is None and hasattr(event, "get"):
        value = event.get(name)
    return value


def normalize_event(event: Any) -> AgentEvent:
    kind = _KIND_BY_TYPE.get(str(event_value(event, "type")), "other")
    if kind == "agent_text":
        text = event_value(event, "transcript") or event_value(event, "text") or ""
        return AgentEvent(kind, text=text)
    if kind == "user_transcript":
        return AgentEvent(kind, text=event_value(event, "transcript") or "")
    if kind == "agent_audio":
        return AgentEvent(kind, audio=audio_bytes(event_value(event, "delta")))
    if kind == "session_created":
        return AgentEvent(kind, conversation_id=event_value(event, "conversation_id"))
    if kind == "error":
        error = event_value(event, "error")
        message = (event_value(error, "message") if error is not None else None) or "unknown"
        return AgentEvent(kind, message=str(message))
    return AgentEvent(kind)


@asynccontextmanager
async def foundry_client(endpoint: str) -> AsyncIterator[AIProjectClient]:
    """Build the project client once (e.g. in the app lifespan) and close it on exit."""
    if not endpoint:
        msg = "AZURE_AI_PROJECT_ENDPOINT is not set"
        raise FoundryNotConfiguredError(msg)
    session = aiohttp.ClientSession(
        auto_decompress=False, headers={"Accept-Encoding": "gzip, deflate"}
    )
    transport = AioHttpTransport(session=session)
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=endpoint, credential=credential, allow_preview=True, transport=transport
        ) as client,
    ):
        yield client


def describe_connection_failure(exc: BaseException) -> str:
    """Short, non-sensitive explanation of why opening the Foundry connection failed."""
    if isinstance(exc, ClientAuthenticationError):
        return (
            "Azure sign-in failed: no valid credentials. Set AZURE_TENANT_ID, AZURE_CLIENT_ID "
            "and AZURE_CLIENT_SECRET in backend/.env (or sign in with the Azure CLI) and restart."
        )
    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(exc, (aiohttp.WSServerHandshakeError, HttpResponseError)) and status_code:
        hints = {
            401: "the credentials were not accepted",
            403: "the identity has no permission on the Foundry project",
            404: "the project endpoint or agent name was not found",
        }
        hint = hints.get(int(status_code), "the service rejected the connection")
        return f"Foundry rejected the connection (HTTP {status_code}): {hint}."
    if isinstance(exc, (aiohttp.ClientConnectionError, OSError)):
        return "Could not reach Azure. Check the network and the project endpoint."
    return "Could not connect to the voice agent"


class AgentConversation:
    """One live conversation with the configured Foundry agent."""

    def __init__(self, connection: RealtimeConnection) -> None:
        self._conn = connection
        self.conversation_id: str | None = None

    async def send_context(self, text: str) -> None:
        item = RealtimeConversationItemMessageSystem(
            content=[RealtimeConversationItemMessageSystemContent(type="input_text", text=text)]
        )
        await self._conn.conversation.item.create(item=item)

    async def _send_user_text(self, text: str) -> None:
        item = RealtimeConversationItemMessageUser(
            content=[RealtimeConversationItemMessageUserContent(type="input_text", text=text)]
        )
        await self._conn.conversation.item.create(item=item)
        await self._conn.response.create()

    async def append_audio(self, pcm: bytes) -> None:
        """Forward one chunk of caller audio (24 kHz PCM16 mono) to the agent."""
        await self._conn.input_audio_buffer.append(audio=pcm)

    async def request_greeting(self) -> None:
        """Ask the agent to speak first, without waiting for the response."""
        await self._conn.response.create()

    async def next_event(self) -> AgentEvent:
        event = normalize_event(await self._conn.recv())
        if event.kind == "session_created":
            self.conversation_id = event.conversation_id
        return event

    async def _collect_turn(self, started: float, response_timeout_s: float) -> TurnResult:
        result = TurnResult()
        try:
            async with asyncio.timeout(response_timeout_s):
                while True:
                    event = await self.next_event()
                    now = time.perf_counter()
                    if event.kind == "error":
                        msg = f"Foundry session error: {event.message}"
                        raise AgentSessionError(msg)
                    if (
                        event.kind in ("agent_first_output", "agent_audio")
                        and result.first_output_s is None
                    ):
                        result.first_output_s = now - started
                    elif event.kind == "agent_text":
                        result.agent_text = event.text
                    elif event.kind == "user_transcript":
                        result.user_transcripts.append(event.text)
                    elif event.kind == "response_done":
                        result.total_s = now - started
                        return result
        except TimeoutError:
            msg = "Timed out waiting for the agent response"
            raise AgentSessionError(msg) from None

    async def open_turn(self, *, response_timeout_s: float = 30.0) -> TurnResult:
        """Ask the agent to speak first (greeting) and wait for the full response."""
        started = time.perf_counter()
        await self._conn.response.create()
        return await self._collect_turn(started, response_timeout_s)

    async def ask(self, text: str, *, response_timeout_s: float = 30.0) -> TurnResult:
        """Send one typed user turn and wait for the agent's full response."""
        started = time.perf_counter()
        await self._send_user_text(text)
        return await self._collect_turn(started, response_timeout_s)

    async def ask_system(self, instruction: str, *, response_timeout_s: float = 30.0) -> TurnResult:
        """Send a system instruction and wait for the resulting response."""
        started = time.perf_counter()
        await self.send_context(instruction)
        await self._conn.response.create()
        return await self._collect_turn(started, response_timeout_s)


@asynccontextmanager
async def open_conversation(
    client: AIProjectClient,
    agent_name: str,
    *,
    structured_inputs: Mapping[str, Any] | None = None,
) -> AsyncIterator[AgentConversation]:
    if not agent_name:
        msg = "AZURE_AI_AGENT_NAME is not set"
        raise FoundryNotConfiguredError(msg)
    async with client.beta.voice_agents.realtime.connect(
        agent_name=agent_name, structured_inputs=structured_inputs
    ) as connection:
        yield AgentConversation(connection)
