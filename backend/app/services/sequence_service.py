import asyncio
import uuid
from datetime import UTC, datetime

from app.schemas.customer import Customer
from app.schemas.session import QueueState, SessionRecord, SessionStatus
from app.schemas.transcript import TranscriptTurn


class QueueCompletedError(RuntimeError):
    """Every customer has already been processed."""


class SessionActiveError(RuntimeError):
    """A session is already running; only one is allowed at a time."""


class SessionNotFoundError(KeyError):
    """No session with the given id exists."""


class StreamUnavailableError(RuntimeError):
    """The session is not active or already has a live voice stream."""


class SequenceService:
    """Sequential, in-memory customer queue.

    The index advances exactly once per finalized session; repeated finalize
    calls for the same session are no-ops (idempotent).
    """

    def __init__(self, customers: list[Customer]) -> None:
        self._customers = customers
        self._index = 0
        self._sessions: dict[str, SessionRecord] = {}
        self._active_id: str | None = None
        self._streaming: set[str] = set()
        self._lock = asyncio.Lock()

    def _state(self) -> QueueState:
        total = len(self._customers)
        done = self._index >= total
        return QueueState(
            total=total,
            current_index=self._index,
            current_position=None if done else self._index + 1,
            remaining=max(total - self._index, 0),
            all_completed=done,
            current_customer=None if done else self._customers[self._index],
            active_session_id=self._active_id,
        )

    async def state(self) -> QueueState:
        async with self._lock:
            return self._state()

    async def list_customers(self) -> list[Customer]:
        return list(self._customers)

    async def start_session(self) -> SessionRecord:
        async with self._lock:
            if self._index >= len(self._customers):
                raise QueueCompletedError
            if self._active_id is not None:
                raise SessionActiveError
            customer = self._customers[self._index]
            record = SessionRecord(
                session_id=f"session_{uuid.uuid4().hex}",
                customer_id=customer.customer_id,
                invoice_id=customer.invoice.invoice_id,
                status=SessionStatus.ACTIVE,
                started_at=datetime.now(UTC),
            )
            self._sessions[record.session_id] = record
            self._active_id = record.session_id
            return record

    async def finalize_session(self, session_id: str) -> QueueState:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            if record.status is SessionStatus.ACTIVE:
                record.status = SessionStatus.COMPLETED
                record.ended_at = datetime.now(UTC)
                self._active_id = None
                self._index += 1
            return self._state()

    async def open_stream(self, session_id: str) -> Customer:
        """Claim the single live voice stream of an active session; return its customer."""
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            if record.status is not SessionStatus.ACTIVE or session_id in self._streaming:
                raise StreamUnavailableError(session_id)
            self._streaming.add(session_id)
            return next(c for c in self._customers if c.customer_id == record.customer_id)

    async def close_stream(
        self, session_id: str, turns: list[TranscriptTurn], conversation_id: str | None
    ) -> None:
        """Release the stream and keep whatever transcript was captured (also on failure)."""
        async with self._lock:
            self._streaming.discard(session_id)
            record = self._sessions.get(session_id)
            if record is not None:
                record.transcript = list(turns)
                record.conversation_id = conversation_id

    async def get_session(self, session_id: str) -> SessionRecord:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(session_id)
            return record.model_copy(deep=True)
