"""PostgresStore against a real PostgreSQL server.

Skipped unless TEST_DATABASE_URL is set. Each test creates the existing schema
(tests/fixtures/existing_schema.sql) in a throwaway schema and drops it after,
so it never touches real tables. Use a scratch database, not production:

    TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/scratch pytest tests/test_database_integration.py
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.schemas.session import (
    ReviewFlag,
    SessionOutcome,
    SessionRecord,
    TranscriptDocument,
    TranscriptTurn,
)
from app.services.database import DatabaseError, PostgresStore

from .conftest import isolated_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")

SCHEMA_SQL = (Path(__file__).parent / "fixtures" / "existing_schema.sql").read_text(encoding="utf-8")
ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


class Seed:
    """Inserts rows into the throwaway schema and reads them back."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def org(self, org_id: uuid.UUID, settings: str = "{}") -> None:
        self.conn.execute(
            "INSERT INTO organizations (id, name, settings) VALUES (%s, 'Acme', %s::jsonb)",
            (org_id, settings),
        )

    def client(self, name: str, org_id: uuid.UUID = ORG, language: str | None = "English") -> uuid.UUID:
        client_id = uuid.uuid4()
        self.conn.execute(
            """INSERT INTO clients (id, organization_id, external_client_id, name, phone, language)
               VALUES (%s, %s, %s, %s, '+910000000000', %s)""",
            (client_id, org_id, f"ext-{name}", name, language),
        )
        return client_id

    def invoice(
        self, client_id: uuid.UUID, number: str, due: date, status: str = "pending",
        amount: str = "100.00", org_id: uuid.UUID = ORG,
    ) -> uuid.UUID:
        invoice_id = uuid.uuid4()
        self.conn.execute(
            """INSERT INTO invoices (id, organization_id, client_id, external_invoice_id, amount, due_date, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (invoice_id, org_id, client_id, number, amount, due, status),
        )
        return invoice_id

    def task(
        self, client_id: uuid.UUID, scheduled: datetime, status: str = "queued", org_id: uuid.UUID = ORG
    ) -> uuid.UUID:
        task_id = uuid.uuid4()
        self.conn.execute(
            """INSERT INTO call_tasks (id, organization_id, client_id, scheduled_at, status)
               VALUES (%s, %s, %s, %s, %s)""",
            (task_id, org_id, client_id, scheduled, status),
        )
        return task_id

    def value(self, sql: str, *params: Any) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def rows(self, sql: str, *params: Any) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def schema() -> Iterator[tuple[str, Seed]]:
    import psycopg

    name = f"test_pf_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{name}"')
    admin.execute(f'SET search_path TO "{name}"')
    admin.execute(SCHEMA_SQL)
    try:
        yield name, Seed(admin)
    finally:
        admin.execute(f'DROP SCHEMA "{name}" CASCADE')
        admin.close()


@pytest.fixture
def make_store(schema) -> Iterator[Any]:
    name, _ = schema
    stores: list[PostgresStore] = []

    def build(**overrides: Any) -> PostgresStore:
        settings = isolated_settings(
            database_url=TEST_DATABASE_URL,
            database_organization_id=str(ORG),
            database_schema=name,
            **overrides,
        )
        store = PostgresStore.from_settings(settings)
        stores.append(store)
        return store

    yield build
    for store in stores:
        store.close()


def start(store: PostgresStore, customer, index: int = 0) -> SessionRecord:
    record = SessionRecord(
        session_id=str(uuid.uuid4()),
        customer_id=customer.customer_id,
        invoice_id=customer.invoice.invoice_id,
        sequence_index=index,
        status="active",
        started_at=T0,
    )
    store.session_started(record)
    return record


def document(record: SessionRecord, status: str = "completed", **overrides: Any) -> TranscriptDocument:
    values: dict[str, Any] = {
        "session_id": record.session_id,
        "customer_id": record.customer_id,
        "invoice_id": record.invoice_id,
        "started_at": record.started_at,
        "ended_at": record.started_at + timedelta(seconds=95),
        "session_status": status,
        "outcome": SessionOutcome.PAYMENT_PROMISED if status == "completed" else SessionOutcome.TECHNICAL_FAILURE,
        "promised_payment_date": date(2026, 11, 20) if status == "completed" else None,
        "transcript": [
            TranscriptTurn(turn=1, speaker="agent", text="Hello", timestamp=T0 + timedelta(seconds=1)),
            TranscriptTurn(turn=2, speaker="user", text="I can pay Friday", timestamp=T0 + timedelta(seconds=5)),
        ],
        "summary": "Promised Friday",
        **overrides,
    }
    return TranscriptDocument(**values)


def test_load_queue_orders_and_filters(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG, '{"currency": "AUD"}')
    seed.org(OTHER_ORG)

    late = seed.client("Late", language=None)
    seed.invoice(late, "INV-L", date(2026, 11, 5), status="overdue", amount="250.75")
    seed.task(late, T0 + timedelta(hours=2))

    early = seed.client("Early")
    seed.invoice(early, "INV-E2", date(2026, 11, 9))
    first_due = seed.invoice(early, "INV-E1", date(2026, 11, 1))
    seed.invoice(early, "INV-E0", date(2026, 10, 1), status="paid")  # closed: ignored
    seed.task(early, T0 + timedelta(hours=1))
    seed.task(early, T0 + timedelta(hours=3))  # second task for same client: only earliest loads

    no_invoice = seed.client("NoInvoice")
    seed.invoice(no_invoice, "INV-N", date(2026, 11, 1), status="paid")
    seed.task(no_invoice, T0)

    done = seed.client("Done")
    seed.invoice(done, "INV-D", date(2026, 11, 1))
    seed.task(done, T0, status="completed")

    other = seed.client("Other", org_id=OTHER_ORG)
    seed.invoice(other, "INV-O", date(2026, 11, 1), org_id=OTHER_ORG)
    seed.task(other, T0, org_id=OTHER_ORG)

    customers = make_store().load_queue()

    assert [c.customer_name for c in customers] == ["Early", "Late"]
    assert customers[0].invoice.invoice_id == str(first_due)
    assert customers[0].invoice.invoice_number == "INV-E1"
    assert customers[0].invoice.payment_status == "unpaid"
    assert customers[0].invoice.currency == "AUD"
    assert customers[1].invoice.amount_due == 250.75
    assert customers[1].invoice.payment_status == "overdue"
    assert customers[1].preferred_language == "English"


def test_session_lifecycle_writes_existing_tables(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1))
    task_id = seed.task(client_id, T0)

    store = make_store()
    [customer] = store.load_queue()
    record = start(store, customer)

    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "in_progress"
    assert seed.value("SELECT status FROM calls WHERE id = %s", uuid.UUID(record.session_id)) == "initiated"
    assert seed.value("SELECT provider_call_id FROM calls WHERE id = %s", uuid.UUID(record.session_id)) is None

    flag = ReviewFlag(kind="unverified_date", speaker="agent", detail="d", excerpt="e")
    doc = document(record, review_flags=[flag])
    store.save_session(doc, "2026/11/x/y/transcript.json")
    store.save_session(doc, "2026/11/x/y/transcript.json")  # retried save stays idempotent

    call_id = uuid.UUID(record.session_id)
    assert seed.rows("SELECT status, duration FROM calls WHERE id = %s", call_id) == [("completed", 95)]
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "completed"
    assert seed.value("SELECT count(*) FROM call_transcripts WHERE call_id = %s", call_id) == 2
    assert seed.value("SELECT count(*) FROM call_actions WHERE call_id = %s", call_id) == 1
    assert seed.value(
        "SELECT action_status FROM call_actions WHERE call_id = %s", call_id
    ) == "pending"  # column default
    assert seed.value(
        "SELECT count(*) FROM call_events WHERE call_id = %s AND event_type = 'session_ended'", call_id
    ) == 1
    assert seed.value("SELECT count(*) FROM audit_logs WHERE organization_id = %s", ORG) == 3

    loaded = store.load_transcript(record.session_id)
    assert loaded is not None
    assert loaded.model_dump() == doc.model_dump()


def test_failed_session_requeues_the_task(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1))
    task_id = seed.task(client_id, T0)

    store = make_store()
    [customer] = store.load_queue()
    record = start(store, customer)
    store.save_session(document(record, status="failed"), None)

    assert seed.value("SELECT status FROM calls WHERE id = %s", uuid.UUID(record.session_id)) == "failed"
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "queued"
    # The same customer can be started again.
    start(store, customer)


def test_task_changed_outside_the_app_is_not_claimed(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1))
    task_id = seed.task(client_id, T0)

    store = make_store()
    [customer] = store.load_queue()
    seed.conn.execute("UPDATE call_tasks SET status = 'cancelled' WHERE id = %s", (task_id,))

    with pytest.raises(DatabaseError, match="no longer"):
        start(store, customer)
    assert seed.value("SELECT count(*) FROM calls") == 0  # rolled back


def test_interrupted_sessions_are_recovered(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1))
    task_id = seed.task(client_id, T0)

    store = make_store()
    [customer] = store.load_queue()
    record = start(store, customer)  # backend "crashes" here

    # A call created by another application is never touched.
    foreign_call = uuid.uuid4()
    seed.conn.execute(
        "INSERT INTO calls (id, organization_id, client_id, status) VALUES (%s, %s, %s, 'initiated')",
        (foreign_call, ORG, client_id),
    )

    assert make_store().recover_interrupted_sessions() == 1
    assert seed.value("SELECT status FROM calls WHERE id = %s", uuid.UUID(record.session_id)) == "failed"
    assert seed.value("SELECT status FROM calls WHERE id = %s", foreign_call) == "initiated"
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "queued"
    assert seed.value(
        "SELECT result->>'outcome' FROM call_actions WHERE call_id = %s", uuid.UUID(record.session_id)
    ) == "technical_failure"


def test_custom_status_vocabulary(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1), status="open")
    task_id = seed.task(client_id, T0, status="scheduled")

    store = make_store(
        database_invoice_status_map={"open": "unpaid"},
        database_task_status_queued="scheduled",
        database_task_status_in_progress="calling",
        database_task_status_completed="done",
        database_call_status_in_progress="active",
        database_call_status_completed="ended",
    )
    [customer] = store.load_queue()
    record = start(store, customer)
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "calling"
    store.save_session(document(record), None)
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "done"
    assert seed.value("SELECT status FROM calls WHERE id = %s", uuid.UUID(record.session_id)) == "ended"


def test_missing_organization_fails(schema, make_store) -> None:
    with pytest.raises(DatabaseError, match="does not exist"):
        make_store().load_queue()


def test_stop_requested_marks_call_declined(schema, make_store) -> None:
    _, seed = schema
    seed.org(ORG)
    client_id = seed.client("Asha")
    seed.invoice(client_id, "INV-1", date(2026, 11, 1))
    task_id = seed.task(client_id, T0)

    store = make_store()
    [customer] = store.load_queue()
    record = start(store, customer)
    store.save_session(
        document(record, outcome=SessionOutcome.STOP_REQUESTED, promised_payment_date=None), None
    )

    assert seed.value("SELECT status FROM calls WHERE id = %s", uuid.UUID(record.session_id)) == "declined"
    assert seed.value("SELECT status FROM call_tasks WHERE id = %s", task_id) == "completed"
