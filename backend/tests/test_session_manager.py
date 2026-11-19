from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.session import FinalizeSessionRequest, SessionOutcome, TranscriptDocument
from app.services.session_manager import (
    FinalizeInProgressError,
    SessionManager,
    SessionNotActiveError,
    SessionNotFoundError,
    TranscriptLoadError,
    TranscriptNotSavedError,
    TranscriptSaveError,
)
from app.services.transcript_storage import InMemoryTranscriptStorage

from .conftest import SAMPLE_ORDER


class FlakyStorage(InMemoryTranscriptStorage):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def save(self, document: TranscriptDocument) -> str:
        if self.failures > 0:
            self.failures -= 1
            raise OSError("storage unavailable")
        return super().save(document)


def completed(**kwargs) -> FinalizeSessionRequest:
    return FinalizeSessionRequest(session_status="completed", **kwargs)


def test_finalize_saves_transcript_and_advances(sample_customers) -> None:
    storage = InMemoryTranscriptStorage()
    manager = SessionManager(sample_customers, storage)
    session = manager.start_session()
    turn_time = datetime(2026, 11, 16, 10, 0, 5, tzinfo=UTC)

    result = manager.finalize_session(
        session.session_id,
        completed(
            outcome=SessionOutcome.PAYMENT_PROMISED,
            promised_payment_date="2026-11-20",
            transcript=[
                {"turn": 7, "speaker": "agent", "text": "Hello", "timestamp": turn_time},
                {"speaker": "user", "text": "Friday", "timestamp": turn_time},
            ],
        ),
    )

    assert result.advanced and not result.already_finalized
    assert result.session.status == "completed"
    doc = storage.load(result.session.transcript_ref)
    assert doc.customer_id == SAMPLE_ORDER[0]
    assert [t.turn for t in doc.transcript] == [1, 2]
    assert [t.speaker for t in doc.transcript] == ["agent", "user"]
    assert manager.queue_state().current_customer.customer_id == SAMPLE_ORDER[1]


def test_storage_failure_does_not_advance_and_can_retry(sample_customers) -> None:
    manager = SessionManager(sample_customers, FlakyStorage(failures=1))
    session = manager.start_session()

    with pytest.raises(TranscriptSaveError):
        manager.finalize_session(session.session_id, completed())
    state = manager.queue_state()
    assert state.current_index == 0
    assert state.active_session_id == session.session_id

    result = manager.finalize_session(session.session_id, completed())
    assert result.advanced
    assert manager.queue_state().current_index == 1


def test_default_outcomes(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    s1 = manager.start_session()
    r1 = manager.finalize_session(s1.session_id, FinalizeSessionRequest(session_status="failed"))
    assert r1.session.outcome == SessionOutcome.TECHNICAL_FAILURE
    assert not r1.advanced

    s2 = manager.start_session()
    assert s2.customer_id == SAMPLE_ORDER[0]  # failed session is retried, not skipped
    r2 = manager.finalize_session(s2.session_id, completed())
    assert r2.session.outcome == SessionOutcome.COMPLETED_NO_COMMITMENT


def test_append_turn_orders_by_timestamp_and_renumbers(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    sid = manager.start_session().session_id
    t0 = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)

    manager.append_turn(sid, "agent", "Hello", at=t0)
    manager.append_turn(sid, "agent", "Noted.", at=t0 + timedelta(seconds=10))
    # User speech whose transcription arrived late, placed at its commit time.
    inserted = manager.append_turn(sid, "user", "I will pay Friday", at=t0 + timedelta(seconds=5))

    turns = manager.get_conversation(sid).turns
    assert [(t.turn, t.speaker, t.text) for t in turns] == [
        (1, "agent", "Hello"),
        (2, "user", "I will pay Friday"),
        (3, "agent", "Noted."),
    ]
    assert inserted.turn == 2


class ReentrantStorage(InMemoryTranscriptStorage):
    """Probes the manager from inside save(), as a concurrent request would."""

    def __init__(self) -> None:
        super().__init__()
        self.manager: SessionManager | None = None
        self.observed: dict[str, object] = {}

    def save(self, document: TranscriptDocument) -> str:
        sid = document.session_id
        try:
            self.manager.append_turn(sid, "user", "late words")
        except SessionNotActiveError:
            self.observed["append_blocked"] = True
        try:
            self.manager.finalize_session(sid, completed())
        except FinalizeInProgressError:
            self.observed["second_finalize_blocked"] = True
        self.observed["queue_readable"] = self.manager.queue_state().position  # lock not held
        return super().save(document)


def test_finalize_upload_runs_without_lock_and_blocks_concurrent_changes(sample_customers) -> None:
    storage = ReentrantStorage()
    manager = SessionManager(sample_customers, storage)
    storage.manager = manager
    sid = manager.start_session().session_id

    result = manager.finalize_session(sid, completed())

    assert result.advanced
    assert storage.observed == {
        "append_blocked": True,
        "second_finalize_blocked": True,
        "queue_readable": 1,
    }
    assert result.session.transcript_store == "memory"


def test_failed_upload_releases_finalizing_claim(sample_customers) -> None:
    manager = SessionManager(sample_customers, FlakyStorage(failures=1))
    sid = manager.start_session().session_id
    with pytest.raises(TranscriptSaveError):
        manager.finalize_session(sid, completed())
    # Session is usable again: turns can be added and finalize can be retried.
    manager.append_turn(sid, "user", "still here")
    assert manager.finalize_session(sid, completed()).advanced


def test_load_transcript(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    sid = manager.start_session().session_id
    with pytest.raises(TranscriptNotSavedError):
        manager.load_transcript(sid)
    manager.append_turn(sid, "agent", "Hello")
    manager.finalize_session(sid, completed())
    assert [t.text for t in manager.load_transcript(sid).transcript] == ["Hello"]


def test_load_transcript_storage_error(sample_customers) -> None:
    class BrokenRead(InMemoryTranscriptStorage):
        def load(self, reference):
            raise ConnectionError("blob service down")

    manager = SessionManager(sample_customers, BrokenRead())
    sid = manager.start_session().session_id
    manager.finalize_session(sid, completed())
    with pytest.raises(TranscriptLoadError, match="blob service down"):
        manager.load_transcript(sid)


def test_unknown_session(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    with pytest.raises(SessionNotFoundError):
        manager.finalize_session("nope", completed())
