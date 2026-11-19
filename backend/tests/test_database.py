"""Database mapping and wiring, without a PostgreSQL server.

`FakeStore` stands in for PostgresStore (same public methods); the SQL itself
is exercised by test_database_integration.py against a real server.
"""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.schemas.session import FinalizeSessionRequest, SessionRecord, TranscriptDocument
from app.services.customer_queue import QueueError
from app.services.database import (
    PostgresTranscriptStorage,
    StatusValues,
    customer_from_row,
    organization_currency,
)
from app.services.session_manager import SessionManager, SessionStartError
from app.services.transcript_storage import InMemoryTranscriptStorage

from .conftest import TODAY, FakeConnector, isolated_settings

TS = "2026-11-16T10:00:05Z"
ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")

STATUSES = StatusValues(
    invoice_open={"pending": "unpaid", "overdue": "overdue"},
    task_queued="queued",
    task_in_progress="in_progress",
    task_completed="completed",
    call_in_progress="initiated",
    call_completed="completed",
    call_declined="declined",
    call_failed="failed",
)


def ended(session_status: str, outcome: str) -> TranscriptDocument:
    return TranscriptDocument(
        session_id=str(uuid.uuid4()),
        customer_id="cust_a",
        invoice_id="inv_a",
        started_at=TS,
        ended_at=TS,
        session_status=session_status,
        outcome=outcome,
        transcript=[],
    )


@pytest.mark.parametrize(
    ("session_status", "outcome", "expected"),
    [
        ("completed", "payment_promised", "completed"),
        ("completed", "callback_requested", "completed"),
        ("completed", "stop_requested", "declined"),
        ("failed", "technical_failure", "failed"),
    ],
)
def test_final_call_status(session_status: str, outcome: str, expected: str) -> None:
    assert STATUSES.final_call_status(ended(session_status, outcome)) == expected


def queue_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "task_id": uuid.uuid4(),
        "client_id": uuid.uuid4(),
        "client_name": "Asha Rao",
        "client_phone": "+910000000000",
        "client_language": "Hindi",
        "invoice_id": uuid.uuid4(),
        "invoice_number": "INV-77",
        "amount": Decimal("1234.50"),
        "due_date": date(2026, 11, 1),
        "invoice_status": "overdue",
    }
    row.update(overrides)
    return row


# --- Row mapping ---------------------------------------------------------------------


def test_customer_from_row_maps_existing_columns() -> None:
    row = queue_row()
    customer = customer_from_row(row, statuses=STATUSES, currency="AUD", default_language="English")
    assert customer.customer_id == str(row["client_id"])
    assert customer.customer_name == "Asha Rao"
    assert customer.phone_number == "+910000000000"
    assert customer.preferred_language == "Hindi"
    assert customer.invoice.invoice_id == str(row["invoice_id"])
    assert customer.invoice.invoice_number == "INV-77"
    assert customer.invoice.amount_due == 1234.5
    assert customer.invoice.currency == "AUD"
    assert customer.invoice.due_date == date(2026, 11, 1)
    assert customer.invoice.payment_status == "overdue"


def test_db_invoice_status_is_mapped_to_app_status() -> None:
    customer = customer_from_row(
        queue_row(invoice_status="pending"), statuses=STATUSES, currency="INR", default_language="English"
    )
    assert customer.invoice.payment_status == "unpaid"


def test_missing_language_uses_default() -> None:
    customer = customer_from_row(
        queue_row(client_language=None), statuses=STATUSES, currency="INR", default_language="English"
    )
    assert customer.preferred_language == "English"


def test_invalid_row_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        customer_from_row(queue_row(client_phone=""), statuses=STATUSES, currency="INR", default_language="English")


@pytest.mark.parametrize(
    ("org_settings", "expected"),
    [
        ({"currency": "AUD"}, "AUD"),
        ({"currency": "aud"}, "INR"),
        ({"currency": "DOLLARS"}, "INR"),
        ({}, "INR"),
        (None, "INR"),
    ],
)
def test_organization_currency(org_settings: Any, expected: str) -> None:
    assert organization_currency(org_settings, "INR") == expected


# --- Settings ------------------------------------------------------------------------


def test_database_url_requires_organization() -> None:
    with pytest.raises(ValidationError, match="DATABASE_ORGANIZATION_ID"):
        isolated_settings(database_url="postgresql://u:p@localhost/db", database_organization_id=None)


def test_open_invoice_statuses_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        isolated_settings(database_invoice_status_map={})


def test_database_url_is_secret() -> None:
    settings = isolated_settings(
        database_url="postgresql://u:hunter2@localhost/db", database_organization_id=str(ORG)
    )
    assert settings.database_configured
    assert "hunter2" not in repr(settings)


def test_status_values_from_settings() -> None:
    settings = isolated_settings(database_task_status_completed="done", database_call_status_failed="error")
    statuses = StatusValues.from_settings(settings)
    assert statuses.task_completed == "done"
    assert statuses.call_failed == "error"
    assert statuses.task_queued == "queued"
    assert statuses.call_in_progress == "initiated"
    assert statuses.call_declined == "declined"
    assert statuses.invoice_open == {"pending": "unpaid", "overdue": "overdue", "partially_paid": "partially_paid"}


# --- Session manager hooks ----------------------------------------------------------------


def test_failed_session_start_releases_the_queue_slot(sample_customers) -> None:
    calls: list[SessionRecord] = []

    def flaky_start(record: SessionRecord) -> None:
        calls.append(record)
        if len(calls) == 1:
            raise RuntimeError("database unavailable")

    manager = SessionManager(sample_customers, InMemoryTranscriptStorage(), on_session_start=flaky_start)
    with pytest.raises(SessionStartError, match="database unavailable"):
        manager.start_session()
    state = manager.queue_state()
    assert state.active_session_id is None
    assert state.current_index == 0
    assert manager.list_sessions() == []

    record = manager.start_session()  # retry succeeds, same customer
    assert record.customer_id == sample_customers[0].customer_id
    assert [c.session_id for c in calls] == [calls[0].session_id, record.session_id]


def test_reset_can_be_disabled(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage(), allow_reset=False)
    with pytest.raises(QueueError, match="database queue"):
        manager.reset_queue()


# --- PostgresTranscriptStorage ----------------------------------------------------------


class FakeStore:
    """Duck-typed PostgresStore that records calls in memory."""

    def __init__(self, customers) -> None:
        self.customers = list(customers)
        self.started: list[SessionRecord] = []
        self.saved: dict[str, tuple[TranscriptDocument, str | None]] = {}
        self.recovered = False
        self.closed = False
        self.fail_start = False
        self.fail_save = False

    def recover_interrupted_sessions(self) -> int:
        self.recovered = True
        return 0

    def load_queue(self):
        return list(self.customers)

    def session_started(self, record: SessionRecord) -> None:
        if self.fail_start:
            raise RuntimeError("task changed outside the app")
        self.started.append(record)

    def save_session(self, document: TranscriptDocument, transcript_blob: str | None) -> None:
        if self.fail_save:
            raise RuntimeError("connection lost")
        self.saved[document.session_id] = (document, transcript_blob)

    def load_transcript(self, session_id: str) -> TranscriptDocument | None:
        saved = self.saved.get(session_id)
        return saved[0] if saved else None

    def close(self) -> None:
        self.closed = True


class RecordingBlob(InMemoryTranscriptStorage):
    kind = "azure_blob"

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    def save(self, document: TranscriptDocument) -> str:
        self.order.append("blob")
        return super().save(document)


def test_blob_is_written_before_the_database_commit(sample_customers) -> None:
    order: list[str] = []
    store = FakeStore(sample_customers)
    original = store.save_session

    def save_session(document, transcript_blob):
        order.append("db")
        original(document, transcript_blob)

    store.save_session = save_session  # type: ignore[method-assign]
    storage = PostgresTranscriptStorage(store, RecordingBlob(order))  # type: ignore[arg-type]
    assert storage.kind == "postgres+azure_blob"

    manager = SessionManager(sample_customers, storage, id_factory=lambda: str(uuid.uuid4()))
    record = manager.start_session()

    result = manager.finalize_session(record.session_id, FinalizeSessionRequest(session_status="completed"))
    assert order == ["blob", "db"]
    assert result.session.transcript_ref == record.session_id
    document, blob_name = store.saved[record.session_id]
    assert blob_name and blob_name.endswith(f"/{record.session_id}/transcript.json")
    assert document.customer_id == sample_customers[0].customer_id


# --- App wiring with the database --------------------------------------------------------


@pytest.fixture
def fake_store(sample_customers) -> FakeStore:
    return FakeStore(sample_customers)


@pytest.fixture
def db_client(fake_store: FakeStore) -> Iterator[TestClient]:
    app = create_app(
        isolated_settings(),
        connector=FakeConnector(),
        today=lambda: TODAY,
        database=fake_store,
    )
    with TestClient(app) as test_client:
        yield test_client


def finalize(client: TestClient, session_id: str, **body):
    body.setdefault("session_status", "completed")
    return client.post(f"/api/sessions/{session_id}/transcript", json=body)


def test_database_mode_health_and_queue(db_client: TestClient, fake_store: FakeStore) -> None:
    assert fake_store.recovered
    health = db_client.get("/api/health").json()
    assert health["customer_source"] == "postgres"
    assert health["transcript_storage"] == "postgres"
    customers = db_client.get("/api/customers").json()["customers"]
    assert [c["customer_id"] for c in customers] == [c.customer_id for c in fake_store.customers]


def test_database_mode_session_lifecycle(db_client: TestClient, fake_store: FakeStore) -> None:
    session = db_client.post("/api/sessions", json={}).json()
    sid = session["session_id"]
    assert uuid.UUID(sid)  # calls.id is a uuid
    assert [r.session_id for r in fake_store.started] == [sid]

    res = finalize(
        db_client,
        sid,
        outcome="payment_promised",
        promised_payment_date="2026-11-20",
        transcript=[
            {"speaker": "agent", "text": "Hello", "timestamp": TS},
            {"speaker": "user", "text": "I can pay on Friday.", "timestamp": TS},
        ],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["advanced"] is True
    assert body["session"]["transcript_store"] == "postgres"
    assert body["session"]["transcript_ref"] == sid

    document, blob_name = fake_store.saved[sid]
    assert blob_name is None
    assert document.outcome == "payment_promised"
    assert [t.speaker for t in document.transcript] == ["agent", "user"]

    doc = db_client.get(f"/api/sessions/{sid}/transcript").json()
    assert doc["promised_payment_date"] == "2026-11-20"


def test_database_start_failure_returns_503_and_keeps_queue(db_client: TestClient, fake_store: FakeStore) -> None:
    fake_store.fail_start = True
    res = db_client.post("/api/sessions", json={})
    assert res.status_code == 503
    queue = db_client.get("/api/queue").json()
    assert queue["active_session_id"] is None
    assert queue["position"] == 1


def test_database_save_failure_keeps_session_open(db_client: TestClient, fake_store: FakeStore) -> None:
    sid = db_client.post("/api/sessions", json={}).json()["session_id"]
    fake_store.fail_save = True
    assert finalize(db_client, sid).status_code == 502
    assert db_client.get("/api/queue").json()["active_session_id"] == sid

    fake_store.fail_save = False
    res = finalize(db_client, sid)
    assert res.status_code == 200
    assert res.json()["advanced"] is True


def test_database_queue_cannot_be_reset(db_client: TestClient) -> None:
    assert db_client.post("/api/queue/reset").status_code == 409


def test_empty_database_queue_is_complete(fake_store: FakeStore) -> None:
    fake_store.customers = []
    app = create_app(isolated_settings(), connector=FakeConnector(), database=fake_store)
    with TestClient(app) as client:
        queue = client.get("/api/queue").json()
        assert queue["total"] == 0
        assert queue["completed"] is True
        assert client.post("/api/sessions", json={}).status_code == 409
    assert fake_store.closed  # closed on shutdown
