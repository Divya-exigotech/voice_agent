"""Persistence in the existing PostgreSQL database.

The schema is owned by another application (its Alembic migrations), so this
module only reads and writes rows; it never creates or alters tables. Every
query is scoped to one organization.

Mapping onto the existing tables:

  queue        call_tasks        status = queued, ordered by scheduled_at; one per client
  customer     clients           customer_id = clients.id
  invoice      invoices          the client's open invoice with the earliest due date;
                                 invoice_number = external_invoice_id
  session      calls             calls.id = session_id; provider_call_id is NULL
                                 (a browser session, not a telephony call)
  lifecycle    call_events       session_started / session_ended / session_interrupted
  outcome      call_actions      action_type = record_outcome, result = outcome JSON
  transcript   call_transcripts  one row per turn
  audit        audit_logs        one row per session start / end

Status values written to call_tasks.status and calls.status come from settings
(`StatusValues`) so they can match the owning application's vocabulary.

All methods are blocking; async callers run them in a worker thread.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError

from app.schemas.customer import Customer, Invoice, PaymentStatus
from app.schemas.session import (
    SessionOutcome,
    SessionRecord,
    TranscriptDocument,
    TranscriptTurn,
)

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from app.config import Settings
    from app.services.transcript_storage import BlobTranscriptStorage

logger = logging.getLogger(__name__)

# Tags the rows this app writes, so it only ever recovers its own sessions.
SOURCE = "payment_followup_poc"
AUDIT_ACTOR = "payment-followup-poc"
OUTCOME_ACTION = "record_outcome"
EVENT_STARTED = "session_started"
EVENT_ENDED = "session_ended"
EVENT_INTERRUPTED = "session_interrupted"


class DatabaseError(Exception):
    """The database is unreachable, or its rows are not in the expected state."""


@dataclass(frozen=True)
class StatusValues:
    """Status vocabulary of the application that owns the schema."""

    # invoices.status values that mean money is still owed -> app payment status.
    invoice_open: Mapping[str, PaymentStatus]
    task_queued: str
    task_in_progress: str
    task_completed: str
    call_in_progress: str
    call_completed: str
    call_declined: str
    call_failed: str

    @classmethod
    def from_settings(cls, settings: Settings) -> StatusValues:
        return cls(
            invoice_open=dict(settings.database_invoice_status_map),
            task_queued=settings.database_task_status_queued,
            task_in_progress=settings.database_task_status_in_progress,
            task_completed=settings.database_task_status_completed,
            call_in_progress=settings.database_call_status_in_progress,
            call_completed=settings.database_call_status_completed,
            call_declined=settings.database_call_status_declined,
            call_failed=settings.database_call_status_failed,
        )

    def final_call_status(self, document: TranscriptDocument) -> str:
        """calls.status for a finished session."""
        if document.session_status == "failed":
            return self.call_failed
        if document.outcome == SessionOutcome.STOP_REQUESTED:
            return self.call_declined
        return self.call_completed


# The earliest queued task per client (so customer_id stays unique in the
# queue), with that client's open invoice that is due first. Tasks for clients
# without an open invoice are not loaded. Final order: scheduled_at.
QUEUE_SQL = """
SELECT q.* FROM (
    SELECT DISTINCT ON (t.client_id)
        t.id            AS task_id,
        t.scheduled_at  AS scheduled_at,
        t.created_at    AS task_created_at,
        c.id            AS client_id,
        c.name          AS client_name,
        c.phone         AS client_phone,
        c.language      AS client_language,
        i.id            AS invoice_id,
        i.external_invoice_id AS invoice_number,
        i.amount        AS amount,
        i.due_date      AS due_date,
        i.status        AS invoice_status
    FROM call_tasks t
    JOIN clients c
      ON c.id = t.client_id AND c.organization_id = t.organization_id
    JOIN LATERAL (
        SELECT inv.id, inv.external_invoice_id, inv.amount, inv.due_date, inv.status
        FROM invoices inv
        WHERE inv.client_id = c.id
          AND inv.organization_id = t.organization_id
          AND inv.status = ANY(%(open_statuses)s)
          AND inv.amount > 0
        ORDER BY inv.due_date, inv.created_at, inv.id
        LIMIT 1
    ) i ON TRUE
    WHERE t.organization_id = %(org)s AND t.status = %(queued)s
    ORDER BY t.client_id, t.scheduled_at, t.created_at, t.id
) q
ORDER BY q.scheduled_at, q.task_created_at, q.task_id
"""

QUEUED_CLIENTS_SQL = """
SELECT count(DISTINCT client_id) AS n
FROM call_tasks
WHERE organization_id = %(org)s AND status = %(queued)s
"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def customer_from_row(
    row: Mapping[str, Any],
    *,
    statuses: StatusValues,
    currency: str,
    default_language: str,
) -> Customer:
    """Map one QUEUE_SQL row to the app's Customer model (raises ValidationError)."""
    amount = row["amount"]
    return Customer(
        customer_id=str(row["client_id"]),
        customer_name=row["client_name"],
        phone_number=row["client_phone"],
        preferred_language=row["client_language"] or default_language,
        invoice=Invoice(
            invoice_id=str(row["invoice_id"]),
            invoice_number=row["invoice_number"],
            amount_due=float(amount) if isinstance(amount, Decimal) else amount,
            currency=currency,
            due_date=row["due_date"],
            payment_status=statuses.invoice_open[row["invoice_status"]],
        ),
    )


def organization_currency(org_settings: Any, default: str) -> str:
    """organizations.settings->>'currency' when it is an ISO 4217 code, else `default`."""
    if isinstance(org_settings, Mapping):
        value = org_settings.get("currency")
        if isinstance(value, str) and len(value) == 3 and value.isalpha() and value.isupper():
            return value
    return default


def _as_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError) as exc:
        raise DatabaseError(f"{label} {value!r} is not a UUID") from exc


class PostgresStore:
    """Reads the call queue and records sessions in the existing schema."""

    def __init__(
        self,
        pool: ConnectionPool,
        *,
        organization_id: UUID,
        statuses: StatusValues,
        default_currency: str = "INR",
        default_language: str = "English",
        agent_id: UUID | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._pool = pool
        self._org = organization_id
        self._statuses = statuses
        self._default_currency = default_currency
        self._default_language = default_language
        self._agent_id = agent_id
        self._clock = clock
        # customer_id (clients.id) -> call_tasks.id for the loaded queue.
        self._task_by_customer: dict[str, UUID] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> PostgresStore:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        if settings.database_url is None or settings.database_organization_id is None:
            raise DatabaseError("DATABASE_URL and DATABASE_ORGANIZATION_ID are required")
        pool = ConnectionPool(
            conninfo=settings.database_url.get_secret_value(),
            min_size=1,
            max_size=4,
            timeout=15,
            open=False,
            kwargs={
                "row_factory": dict_row,
                "options": f"-c search_path={settings.database_schema}",
                "application_name": AUDIT_ACTOR,
                "connect_timeout": 10,
            },
        )
        try:
            # Fail fast at startup if the database is unreachable.
            pool.open(wait=True, timeout=20)
        except Exception as exc:
            pool.close()
            raise DatabaseError(f"Cannot connect to PostgreSQL: {exc}") from exc
        return cls(
            pool,
            organization_id=settings.database_organization_id,
            statuses=StatusValues.from_settings(settings),
            default_currency=settings.database_default_currency,
            default_language=settings.database_default_language,
            agent_id=settings.database_agent_id,
        )

    def close(self) -> None:
        self._pool.close()

    # --- Queue ------------------------------------------------------------------

    def load_queue(self) -> list[Customer]:
        """Queued call tasks, in scheduled order, as Customer records."""
        s = self._statuses
        with self._pool.connection() as conn:
            org = conn.execute(
                "SELECT name, settings FROM organizations WHERE id = %s", (self._org,)
            ).fetchone()
            if org is None:
                raise DatabaseError(f"Organization {self._org} does not exist")
            currency = organization_currency(org["settings"], self._default_currency)
            params = {
                "org": self._org,
                "queued": s.task_queued,
                "open_statuses": list(s.invoice_open),
            }
            rows = conn.execute(QUEUE_SQL, params).fetchall()
            queued_clients = conn.execute(QUEUED_CLIENTS_SQL, params).fetchone()["n"]

        customers: list[Customer] = []
        self._task_by_customer = {}
        for row in rows:
            try:
                customer = customer_from_row(
                    row,
                    statuses=s,
                    currency=currency,
                    default_language=self._default_language,
                )
            except ValidationError as exc:
                logger.warning(
                    "Call task %s (client %s, invoice %s) is not loaded; its data is invalid: %s",
                    row["task_id"], row["client_id"], row["invoice_id"], exc,
                )
                continue
            customers.append(customer)
            self._task_by_customer[customer.customer_id] = row["task_id"]

        without_invoice = queued_clients - len(rows)
        if without_invoice:
            logger.warning(
                "%d client(s) with a queued call task have no open invoice (status in %s) "
                "and are not in the queue",
                without_invoice, sorted(s.invoice_open),
            )
        logger.info(
            "Loaded %d queued call task(s) for organization %s (currency %s)",
            len(customers), self._org, currency,
        )
        return customers

    # --- Session lifecycle ---------------------------------------------------------

    def session_started(self, record: SessionRecord) -> None:
        """Claim the call task and create the calls row (one transaction).

        Raises DatabaseError if the task was changed outside this app since the
        queue was loaded, so a task is never worked on twice.
        """
        s = self._statuses
        task_id = self._task_by_customer.get(record.customer_id)
        if task_id is None:
            raise DatabaseError(f"No queued call task is loaded for client {record.customer_id}")
        call_id = _as_uuid(record.session_id, "session_id")
        with self._pool.connection() as conn:
            claimed = conn.execute(
                """
                UPDATE call_tasks SET status = %s, updated_at = %s
                WHERE id = %s AND organization_id = %s AND status = %s
                """,
                (s.task_in_progress, record.started_at, task_id, self._org, s.task_queued),
            ).rowcount
            if claimed != 1:
                raise DatabaseError(
                    f"Call task {task_id} is no longer '{s.task_queued}' (changed outside "
                    "this app); restart the backend to reload the queue"
                )
            conn.execute(
                """
                INSERT INTO calls (id, organization_id, client_id, agent_id, provider_call_id,
                                   status, duration, created_at, updated_at)
                VALUES (%s, %s, %s, %s, NULL, %s, NULL, %s, %s)
                """,
                (
                    call_id, self._org, UUID(record.customer_id), self._agent_id,
                    s.call_in_progress, record.started_at, record.started_at,
                ),
            )
            self._insert_event(
                conn,
                call_id,
                EVENT_STARTED,
                {
                    "source": SOURCE,
                    "channel": "browser",
                    "call_task_id": str(task_id),
                    "invoice_id": record.invoice_id,
                    "sequence_index": record.sequence_index,
                },
                record.started_at,
            )
            self._audit(conn, f"call.session_started call_id={call_id} task_id={task_id}", record.started_at)

    def save_session(self, document: TranscriptDocument, transcript_blob: str | None) -> None:
        """Record the end of a session (one transaction).

        Updates calls, replaces the call's transcript turns and outcome action,
        adds a session_ended event and moves the call task on: completed, or
        back to queued after a failed session so the customer is retried.
        """
        s = self._statuses
        call_id = _as_uuid(document.session_id, "session_id")
        completed = document.session_status == "completed"
        duration = max(0, round((document.ended_at - document.started_at).total_seconds()))
        with self._pool.connection() as conn:
            updated = conn.execute(
                """
                UPDATE calls SET status = %s, duration = %s, updated_at = %s
                WHERE id = %s AND organization_id = %s
                """,
                (s.final_call_status(document), duration, document.ended_at, call_id, self._org),
            ).rowcount
            if updated != 1:
                raise DatabaseError(f"Call {call_id} does not exist")

            # Replace rather than append, so a retried save stays idempotent.
            conn.execute("DELETE FROM call_transcripts WHERE call_id = %s", (call_id,))
            if document.transcript:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO call_transcripts (id, call_id, speaker, "text", "timestamp")
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            (uuid.uuid4(), call_id, turn.speaker, turn.text, turn.timestamp)
                            for turn in document.transcript
                        ],
                    )

            conn.execute(
                "DELETE FROM call_actions WHERE call_id = %s AND action_type = %s",
                (call_id, OUTCOME_ACTION),
            )
            # action_status is left to its column default.
            conn.execute(
                """
                INSERT INTO call_actions (id, call_id, action_type, "result", created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(), call_id, OUTCOME_ACTION,
                    _jsonb(
                        {
                            "source": SOURCE,
                            "session_status": document.session_status,
                            "outcome": document.outcome.value,
                            "promised_payment_date": (
                                document.promised_payment_date.isoformat()
                                if document.promised_payment_date
                                else None
                            ),
                            "invoice_id": document.invoice_id,
                            "summary": document.summary,
                            "review_flags": [f.model_dump() for f in document.review_flags],
                        }
                    ),
                    document.ended_at,
                ),
            )

            conn.execute(
                "DELETE FROM call_events WHERE call_id = %s AND event_type = %s",
                (call_id, EVENT_ENDED),
            )
            self._insert_event(
                conn,
                call_id,
                EVENT_ENDED,
                {
                    "source": SOURCE,
                    "session_status": document.session_status,
                    "outcome": document.outcome.value,
                    "turn_count": len(document.transcript),
                    "review_flag_count": len(document.review_flags),
                    "transcript_blob": transcript_blob,
                },
                document.ended_at,
            )

            conn.execute(
                """
                UPDATE call_tasks SET status = %s, updated_at = %s
                WHERE organization_id = %s AND status = %s AND id = (
                    SELECT (event_data->>'call_task_id')::uuid FROM call_events
                    WHERE call_id = %s AND event_type = %s
                    ORDER BY created_at LIMIT 1
                )
                """,
                (
                    s.task_completed if completed else s.task_queued,
                    document.ended_at, self._org, s.task_in_progress, call_id, EVENT_STARTED,
                ),
            )
            self._audit(
                conn,
                f"call.session_ended call_id={call_id} status={document.session_status} "
                f"outcome={document.outcome.value}",
                document.ended_at,
            )

    def load_transcript(self, session_id: str) -> TranscriptDocument | None:
        """Rebuild a saved session's transcript document; None if not saved."""
        call_id = _as_uuid(session_id, "session_id")
        with self._pool.connection() as conn:
            call = conn.execute(
                "SELECT id, client_id, created_at FROM calls WHERE id = %s AND organization_id = %s",
                (call_id, self._org),
            ).fetchone()
            if call is None:
                return None
            events = {
                row["event_type"]: row
                for row in conn.execute(
                    """
                    SELECT event_type, event_data, created_at FROM call_events
                    WHERE call_id = %s AND event_type IN (%s, %s)
                    """,
                    (call_id, EVENT_STARTED, EVENT_ENDED),
                ).fetchall()
            }
            action = conn.execute(
                """
                SELECT "result" FROM call_actions
                WHERE call_id = %s AND action_type = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (call_id, OUTCOME_ACTION),
            ).fetchone()
            turns = conn.execute(
                """
                SELECT speaker, "text", "timestamp" FROM call_transcripts
                WHERE call_id = %s ORDER BY "timestamp", id
                """,
                (call_id,),
            ).fetchall()

        ended = events.get(EVENT_ENDED)
        started = events.get(EVENT_STARTED)
        if ended is None or started is None or action is None:
            return None
        result = action["result"]
        return TranscriptDocument(
            session_id=str(call["id"]),
            customer_id=str(call["client_id"]),
            invoice_id=started["event_data"].get("invoice_id") or result.get("invoice_id"),
            started_at=call["created_at"],
            ended_at=ended["created_at"],
            session_status=ended["event_data"]["session_status"],
            outcome=SessionOutcome(result["outcome"]),
            promised_payment_date=result.get("promised_payment_date"),
            transcript=[
                TranscriptTurn(
                    turn=number, speaker=row["speaker"], text=row["text"], timestamp=row["timestamp"]
                )
                for number, row in enumerate(turns, start=1)
            ],
            summary=result.get("summary"),
            review_flags=result.get("review_flags") or [],
        )

    def recover_interrupted_sessions(self) -> int:
        """Fail sessions this app left in progress (backend stopped mid-session).

        Their call tasks go back to queued so the customers are called again.
        Only calls tagged with this app's SOURCE are touched. Safe with a single
        backend instance only: a second instance would see the first one's live
        sessions as interrupted.
        """
        s = self._statuses
        now = self._clock()
        with self._pool.connection() as conn:
            stale = conn.execute(
                """
                SELECT c.id AS call_id, e.event_data->>'call_task_id' AS task_id
                FROM calls c
                JOIN call_events e ON e.call_id = c.id AND e.event_type = %s
                WHERE c.organization_id = %s AND c.status = %s AND e.event_data->>'source' = %s
                """,
                (EVENT_STARTED, self._org, s.call_in_progress, SOURCE),
            ).fetchall()
            for row in stale:
                call_id = row["call_id"]
                conn.execute(
                    "UPDATE calls SET status = %s, updated_at = %s WHERE id = %s",
                    (s.call_failed, now, call_id),
                )
                conn.execute(
                    """
                    INSERT INTO call_actions (id, call_id, action_type, "result", created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), call_id, OUTCOME_ACTION,
                        _jsonb(
                            {
                                "source": SOURCE,
                                "session_status": "failed",
                                "outcome": SessionOutcome.TECHNICAL_FAILURE.value,
                                "summary": "The backend stopped during the session; "
                                "no transcript was saved.",
                            }
                        ),
                        now,
                    ),
                )
                self._insert_event(conn, call_id, EVENT_INTERRUPTED, {"source": SOURCE}, now)
                if row["task_id"]:
                    conn.execute(
                        """
                        UPDATE call_tasks SET status = %s, updated_at = %s
                        WHERE id = %s AND organization_id = %s AND status = %s
                        """,
                        (s.task_queued, now, UUID(row["task_id"]), self._org, s.task_in_progress),
                    )
                self._audit(conn, f"call.session_interrupted call_id={call_id}", now)
        if stale:
            logger.warning(
                "Marked %d interrupted session(s) as failed and re-queued their call tasks",
                len(stale),
            )
        return len(stale)

    # --- Internals -------------------------------------------------------------------

    def _insert_event(
        self, conn: Any, call_id: UUID, event_type: str, data: dict[str, Any], at: datetime
    ) -> None:
        conn.execute(
            """
            INSERT INTO call_events (id, call_id, event_type, event_data, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (uuid.uuid4(), call_id, event_type, _jsonb(data), at),
        )

    def _audit(self, conn: Any, action: str, at: datetime) -> None:
        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, actor, "action", "timestamp")
            VALUES (%s, %s, %s, %s, %s)
            """,
            (uuid.uuid4(), self._org, AUDIT_ACTOR, action[:255], at),
        )


class PostgresTranscriptStorage:
    """TranscriptStorage that records the session in PostgreSQL.

    When a Blob storage is given, the transcript JSON is uploaded there first
    (idempotent overwrite) and its blob name is recorded on the session_ended
    event; the database transaction is the commit point, so a failed database
    write leaves the session open for End Session to be retried.
    """

    def __init__(self, store: PostgresStore, blob: BlobTranscriptStorage | None = None) -> None:
        self._store = store
        self._blob = blob
        self.kind = "postgres+azure_blob" if blob is not None else "postgres"

    def save(self, document: TranscriptDocument) -> str:
        blob_name = self._blob.save(document) if self._blob is not None else None
        self._store.save_session(document, blob_name)
        return document.session_id

    def load(self, reference: str) -> TranscriptDocument | None:
        return self._store.load_transcript(reference)

    def close(self) -> None:
        # The store is closed by the app, which owns it.
        if self._blob is not None:
            self._blob.close()
