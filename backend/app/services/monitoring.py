"""Read-only queries (awaited off the event loop) behind the live monitoring pages.

Every query is scoped to one organization and only reads: it never creates or
alters rows, so it cannot disturb the session/queue code in `database.py`.
Rows written by the voice sessions show up here as soon as they are committed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.schemas.monitoring import (
    AuditLogItem,
    AuditLogPage,
    CallActionItem,
    CallActionPage,
    CallEventItem,
    CallEventPage,
    CallItem,
    CallPage,
    CallTaskItem,
    CallTaskPage,
    CallTranscriptItem,
    CallTranscriptPage,
    CountBucket,
    MonitoringSummary,
)
from app.services.database import OUTCOME_ACTION, DatabaseError, StatusValues

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from app.config import Settings

logger = logging.getLogger(__name__)


class MonitoringStore:
    """Async-facing reader for calls, call tasks, call events, call actions and audit logs."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        organization_id: UUID,
        statuses: StatusValues,
    ) -> None:
        self._pool = pool
        self._org = organization_id
        self._statuses = statuses

    @classmethod
    def from_settings(
        cls, settings: Settings, organization_id: UUID
    ) -> MonitoringStore:
        """Build the store; the pool is opened later with `open()`."""
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        if settings.database_url is None:
            raise DatabaseError("DATABASE_URL is required")
        pool = AsyncConnectionPool(
            conninfo=settings.database_url.get_secret_value(),
            min_size=1,
            max_size=3,
            timeout=15,
            open=False,
            kwargs={
                "row_factory": dict_row,
                "options": f"-c search_path={settings.database_schema}",
                "application_name": "payment-followup-monitoring",
                "connect_timeout": 10,
            },
        )
        return cls(
            pool,
            organization_id=organization_id,
            statuses=StatusValues.from_settings(settings),
        )

    async def open(self) -> None:
        # Do not wait: the app must start even when the database is briefly down.
        await self._pool.open(wait=False)

    async def aclose(self) -> None:
        await self._pool.close()

    async def _fetch(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        # On Windows psycopg's async mode needs the selector event loop (see database.py).
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(sql, params)
                return await cur.fetchall()
        except Exception as exc:
            raise DatabaseError(f"Monitoring query failed: {exc}") from exc

    async def _page(
        self, count_sql: str, rows_sql: str, params: dict[str, Any]
    ) -> tuple[int, list[dict[str, Any]]]:
        total_rows = await self._fetch(count_sql, params)
        rows = await self._fetch(rows_sql, params)
        return int(total_rows[0]["n"]), rows

    # --- Summary ----------------------------------------------------------------

    async def summary(self) -> MonitoringSummary:
        s = self._statuses
        p = {"org": self._org}
        calls = (
            await self._fetch(
                """
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today,
                    count(*) FILTER (WHERE status = %(in_progress)s) AS active,
                    count(*) FILTER (WHERE status = %(completed)s) AS completed,
                    count(*) FILTER (WHERE status = %(failed)s) AS failed,
                    count(*) FILTER (WHERE status = %(declined)s) AS declined,
                    avg(duration) AS avg_duration
                FROM calls WHERE organization_id = %(org)s
                """,
                {
                    **p,
                    "in_progress": s.call_in_progress,
                    "completed": s.call_completed,
                    "failed": s.call_failed,
                    "declined": s.call_declined,
                },
            )
        )[0]
        counts = (
            await self._fetch(
                """
                SELECT
                    (SELECT count(*) FROM call_tasks WHERE organization_id = %(org)s) AS tasks,
                    (SELECT count(*) FROM call_events e JOIN calls c ON c.id = e.call_id
                      WHERE c.organization_id = %(org)s) AS events,
                    (SELECT count(*) FROM call_actions a JOIN calls c ON c.id = a.call_id
                      WHERE c.organization_id = %(org)s) AS actions,
                    (SELECT count(*) FROM audit_logs WHERE organization_id = %(org)s) AS audits
                """,
                p,
            )
        )[0]
        by_call_status = await self._fetch(
            """
            SELECT status AS key, count(*) AS n FROM calls
            WHERE organization_id = %(org)s GROUP BY status ORDER BY n DESC, status
            """,
            p,
        )
        by_task_status = await self._fetch(
            """
            SELECT status AS key, count(*) AS n FROM call_tasks
            WHERE organization_id = %(org)s GROUP BY status ORDER BY n DESC, status
            """,
            p,
        )
        outcomes = await self._fetch(
            """
            SELECT COALESCE(a."result"->>'outcome', 'unknown') AS key, count(*) AS n
            FROM call_actions a JOIN calls c ON c.id = a.call_id
            WHERE c.organization_id = %(org)s AND a.action_type = %(action)s
            GROUP BY 1 ORDER BY n DESC, key
            """,
            {**p, "action": OUTCOME_ACTION},
        )
        avg = calls["avg_duration"]
        return MonitoringSummary(
            generated_at=datetime.now(UTC),
            total_calls=calls["total"],
            calls_today=calls["today"],
            active_calls=calls["active"],
            completed_calls=calls["completed"],
            failed_calls=calls["failed"],
            declined_calls=calls["declined"],
            average_duration_seconds=float(avg) if avg is not None else None,
            total_tasks=counts["tasks"],
            total_events=counts["events"],
            total_actions=counts["actions"],
            total_audit_logs=counts["audits"],
            calls_by_status=_buckets(by_call_status),
            tasks_by_status=_buckets(by_task_status),
            outcomes=_buckets(outcomes),
        )

    # --- Lists ------------------------------------------------------------------

    async def list_calls(
        self, *, limit: int, offset: int, status: str | None
    ) -> CallPage:
        params = {"org": self._org, "status": status, "limit": limit, "offset": offset}
        total, rows = await self._page(
            """
            SELECT count(*) AS n FROM calls
            WHERE organization_id = %(org)s AND (%(status)s::text IS NULL OR status = %(status)s)
            """,
            """
            SELECT c.id, c.client_id, cl.name AS client_name, c.status, c.duration,
                   c.created_at, c.updated_at
            FROM calls c LEFT JOIN clients cl ON cl.id = c.client_id
            WHERE c.organization_id = %(org)s
              AND (%(status)s::text IS NULL OR c.status = %(status)s)
            ORDER BY c.created_at DESC, c.id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return CallPage(
            total=total,
            items=[
                CallItem(
                    call_id=str(r["id"]),
                    client_id=str(r["client_id"]),
                    client_name=r["client_name"],
                    status=r["status"],
                    duration_seconds=r["duration"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ],
        )

    async def list_call_tasks(
        self, *, limit: int, offset: int, status: str | None
    ) -> CallTaskPage:
        params = {"org": self._org, "status": status, "limit": limit, "offset": offset}
        total, rows = await self._page(
            """
            SELECT count(*) AS n FROM call_tasks
            WHERE organization_id = %(org)s AND (%(status)s::text IS NULL OR status = %(status)s)
            """,
            """
            SELECT t.id, t.client_id, cl.name AS client_name, t.status, t.scheduled_at,
                   t.created_at, t.updated_at
            FROM call_tasks t LEFT JOIN clients cl ON cl.id = t.client_id
            WHERE t.organization_id = %(org)s
              AND (%(status)s::text IS NULL OR t.status = %(status)s)
            ORDER BY t.updated_at DESC, t.id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return CallTaskPage(
            total=total,
            items=[
                CallTaskItem(
                    task_id=str(r["id"]),
                    client_id=str(r["client_id"]),
                    client_name=r["client_name"],
                    status=r["status"],
                    scheduled_at=r["scheduled_at"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ],
        )

    async def list_call_events(
        self, *, limit: int, offset: int, event_type: str | None
    ) -> CallEventPage:
        params = {
            "org": self._org,
            "kind": event_type,
            "limit": limit,
            "offset": offset,
        }
        total, rows = await self._page(
            """
            SELECT count(*) AS n FROM call_events e JOIN calls c ON c.id = e.call_id
            WHERE c.organization_id = %(org)s
              AND (%(kind)s::text IS NULL OR e.event_type = %(kind)s)
            """,
            """
            SELECT e.id, e.call_id, cl.name AS client_name, e.event_type, e.event_data,
                   e.created_at
            FROM call_events e
            JOIN calls c ON c.id = e.call_id
            LEFT JOIN clients cl ON cl.id = c.client_id
            WHERE c.organization_id = %(org)s
              AND (%(kind)s::text IS NULL OR e.event_type = %(kind)s)
            ORDER BY e.created_at DESC, e.id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return CallEventPage(
            total=total,
            items=[
                CallEventItem(
                    event_id=str(r["id"]),
                    call_id=str(r["call_id"]),
                    client_name=r["client_name"],
                    event_type=r["event_type"],
                    event_data=r["event_data"] or {},
                    created_at=r["created_at"],
                )
                for r in rows
            ],
        )

    async def list_call_actions(
        self, *, limit: int, offset: int, action_type: str | None
    ) -> CallActionPage:
        params = {
            "org": self._org,
            "kind": action_type,
            "limit": limit,
            "offset": offset,
        }
        total, rows = await self._page(
            """
            SELECT count(*) AS n FROM call_actions a JOIN calls c ON c.id = a.call_id
            WHERE c.organization_id = %(org)s
              AND (%(kind)s::text IS NULL OR a.action_type = %(kind)s)
            """,
            """
            SELECT a.id, a.call_id, cl.name AS client_name, a.action_type, a.action_status,
                   a."result" AS result, a.created_at
            FROM call_actions a
            JOIN calls c ON c.id = a.call_id
            LEFT JOIN clients cl ON cl.id = c.client_id
            WHERE c.organization_id = %(org)s
              AND (%(kind)s::text IS NULL OR a.action_type = %(kind)s)
            ORDER BY a.created_at DESC, a.id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return CallActionPage(
            total=total,
            items=[
                CallActionItem(
                    action_id=str(r["id"]),
                    call_id=str(r["call_id"]),
                    client_name=r["client_name"],
                    action_type=r["action_type"],
                    action_status=r["action_status"],
                    result=r["result"] or {},
                    created_at=r["created_at"],
                )
                for r in rows
            ],
        )

    async def list_call_transcripts(
        self, *, limit: int, offset: int, speaker: str | None
    ) -> CallTranscriptPage:
        params = {
            "org": self._org,
            "speaker": speaker,
            "limit": limit,
            "offset": offset,
        }
        total, rows = await self._page(
            """
            SELECT count(*) AS n FROM call_transcripts t JOIN calls c ON c.id = t.call_id
            WHERE c.organization_id = %(org)s
              AND (%(speaker)s::text IS NULL OR t.speaker = %(speaker)s)
            """,
            """
            SELECT t.id, t.call_id, cl.name AS client_name, t.speaker,
                   t."text" AS text, t."timestamp" AS ts
            FROM call_transcripts t
            JOIN calls c ON c.id = t.call_id
            LEFT JOIN clients cl ON cl.id = c.client_id
            WHERE c.organization_id = %(org)s
              AND (%(speaker)s::text IS NULL OR t.speaker = %(speaker)s)
            ORDER BY t."timestamp" DESC, t.id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return CallTranscriptPage(
            total=total,
            items=[
                CallTranscriptItem(
                    transcript_id=str(r["id"]),
                    call_id=str(r["call_id"]),
                    client_name=r["client_name"],
                    speaker=r["speaker"],
                    text=r["text"],
                    timestamp=r["ts"],
                )
                for r in rows
            ],
        )

    async def list_audit_logs(self, *, limit: int, offset: int) -> AuditLogPage:
        params = {"org": self._org, "limit": limit, "offset": offset}
        total, rows = await self._page(
            "SELECT count(*) AS n FROM audit_logs WHERE organization_id = %(org)s",
            """
            SELECT id, actor, "action" AS action, "timestamp" AS ts
            FROM audit_logs WHERE organization_id = %(org)s
            ORDER BY "timestamp" DESC, id LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return AuditLogPage(
            total=total,
            items=[
                AuditLogItem(
                    audit_id=str(r["id"]),
                    actor=r["actor"],
                    action=r["action"],
                    timestamp=r["ts"],
                )
                for r in rows
            ],
        )


def _buckets(rows: list[dict[str, Any]]) -> list[CountBucket]:
    return [CountBucket(key=str(r["key"]), count=int(r["n"])) for r in rows]
