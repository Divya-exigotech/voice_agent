"""Read-only live monitoring endpoints for the dashboard pages."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import MonitoringStoreDep
from app.schemas.monitoring import (
    AuditLogPage,
    CallActionPage,
    CallEventPage,
    CallPage,
    CallTaskPage,
    CallTranscriptPage,
    LatencySampleItem,
    LatencySamplePage,
    LatencyStatItem,
    LatencySummary,
    MonitoringSummary,
)
from app.services import latency
from app.services.database import DatabaseError

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
Filter = Annotated[str | None, Query(min_length=1, max_length=50)]


def _unavailable(exc: DatabaseError) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("/summary", response_model=MonitoringSummary)
async def get_summary(store: MonitoringStoreDep) -> MonitoringSummary:
    try:
        return await store.summary()
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/calls", response_model=CallPage)
async def list_calls(
    store: MonitoringStoreDep,
    limit: Limit = 50,
    offset: Offset = 0,
    call_status: Annotated[
        str | None, Query(alias="status", min_length=1, max_length=30)
    ] = None,
) -> CallPage:
    try:
        return await store.list_calls(limit=limit, offset=offset, status=call_status)
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/call-tasks", response_model=CallTaskPage)
async def list_call_tasks(
    store: MonitoringStoreDep,
    limit: Limit = 50,
    offset: Offset = 0,
    task_status: Annotated[
        str | None, Query(alias="status", min_length=1, max_length=30)
    ] = None,
) -> CallTaskPage:
    try:
        return await store.list_call_tasks(
            limit=limit, offset=offset, status=task_status
        )
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/call-events", response_model=CallEventPage)
async def list_call_events(
    store: MonitoringStoreDep,
    limit: Limit = 50,
    offset: Offset = 0,
    event_type: Filter = None,
) -> CallEventPage:
    try:
        return await store.list_call_events(
            limit=limit, offset=offset, event_type=event_type
        )
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/call-actions", response_model=CallActionPage)
async def list_call_actions(
    store: MonitoringStoreDep,
    limit: Limit = 50,
    offset: Offset = 0,
    action_type: Filter = None,
) -> CallActionPage:
    try:
        return await store.list_call_actions(
            limit=limit, offset=offset, action_type=action_type
        )
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/call-transcripts", response_model=CallTranscriptPage)
async def list_call_transcripts(
    store: MonitoringStoreDep,
    limit: Limit = 50,
    offset: Offset = 0,
    speaker: Filter = None,
) -> CallTranscriptPage:
    try:
        return await store.list_call_transcripts(
            limit=limit, offset=offset, speaker=speaker
        )
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/audit-logs", response_model=AuditLogPage)
async def list_audit_logs(
    store: MonitoringStoreDep, limit: Limit = 50, offset: Offset = 0
) -> AuditLogPage:
    try:
        return await store.list_audit_logs(limit=limit, offset=offset)
    except DatabaseError as exc:
        raise _unavailable(exc) from exc


@router.get("/latency/summary", response_model=LatencySummary)
async def get_latency_summary() -> LatencySummary:
    """Per-metric latency figures; kept in memory, so no database is needed."""
    return LatencySummary(
        generated_at=datetime.now(UTC),
        metrics=[LatencyStatItem(**asdict(s)) for s in latency.recorder.stats()],
    )


@router.get("/latency/samples", response_model=LatencySamplePage)
async def list_latency_samples(
    limit: Limit = 50, offset: Offset = 0, metric: Filter = None
) -> LatencySamplePage:
    total, samples = latency.recorder.recent(limit=limit, offset=offset, metric=metric)
    return LatencySamplePage(
        total=total,
        items=[
            LatencySampleItem(
                seq=s.seq,
                metric=s.metric,
                milliseconds=round(s.milliseconds, 1),
                at=s.at,
                label=s.label,
                session_id=s.session_id,
            )
            for s in samples
        ],
    )
