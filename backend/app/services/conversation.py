"""Conversation with the Foundry voice agent for an active app session.

One live agent connection per app session: opened when the browser attaches
voice (or sends the first typed message), reused for every turn, and closed
when the session is finalized. If the connection drops, the next attach or
message reconnects and replays the conversation so far in the session context.

Voice (M3): one browser WebSocket ("sink") per session receives the agent's
audio, speech events and transcript snapshots; microphone audio is streamed in
with `append_audio`. Typed messages keep working during a voice session.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.schemas.session import OutcomeProposal, ReviewFlag, TranscriptTurn
from app.services.agent_prompt import RECORD_OUTCOME_TOOL_NAME, build_session_context
from app.services.outcome_validation import validate_outcome_arguments
from app.services.response_checks import (
    check_agent_text,
    extract_amounts,
    extract_dates,
    redact_user_text,
)
from app.services.session_manager import (
    ConversationSnapshot,
    SessionManager,
    SessionNotActiveError,
)
from app.services.voice_agent import AgentConnector, AgentError, LiveAgentSession

logger = logging.getLogger(__name__)

# Matches the TranscriptTurn.text limit.
MAX_TURN_CHARS = 5000

# Receives agent audio (bytes) and JSON-able UI events (dicts) for one browser.
VoiceSink = Callable[[Any], None]


class AgentNotConfiguredError(Exception):
    pass


class AgentBusyError(Exception):
    """The agent is still answering the previous message for this session."""


class ConversationStateError(Exception):
    """The request does not fit the conversation state (e.g. second opening)."""


@dataclass(frozen=True)
class ChatResult:
    agent_turn: TranscriptTurn
    transcript: tuple[TranscriptTurn, ...]
    proposal: OutcomeProposal | None
    flags: tuple[ReviewFlag, ...] = ()


class ConversationService:
    def __init__(
        self,
        manager: SessionManager,
        connector: AgentConnector | None,
        *,
        organization: str,
        today: Callable[[], date],
        timeout_seconds: float = 45.0,
    ) -> None:
        self._manager = manager
        self._connector = connector
        self._organization = organization
        self._today = today
        self._timeout = timeout_seconds
        self._live: dict[str, LiveAgentSession] = {}
        self._busy: set[str] = set()
        self._sinks: dict[str, VoiceSink] = {}

    @property
    def configured(self) -> bool:
        return self._connector is not None

    async def send(self, session_id: str, text: str | None) -> ChatResult:
        """Add the user's message (or request the opening line) and get the agent reply."""
        self._require_connector()
        snapshot = self._manager.get_conversation(session_id)  # 404 / 409 if not active
        if text is None and snapshot.turns:
            raise ConversationStateError("The conversation has already started; send a message")

        # One in-flight request per session; the event loop makes check+add atomic.
        if session_id in self._busy:
            raise AgentBusyError("The agent is still responding to the previous message")
        self._busy.add(session_id)
        try:
            live = await self._ensure_live(session_id, snapshot)
            if text is not None:
                # Card numbers/codes are removed before storing and before the agent sees them.
                text = self._record_turn(session_id, "user", text, None)
            try:
                reply = await live.request_reply(text)
            except AgentError:
                if not live.is_open:
                    await self._drop(session_id)
                raise
            if not reply:
                raise AgentError("The voice agent returned an empty reply")

            final = self._manager.get_conversation(session_id)
            agent_turn = next(t for t in reversed(final.turns) if t.speaker == "agent")
            return ChatResult(agent_turn, final.turns, final.proposal, final.flags)
        finally:
            self._busy.discard(session_id)

    # --- Voice (M3) --------------------------------------------------------------

    async def attach_voice(self, session_id: str, sink: VoiceSink) -> None:
        """Connect a browser to the session's live agent and start the opening if needed.

        A newer browser connection replaces an older one (e.g. after a reload).
        """
        self._require_connector()
        snapshot = self._manager.get_conversation(session_id)  # 404 / 409 if not active
        await self._ensure_live(session_id, snapshot)
        self._sinks[session_id] = sink
        sink({"type": "ready", **self._transcript_payload(session_id)})
        if not snapshot.turns and session_id not in self._busy:
            await self._live[session_id].create_response()  # agent speaks first

    def detach_voice(self, session_id: str, sink: VoiceSink) -> None:
        if self._sinks.get(session_id) is sink:
            del self._sinks[session_id]

    async def append_audio(self, session_id: str, pcm16: bytes) -> None:
        live = self._live.get(session_id)
        if live is None or not live.is_open:
            raise AgentError("The voice agent session is not connected")
        await live.append_audio(pcm16)

    # --- Lifecycle ------------------------------------------------------------

    async def close(self, session_id: str) -> None:
        self._emit(session_id, {"type": "session_ended"})
        await self._drop(session_id)

    async def close_all(self) -> None:
        for session_id in list(self._live):
            await self._drop(session_id)

    # --- Internals ------------------------------------------------------------

    def _require_connector(self) -> None:
        if self._connector is None:
            raise AgentNotConfiguredError(
                "The Foundry voice agent is not configured (set AZURE_AI_PROJECT_ENDPOINT "
                "and AZURE_AI_AGENT_NAME)"
            )

    def _emit(self, session_id: str, event: Any) -> None:
        sink = self._sinks.get(session_id)
        if sink is not None:
            sink(event)

    def _transcript_payload(self, session_id: str) -> dict[str, Any]:
        snapshot = self._manager.get_conversation(session_id)
        return {
            "transcript": [t.model_dump(mode="json") for t in snapshot.turns],
            "proposed_outcome": snapshot.proposal.model_dump(mode="json") if snapshot.proposal else None,
            "review_flags": [f.model_dump(mode="json") for f in snapshot.flags],
        }

    def _emit_transcript(self, session_id: str) -> None:
        if session_id not in self._sinks:
            return
        try:
            self._emit(session_id, {"type": "transcript", **self._transcript_payload(session_id)})
        except SessionNotActiveError:
            pass

    async def _ensure_live(self, session_id: str, snapshot: ConversationSnapshot) -> LiveAgentSession:
        live = self._live.get(session_id)
        if live is not None and live.is_open:
            return live
        if live is not None:
            await self._drop(session_id)

        assert self._connector is not None
        live = LiveAgentSession(
            self._connector.connect,
            context=build_session_context(
                customer=snapshot.customer,
                today=self._today(),
                organization=self._organization,
                history=snapshot.turns,
            ),
            on_turn=lambda speaker, text, at: self._record_turn(session_id, speaker, text, at),
            on_tool_call=lambda name, args: self._handle_tool_call(session_id, name, args),
            on_event=lambda event: self._emit(session_id, event),
            timeout_seconds=self._timeout,
        )
        await live.start()
        logger.info(
            "Voice agent session started for %s (conversation %s, %s)",
            session_id,
            live.conversation_id,
            live.session_info,
        )
        self._live[session_id] = live
        return live

    async def _drop(self, session_id: str) -> None:
        live = self._live.pop(session_id, None)
        if live is not None:
            await live.close()

    def _record_turn(
        self, session_id: str, speaker: str, text: str, at: datetime | None
    ) -> str:
        """Screen, store and broadcast a turn. Returns the stored (possibly redacted) text."""
        text = text[:MAX_TURN_CHARS]
        try:
            if speaker == "user":
                text, flags = redact_user_text(text)
            else:
                flags = self._screen_agent_text(session_id, text)
            self._manager.append_turn(session_id, speaker, text, at)  # type: ignore[arg-type]
            self._manager.add_review_flags(session_id, flags)
        except SessionNotActiveError:
            logger.info("Ignoring %s turn for ended session %s", speaker, session_id)
            return text
        for flag in flags:
            # Log the kind only; the excerpt may contain customer data.
            logger.warning("Review flag on session %s: %s", session_id, flag.kind)
        self._emit_transcript(session_id)
        return text

    def _screen_agent_text(self, session_id: str, text: str) -> list[ReviewFlag]:
        snapshot = self._manager.get_conversation(session_id)
        today = self._today()
        user_text = " ".join(t.text for t in snapshot.turns if t.speaker == "user")
        allowed_dates = set(extract_dates(user_text, today))
        if snapshot.proposal and snapshot.proposal.promised_payment_date:
            allowed_dates.add(snapshot.proposal.promised_payment_date)
        return check_agent_text(
            text,
            customer=snapshot.customer,
            today=today,
            allowed_amounts=extract_amounts(user_text),
            allowed_dates=allowed_dates,
        )

    def _handle_tool_call(self, session_id: str, name: str, arguments: str) -> str:
        if name != RECORD_OUTCOME_TOOL_NAME:
            return json.dumps({"recorded": False, "error": f"Unknown tool {name}"})
        result = validate_outcome_arguments(
            arguments, today=self._today(), now=self._manager.now()
        )
        if not result.ok:
            return json.dumps({"recorded": False, "error": result.error})
        try:
            self._manager.set_proposed_outcome(session_id, result.proposal)
        except SessionNotActiveError:
            return json.dumps({"recorded": False, "error": "The session has already ended"})
        self._emit_transcript(session_id)
        return json.dumps({"recorded": True})
