"""Coordinates the customer queue, session metadata and transcript storage."""

import asyncio
import bisect
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.schemas.customer import Customer
from app.schemas.session import (
    FinalizeSessionRequest,
    OutcomeProposal,
    QueueState,
    ReviewFlag,
    SessionOutcome,
    SessionRecord,
    Speaker,
    TranscriptDocument,
    TranscriptTurn,
)
from app.services.customer_queue import CustomerQueue, QueueError
from app.services.transcript_storage import TranscriptStorage

logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    pass


class SessionNotActiveError(Exception):
    """The session has already been finalized."""


class TranscriptSaveError(Exception):
    """Saving the transcript failed; the session stays active so it can be retried."""


class FinalizeInProgressError(Exception):
    """Another End Session request for this session is still saving the transcript."""


class TranscriptNotSavedError(Exception):
    """The session has no saved transcript yet (it is still active)."""


class TranscriptLoadError(Exception):
    """The saved transcript could not be read back from storage."""


class SessionStartError(Exception):
    """Recording the new session (e.g. in the database) failed; nothing was started."""


@dataclass(frozen=True)
class FinalizeResult:
    session: SessionRecord
    advanced: bool
    already_finalized: bool


@dataclass(frozen=True)
class ConversationSnapshot:
    session: SessionRecord
    customer: Customer
    turns: tuple[TranscriptTurn, ...]
    proposal: OutcomeProposal | None
    flags: tuple[ReviewFlag, ...] = ()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_session_id() -> str:
    return f"session_{uuid.uuid4().hex}"


class SessionManager:
    """In-memory session metadata for the PoC (one operator, one process)."""

    def __init__(
        self,
        customers: Sequence[Customer],
        storage: TranscriptStorage,
        *,
        clock: Callable[[], datetime] = _utcnow,
        id_factory: Callable[[], str] = _new_session_id,
        on_session_start: Callable[[SessionRecord], Awaitable[None]] | None = None,
        allow_reset: bool = True,
        on_turns_changed: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        # Cheap async notification after a turn is added (live persistence).
        self.on_turns_changed = on_turns_changed
        # on_session_start persists a new session before it is handed out
        # (database I/O); if it raises, the session is not started.
        self._on_session_start = on_session_start
        # The database-backed queue cannot be replayed from the start.
        self._allow_reset = allow_reset
        self._customers = tuple(customers)
        self._customers_by_id = {c.customer_id: c for c in self._customers}
        self._queue = CustomerQueue(self._customers)
        self._storage = storage
        self._clock = clock
        self._id_factory = id_factory
        self._sessions: dict[str, SessionRecord] = {}
        # Turns captured server-side (typed conversation via the backend model).
        self._turns: dict[str, list[TranscriptTurn]] = {}
        # Latest validated outcome proposed by the model, per session.
        self._proposals: dict[str, OutcomeProposal] = {}
        # Heuristic screening flags raised during the conversation.
        self._flags: dict[str, list[ReviewFlag]] = {}
        # Sessions whose transcript upload is in flight.
        self._finalizing: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def customers(self) -> tuple[Customer, ...]:
        return self._customers

    def now(self) -> datetime:
        return self._clock()

    async def queue_state(self) -> QueueState:
        async with self._lock:
            return self._queue_state()

    async def list_sessions(self) -> list[SessionRecord]:
        async with self._lock:
            return list(self._sessions.values())

    async def start_session(
        self, expected_customer_id: str | None = None
    ) -> SessionRecord:
        """Start a session for the current customer.

        The queue slot is claimed under the lock, the session is persisted via
        `on_session_start` outside it (database I/O), and only then is the
        session registered. If persisting fails the slot is released, the
        queue does not move, and SessionStartError is raised.
        """
        async with self._lock:
            session_id = self._id_factory()
            index = self._queue.current_index
            customer = self._queue.begin(session_id, expected_customer_id)
            record = SessionRecord(
                session_id=session_id,
                customer_id=customer.customer_id,
                invoice_id=customer.invoice.invoice_id,
                sequence_index=index,
                status="active",
                started_at=self._clock(),
            )

        if self._on_session_start is not None:
            try:
                await self._on_session_start(record)
            except Exception as exc:
                async with self._lock:
                    self._queue.finish(session_id, advance=False)
                raise SessionStartError(str(exc)) from exc

        async with self._lock:
            self._sessions[session_id] = record
            self._turns[session_id] = []
            return record

    # --- Conversation state -------------------------------------------------

    async def get_conversation(self, session_id: str) -> ConversationSnapshot:
        async with self._lock:
            record = self._active_record(session_id)
            return ConversationSnapshot(
                session=record,
                customer=self._customers_by_id[record.customer_id],
                turns=tuple(self._turns.get(session_id, ())),
                proposal=self._proposals.get(session_id),
                flags=tuple(self._flags.get(session_id, ())),
            )

    async def add_review_flags(self, session_id: str, flags: list[ReviewFlag]) -> None:
        if not flags:
            return
        async with self._lock:
            self._active_record(session_id)
            self._flags.setdefault(session_id, []).extend(flags)

    async def append_turn(
        self,
        session_id: str,
        speaker: Speaker,
        text: str,
        at: datetime | None = None,
    ) -> TranscriptTurn:
        """Add a turn in timestamp order and renumber.

        `at` lets late-arriving turns land in the right place, e.g. a user's
        speech transcription that completes after the agent started replying
        is placed at the moment the user stopped speaking.
        """
        async with self._lock:
            self._active_record(session_id)
            turns = self._turns.setdefault(session_id, [])
            timestamp = at or self._clock()
            index = bisect.bisect_right([t.timestamp for t in turns], timestamp)
            turns.insert(
                index,
                TranscriptTurn(
                    turn=index + 1, speaker=speaker, text=text, timestamp=timestamp
                ),
            )
            for number in range(index, len(turns)):
                turns[number] = turns[number].model_copy(update={"turn": number + 1})
            added = turns[index]
        if self.on_turns_changed is not None:
            try:
                await self.on_turns_changed(session_id)
            except Exception:  # live persistence must never break the conversation
                logger.exception("Turn change hook failed for session %s", session_id)
        return added

    async def set_proposed_outcome(
        self, session_id: str, proposal: OutcomeProposal
    ) -> None:
        async with self._lock:
            self._active_record(session_id)
            self._proposals[session_id] = proposal

    # --- Finalization ---------------------------------------------------------

    async def finalize_session(
        self, session_id: str, request: FinalizeSessionRequest
    ) -> FinalizeResult:
        """Save the transcript, then mark the session final and advance the queue.

        Three phases so a slow storage upload never holds the lock:
          1. (locked) claim the session as finalizing and build the document;
          2. (unlocked) upload the transcript;
          3. (locked) commit, or release the claim if the upload failed.
        While finalizing, the session accepts no new turns and a concurrent
        finalize gets FinalizeInProgressError.
        """
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            if record.status != "active":
                # Duplicate end event: return the stored result, never advance again.
                return FinalizeResult(record, advanced=False, already_finalized=True)
            if session_id in self._finalizing:
                raise FinalizeInProgressError(session_id)
            self._finalizing.add(session_id)
            try:
                document = self._build_document(record, request)
            except Exception:
                self._finalizing.discard(session_id)
                raise

        # The queue only advances after the transcript is safely stored.
        try:
            reference = await self._storage.save(document)
        except Exception as exc:
            async with self._lock:
                self._finalizing.discard(session_id)
            raise TranscriptSaveError(str(exc)) from exc

        async with self._lock:
            self._finalizing.discard(session_id)
            updated = record.model_copy(
                update={
                    "status": request.session_status,
                    "ended_at": document.ended_at,
                    "outcome": document.outcome,
                    "promised_payment_date": document.promised_payment_date,
                    "transcript_ref": reference,
                    "transcript_store": self._storage.kind,
                    "review_flag_count": len(document.review_flags),
                }
            )
            self._sessions[session_id] = updated
            # A failed session releases the slot but keeps the same customer
            # current, so the record is retried rather than skipped.
            advanced = self._queue.finish(
                session_id, advance=request.session_status == "completed"
            )
            return FinalizeResult(updated, advanced=advanced, already_finalized=False)

    async def get_session(self, session_id: str) -> SessionRecord:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            return record

    async def load_transcript(self, session_id: str) -> TranscriptDocument:
        """Read a finalized session's transcript back from storage."""
        record = await self.get_session(session_id)
        if record.transcript_ref is None:
            raise TranscriptNotSavedError(session_id)
        try:
            document = await self._storage.load(record.transcript_ref)
        except Exception as exc:
            raise TranscriptLoadError(str(exc)) from exc
        if document is None:
            raise TranscriptLoadError(
                f"Transcript {record.transcript_ref} was not found in storage"
            )
        return document

    @property
    def storage_kind(self) -> str:
        return self._storage.kind

    def _build_document(
        self, record: SessionRecord, request: FinalizeSessionRequest
    ) -> TranscriptDocument:
        """Build the transcript document to persist (caller holds the lock)."""
        session_id = record.session_id
        outcome, promised, summary = self._resolve_outcome(session_id, request)
        # Turns captured by the backend are authoritative; otherwise use the
        # turns the client captured.
        turns = self._turns.get(session_id) or request.transcript
        return TranscriptDocument(
            session_id=record.session_id,
            customer_id=record.customer_id,
            invoice_id=record.invoice_id,
            started_at=record.started_at,
            ended_at=self._clock(),
            session_status=request.session_status,
            outcome=outcome,
            promised_payment_date=promised,
            transcript=[
                turn.model_copy(update={"turn": number})
                for number, turn in enumerate(turns, start=1)
            ],
            summary=summary,
            review_flags=list(self._flags.get(session_id, ())),
        )

    async def requeue_customer(self, customer: Customer) -> bool:
        """Add a customer to the end of the queue (follow-up call, after everyone else).

        Returns False, and changes nothing, when the customer is already waiting.
        """
        async with self._lock:
            if self._queue.is_pending(customer.customer_id):
                return False
            self._customers_by_id[customer.customer_id] = customer
            self._queue.append(customer)
            return True

    async def customer(self, customer_id: str) -> Customer | None:
        async with self._lock:
            return self._customers_by_id.get(customer_id)

    async def reset_queue(self) -> QueueState:
        if not self._allow_reset:
            raise QueueError(
                "Reset is not available for the database queue; finished call tasks "
                "are not called again"
            )
        async with self._lock:
            self._queue.reset()
            return self._queue_state()

    # --- Internals ------------------------------------------------------------

    def _active_record(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if record.status != "active" or session_id in self._finalizing:
            raise SessionNotActiveError(session_id)
        return record

    def _resolve_outcome(self, session_id: str, request: FinalizeSessionRequest):
        """Explicit request outcome > validated model proposal > default."""
        proposal = self._proposals.get(session_id)
        summary = request.summary or (proposal.summary if proposal else None)
        if request.session_status == "failed":
            return SessionOutcome.TECHNICAL_FAILURE, None, summary
        if request.outcome is not None:
            return request.outcome, request.promised_payment_date, summary
        if proposal is not None:
            return proposal.outcome, proposal.promised_payment_date, summary
        return SessionOutcome.COMPLETED_NO_COMMITMENT, None, summary

    def _queue_state(self) -> QueueState:
        q = self._queue
        return QueueState(
            total=q.total,
            current_index=q.current_index,
            position=None if q.is_complete else q.current_index + 1,
            remaining=q.remaining,
            completed=q.is_complete,
            current_customer=q.current_customer,
            active_session_id=q.active_session_id,
        )
