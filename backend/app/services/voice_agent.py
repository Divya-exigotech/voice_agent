"""Live session with an existing Microsoft Foundry voice agent.

The agent (model, instructions, voice, tools) is configured in Foundry and is
applied automatically when the session connects by agent name, as in
https://github.com/microsoft/voice-agent. The application:

  * connects with `client.voice_agents.realtime.connect(agent_name=...)`
    using Microsoft Entra ID (see azure_auth.py) — no keys;
  * adds one system message with the current customer's facts;
  * runs a background event pump that captures transcript turns and executes
    client-side function tools (validated by the backend);
  * typed turns (M2): sends user text and waits for the agent reply;
  * voice (M3): appends browser microphone PCM16 (24 kHz mono) to the input
    audio buffer and forwards the agent's audio deltas and speech events to a
    listener. Turn detection (server VAD) is whatever the agent is configured with.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

from app.services import latency
from app.services.agent_prompt import RECORD_OUTCOME_TOOL, RECORD_OUTCOME_TOOL_NAME

logger = logging.getLogger(__name__)

# (speaker, text, when the turn happened or None for "now") -> None
TurnCallback = Callable[[str, str, "datetime | None"], None]
# (tool name, JSON arguments) -> JSON output string returned to the agent
ToolHandler = Callable[[str, str], str]
# Receives agent audio (bytes, PCM16 24 kHz mono) and UI events (dicts).
EventListener = Callable[[Any], None]

MAX_TOOL_ROUNDS = 3
# A typed request whose response is cancelled before any text (barge-in) is
# asked again this many times, after this long without a new response.
MAX_EMPTY_RETRIES = 2
EMPTY_REPLY_RETRY_SECONDS = 2.0

AGENT_TRANSCRIPT_DELTA_EVENTS = (
    "response.output_audio_transcript.delta",
    "response.output_text.delta",
)
AGENT_TRANSCRIPT_EVENTS = ("response.output_audio_transcript.done", "response.output_text.done")


class AgentError(Exception):
    """The voice agent session failed or returned an unusable response."""


class AgentConnector(Protocol):
    def connect(self) -> AbstractAsyncContextManager[Any]:
        """Return an async context manager yielding a realtime connection."""
        ...

    async def aclose(self) -> None: ...


def event_value(obj: Any, name: str) -> Any:
    """Read a field from a typed SDK event/model or a plain mapping."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None and hasattr(obj, "get"):
        value = obj.get(name)
    return value


def event_type(obj: Any) -> str:
    value = event_value(obj, "type")
    return str(getattr(value, "value", value) or "")


def audio_bytes(delta: Any) -> bytes:
    """Normalize typed bytes and base64 audio deltas."""
    if isinstance(delta, (bytes, bytearray)):
        return bytes(delta)
    if isinstance(delta, str):
        return base64.b64decode(delta)
    raise AgentError("The voice agent returned an unsupported audio delta")


def summarize_session(session: Any) -> dict[str, Any]:
    """Best-effort summary of the session config the agent applied."""
    if session is None:
        return {}
    tools = event_value(session, "tools") or []
    return {
        "model": event_value(session, "model"),
        "output_modalities": event_value(session, "output_modalities")
        or event_value(session, "modalities"),
        "tools": [event_value(t, "name") or event_type(t) for t in tools],
    }


class LiveAgentSession:
    """One realtime connection to the Foundry voice agent for one app session."""

    def __init__(
        self,
        connect: Callable[[], AbstractAsyncContextManager[Any]],
        *,
        context: str,
        on_turn: TurnCallback,
        on_tool_call: ToolHandler,
        on_event: EventListener | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self._connect = connect
        self._context = context
        self._on_turn = on_turn
        self._on_tool_call = on_tool_call
        self._on_event = on_event
        self._timeout = timeout_seconds
        # item_id -> when the user's speech was committed (for turn ordering)
        self._speech_committed_at: dict[str, datetime] = {}
        self._closing = False
        # time.monotonic() when the customer stopped speaking; cleared at the
        # agent's first audio (voice reply latency).
        self._speech_stopped_at: float | None = None

        self._cm: AbstractAsyncContextManager[Any] | None = None
        self._conn: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[str] | None = None
        self._response_text: list[str] = []
        self._transcript_deltas: list[str] = []
        self._reply_parts: list[str] = []
        self._tool_rounds = 0
        self._closed = False
        self._close_reason: str | None = None
        # The agent may start a response by itself (e.g. a greeting configured in
        # Foundry), so track whether one is running before asking for another.
        self._idle = asyncio.Event()
        self._idle.set()
        self._responses_started = 0
        self._last_reply = ""
        # Our response.create was rejected because one was already running:
        # ask again once it finishes (a typed message still needs its answer).
        self._create_after_done = False
        self._pending_user_text = False
        self._empty_retries = 0
        self._retry_task: asyncio.Task[None] | None = None

        self.session_info: dict[str, Any] = {}
        self.conversation_id: str | None = None

    @property
    def is_open(self) -> bool:
        return (
            self._conn is not None
            and not self._closed
            and self._pump_task is not None
            and not self._pump_task.done()
        )

    # --- Lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        self._cm = self._connect()
        try:
            self._conn = await self._cm.__aenter__()
        except Exception as exc:
            self._cm = None
            raise AgentError(f"Could not connect to the Foundry voice agent: {exc}") from exc

        self._pump_task = asyncio.create_task(self._pump(), name="voice-agent-pump")
        try:
            await asyncio.wait_for(self._ready.wait(), self._timeout)
        except TimeoutError as exc:
            await self.close()
            raise AgentError("Timed out waiting for the voice agent session to start") from exc
        if self._closed:
            reason = self._close_reason
            await self.close()
            raise AgentError(reason or "The voice agent session closed during start-up")

        try:
            if RECORD_OUTCOME_TOOL_NAME not in (self.session_info.get("tools") or []):
                # The Foundry agent has no outcome tool: send it with this session
                # so payment promises and other outcomes are still recorded.
                logger.info("Agent has no %s tool; sending it with the session", RECORD_OUTCOME_TOOL_NAME)
                await self._send(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "tools": [RECORD_OUTCOME_TOOL],
                            "tool_choice": "auto",
                        },
                    }
                )
            await self._send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "system",
                        "content": [{"type": "input_text", "text": self._context}],
                    },
                }
            )
        except AgentError:
            await self.close()
            raise

    async def close(self) -> None:
        self._closing = True
        self._closed = True
        if self._retry_task is not None and not self._retry_task.done():
            self._retry_task.cancel()
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump_task
        if self._cm is not None:
            cm, self._cm = self._cm, None
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)
        self._fail_pending(AgentError("The voice agent session was closed"))

    # --- Typed conversation (M2) ----------------------------------------------

    async def request_reply(self, user_text: str | None) -> str:
        """Send a user text message (or none, for the opening) and await the reply."""
        async with self._lock:
            if not self.is_open:
                raise AgentError(self._close_reason or "The voice agent session is not open")
            if user_text is None and self._responses_started:
                # The agent already greeted by itself: that greeting is the opening
                # (unless it was cancelled before saying anything).
                opening = await self._await_idle_reply()
                if opening:
                    return opening
            try:
                await asyncio.wait_for(self._idle.wait(), self._timeout)
            except TimeoutError as exc:
                raise AgentError(
                    f"The voice agent did not finish its previous response within {self._timeout:.0f}s"
                ) from exc
            self._pending = asyncio.get_running_loop().create_future()
            self._pending_user_text = user_text is not None
            self._create_after_done = False
            self._empty_retries = 0
            self._reply_parts = []
            self._tool_rounds = 0
            try:
                if user_text is not None:
                    await self._send(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": user_text}],
                            },
                        }
                    )
                await self._conn.response.create()
                return await asyncio.wait_for(self._pending, self._timeout)
            except TimeoutError as exc:
                raise AgentError(
                    f"The voice agent did not respond within {self._timeout:.0f}s"
                ) from exc
            finally:
                self._pending = None
                self._create_after_done = False

    async def _await_idle_reply(self) -> str:
        """Wait for the running response (if any) and return the last agent reply."""
        try:
            await asyncio.wait_for(self._idle.wait(), self._timeout)
        except TimeoutError as exc:
            raise AgentError(
                f"The voice agent did not respond within {self._timeout:.0f}s"
            ) from exc
        if self._closed:
            raise AgentError(self._close_reason or "The voice agent session ended")
        return self._last_reply

    # --- Voice (M3) --------------------------------------------------------------

    async def append_audio(self, pcm16: bytes) -> None:
        """Stream microphone PCM16 (24 kHz mono) into the agent's input buffer."""
        if not self.is_open:
            raise AgentError(self._close_reason or "The voice agent session is not open")
        try:
            await self._conn.input_audio_buffer.append(audio=pcm16)
        except Exception as exc:
            raise AgentError(f"Could not stream audio to the voice agent: {exc}") from exc

    async def create_response(self) -> None:
        """Ask the agent to speak now (e.g. its opening line) without waiting for it.

        Skipped when the agent already started speaking by itself.
        """
        if not self.is_open:
            raise AgentError(self._close_reason or "The voice agent session is not open")
        if self._responses_started:
            return
        await self._conn.response.create()

    def _emit(self, event: Any) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # a slow/broken listener must not kill the pump
                logger.exception("Voice event listener failed")

    # --- Event pump -------------------------------------------------------------

    async def _pump(self) -> None:
        try:
            async for event in self._conn:
                await self._handle(event)
            self._close_reason = "The voice agent session ended"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Voice agent connection failed")
            self._close_reason = f"Voice agent connection failed: {exc}"
        finally:
            self._closed = True
            self._ready.set()
            self._idle.set()  # wake anyone waiting for a response to finish
            reason = self._close_reason or "The voice agent session ended"
            self._fail_pending(AgentError(reason))
            if not self._closing:
                self._emit({"type": "agent_closed", "reason": reason})

    async def _handle(self, event: Any) -> None:
        kind = event_type(event)
        if kind == "session.created":
            session = event_value(event, "session")
            self.conversation_id = event_value(event, "conversation_id") or event_value(
                session, "conversation_id"
            )
            self.session_info = summarize_session(session)
            self._ready.set()
        elif kind == "response.output_audio.delta":
            if self._speech_stopped_at is not None:
                reply_ms = (time.monotonic() - self._speech_stopped_at) * 1000
                logger.info(
                    "latency voice_reply_ms=%d (customer stopped speaking -> first agent audio)",
                    reply_ms,
                )
                latency.recorder.record(latency.VOICE_REPLY, reply_ms)
                self._speech_stopped_at = None
            try:
                self._emit(audio_bytes(event_value(event, "delta")))
            except (AgentError, ValueError) as exc:  # skip one bad chunk, keep the call alive
                logger.warning("Dropping agent audio chunk: %s", exc)
        elif kind in AGENT_TRANSCRIPT_DELTA_EVENTS:
            delta = event_value(event, "delta")
            if isinstance(delta, str):
                self._transcript_deltas.append(delta)
        elif kind in AGENT_TRANSCRIPT_EVENTS:
            self._transcript_deltas = []  # the final text supersedes the partial deltas
            text = (event_value(event, "transcript") or event_value(event, "text") or "").strip()
            if text and (not self._response_text or self._response_text[-1] != text):
                self._response_text.append(text)
        elif kind == "input_audio_buffer.speech_started":
            # Barge-in: the browser drops queued agent audio.
            self._speech_stopped_at = None
            self._emit({"type": "speech_started"})
        elif kind == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = time.monotonic()
            self._emit({"type": "speech_stopped"})
        elif kind == "input_audio_buffer.committed":
            item_id = event_value(event, "item_id")
            if item_id:
                self._speech_committed_at[str(item_id)] = datetime.now(UTC)
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = (event_value(event, "transcript") or "").strip()
            at = self._speech_committed_at.pop(str(event_value(event, "item_id")), None)
            if text:
                self._on_turn("user", text, at)
        elif kind == "conversation.item.input_audio_transcription.failed":
            self._speech_committed_at.pop(str(event_value(event, "item_id")), None)
            logger.warning("User speech transcription failed: %s", event_value(event, "error"))
        elif kind == "error":
            error = event_value(event, "error")
            message = event_value(error, "message") or "Voice agent error"
            if self._is_active_response_error(error, message):
                # The agent was already responding (e.g. its own greeting). That
                # response answers an opening request; a typed message is asked
                # for again once it finishes.
                logger.info("Voice agent was already responding; waiting for it to finish")
                if self._pending is not None and not self._pending.done():
                    self._create_after_done = self._pending_user_text
                return
            logger.warning("Voice agent error event: %s", message)
            self._fail_pending(AgentError(message))
        elif kind == "response.created":
            self._responses_started += 1
            self._idle.clear()
        elif kind == "response.done":
            self._idle.set()
            await self._on_response_done(event_value(event, "response"))

    async def _on_response_done(self, response: Any) -> None:
        texts, self._response_text = self._response_text, []
        # An interrupted (barge-in) response never sends a final transcript, so
        # keep what the customer actually heard instead of dropping it.
        partial, self._transcript_deltas = "".join(self._transcript_deltas).strip(), []
        if not texts and partial:
            texts = [partial]
        status = str(event_value(response, "status") or "completed")
        if status == "failed":
            details = event_value(response, "status_details")
            self._fail_pending(AgentError(f"Voice agent response failed: {details}"))
            return

        output = event_value(response, "output") or []
        calls = [item for item in output if event_type(item) == "function_call"]
        if not texts:
            texts = self._message_text(output)
        text = " ".join(texts).strip()
        if text:
            self._on_turn("agent", text, None)
            self._reply_parts.append(text)

        if calls and status == "completed":
            self._tool_rounds += 1
            if self._tool_rounds > MAX_TOOL_ROUNDS:
                self._fail_pending(AgentError("The voice agent kept calling tools without replying"))
                return
            for call in calls:
                await self._send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": event_value(call, "call_id"),
                            "output": self._run_tool(call),
                        },
                    }
                )
            await self._conn.response.create()
            return

        reply = " ".join(self._reply_parts).strip()
        self._reply_parts = []
        self._tool_rounds = 0  # a final reply ends the tool chain (voice has no request_reply)
        if reply:
            self._last_reply = reply
        self._emit({"type": "response_done"})
        if self._pending is None or self._pending.done():
            return
        if self._create_after_done:
            # Our request was rejected while the agent was busy; ask again now.
            self._create_after_done = False
            await self._conn.response.create()
            return
        if reply:
            self._pending.set_result(reply)
            return
        # No text: typically the response was cancelled because the microphone
        # picked up speech, echo or noise (barge-in). Wait for the next response
        # instead of returning an empty reply; ask again if none starts.
        details = event_value(response, "status_details")
        reason = event_value(details, "reason") or details
        if status in ("cancelled", "incomplete") and self._empty_retries < MAX_EMPTY_RETRIES:
            self._empty_retries += 1
            logger.info("Voice agent response %s without text (%s); waiting for the next one", status, reason)
            self._schedule_retry()
            return
        suffix = f": {reason}" if reason else ""
        self._fail_pending(
            AgentError(f"The voice agent returned an empty reply (response {status}{suffix})")
        )

    def _schedule_retry(self) -> None:
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_if_idle(), name="voice-agent-retry")

    async def _retry_if_idle(self) -> None:
        await asyncio.sleep(EMPTY_REPLY_RETRY_SECONDS)
        pending = self._pending
        # Another response started (e.g. the agent answering the speech) or
        # the request is already answered: nothing to do.
        if pending is None or pending.done() or not self._idle.is_set() or not self.is_open:
            return
        try:
            await self._conn.response.create()
        except Exception as exc:
            self._fail_pending(AgentError(f"Could not ask the voice agent again: {exc}"))

    def _run_tool(self, call: Any) -> str:
        name = str(event_value(call, "name") or "")
        arguments = event_value(call, "arguments") or "{}"
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        try:
            return self._on_tool_call(name, arguments)
        except Exception as exc:  # never let a tool crash the pump
            logger.exception("Tool %s failed", name)
            return json.dumps({"recorded": False, "error": f"Tool failed: {exc}"})

    @staticmethod
    def _message_text(output: list[Any]) -> list[str]:
        texts = []
        for item in output:
            if event_type(item) != "message":
                continue
            for part in event_value(item, "content") or []:
                text = event_value(part, "transcript") or event_value(part, "text")
                if text:
                    texts.append(str(text).strip())
        return texts

    @staticmethod
    def _is_active_response_error(error: Any, message: str) -> bool:
        code = str(event_value(error, "code") or "")
        return code == "conversation_already_has_active_response" or (
            "already has an active response" in message.lower()
        )

    def _fail_pending(self, error: Exception) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(error)

    async def _send(self, event: dict[str, Any]) -> None:
        try:
            await self._conn.send(event)
        except Exception as exc:
            raise AgentError(f"Could not send to the voice agent: {exc}") from exc


class FoundryAgentConnector:
    """Creates realtime connections to a named voice agent in a Foundry project."""

    def __init__(
        self,
        *,
        project_endpoint: str,
        agent_name: str,
        credential_factory: Callable[[], Any] | None = None,
    ) -> None:
        """`credential_factory` returns an AsyncTokenCredential (default: DefaultAzureCredential)."""
        self._project_endpoint = project_endpoint
        self._agent_name = agent_name
        self._credential_factory = credential_factory
        self._client: Any = None
        self._credential: Any = None

    def connect(self) -> AbstractAsyncContextManager[Any]:
        return self._get_client().voice_agents.realtime.connect(agent_name=self._agent_name)

    def _get_client(self) -> Any:
        # Created lazily inside the running event loop (aiohttp needs one).
        if self._client is None:
            import aiohttp
            from azure.ai.projects.aio import AIProjectClient
            from azure.core.pipeline.transport import AioHttpTransport
            if self._credential_factory is not None:
                self._credential = self._credential_factory()
            else:
                from azure.identity.aio import DefaultAzureCredential

                self._credential = DefaultAzureCredential()
            transport = AioHttpTransport(
                session=aiohttp.ClientSession(
                    auto_decompress=False,
                    headers={"Accept-Encoding": "gzip, deflate"},
                )
            )
            self._client = AIProjectClient(
                endpoint=self._project_endpoint,
                credential=self._credential,
                allow_preview=True,
                transport=transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
        if self._credential is not None:
            with contextlib.suppress(Exception):
                await self._credential.close()
        self._client = self._credential = None
