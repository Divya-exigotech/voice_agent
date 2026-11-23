"""Read-only schemas for the live monitoring pages (calls, tasks, events, actions, audit)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CallItem(BaseModel):
    call_id: str
    client_id: str
    client_name: str | None = None
    status: str
    duration_seconds: int | None = None
    created_at: datetime
    updated_at: datetime


class CallTaskItem(BaseModel):
    task_id: str
    client_id: str
    client_name: str | None = None
    status: str
    scheduled_at: datetime
    created_at: datetime
    updated_at: datetime


class CallEventItem(BaseModel):
    event_id: str
    call_id: str
    client_name: str | None = None
    event_type: str
    event_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CallActionItem(BaseModel):
    action_id: str
    call_id: str
    client_name: str | None = None
    action_type: str
    action_status: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CallTranscriptItem(BaseModel):
    transcript_id: str
    call_id: str
    client_name: str | None = None
    speaker: str
    text: str
    timestamp: datetime


class AuditLogItem(BaseModel):
    audit_id: str
    actor: str
    action: str
    timestamp: datetime


class CallPage(BaseModel):
    total: int
    items: list[CallItem]


class CallTaskPage(BaseModel):
    total: int
    items: list[CallTaskItem]


class CallEventPage(BaseModel):
    total: int
    items: list[CallEventItem]


class CallActionPage(BaseModel):
    total: int
    items: list[CallActionItem]


class CallTranscriptPage(BaseModel):
    total: int
    items: list[CallTranscriptItem]


class AuditLogPage(BaseModel):
    total: int
    items: list[AuditLogItem]


class CountBucket(BaseModel):
    key: str
    count: int


class MonitoringSummary(BaseModel):
    """Live totals shown on the dashboard; every number is read from the database."""

    generated_at: datetime
    total_calls: int
    calls_today: int
    active_calls: int
    completed_calls: int
    failed_calls: int
    declined_calls: int
    average_duration_seconds: float | None
    total_tasks: int
    total_events: int
    total_actions: int
    total_audit_logs: int
    calls_by_status: list[CountBucket]
    tasks_by_status: list[CountBucket]
    outcomes: list[CountBucket]


class LatencyStatItem(BaseModel):
    metric: str
    count: int
    average_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    last_ms: float | None = None
    last_at: datetime | None = None


class LatencySummary(BaseModel):
    generated_at: datetime
    metrics: list[LatencyStatItem]


class LatencySampleItem(BaseModel):
    seq: int
    metric: str
    milliseconds: float
    at: datetime
    label: str | None = None
    session_id: str | None = None


class LatencySamplePage(BaseModel):
    total: int
    items: list[LatencySampleItem]
