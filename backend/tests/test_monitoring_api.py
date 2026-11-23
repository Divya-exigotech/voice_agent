from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.schemas.monitoring import (
    AuditLogItem,
    AuditLogPage,
    CallItem,
    CallPage,
    CallTranscriptItem,
    CallTranscriptPage,
    CountBucket,
    MonitoringSummary,
)
from app.services.database import DatabaseError

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


class FakeMonitoringStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def summary(self) -> MonitoringSummary:
        if self.fail:
            raise DatabaseError("db down")
        return MonitoringSummary(
            generated_at=NOW,
            total_calls=3,
            calls_today=2,
            active_calls=1,
            completed_calls=1,
            failed_calls=1,
            declined_calls=0,
            average_duration_seconds=42.5,
            total_tasks=4,
            total_events=6,
            total_actions=2,
            total_audit_logs=6,
            calls_by_status=[CountBucket(key="completed", count=1)],
            tasks_by_status=[CountBucket(key="queued", count=3)],
            outcomes=[CountBucket(key="payment_promised", count=1)],
        )

    async def list_calls(self, **kwargs: Any) -> CallPage:
        self.calls.append(kwargs)
        item = CallItem(
            call_id="c1",
            client_id="k1",
            client_name="Alpha",
            status="completed",
            duration_seconds=30,
            created_at=NOW,
            updated_at=NOW,
        )
        return CallPage(total=1, items=[item])

    async def list_call_transcripts(self, **kwargs: Any) -> CallTranscriptPage:
        self.calls.append(kwargs)
        item = CallTranscriptItem(
            transcript_id="t1",
            call_id="c1",
            client_name="Alpha",
            speaker="agent",
            text="Hello",
            timestamp=NOW,
        )
        return CallTranscriptPage(total=1, items=[item])

    async def list_audit_logs(self, **kwargs: Any) -> AuditLogPage:
        item = AuditLogItem(
            audit_id="a1", actor="poc", action="call.session_started", timestamp=NOW
        )
        return AuditLogPage(total=1, items=[item])


@pytest.fixture
def fake_store(client: TestClient) -> FakeMonitoringStore:
    store = FakeMonitoringStore()
    client.app.state.monitoring = store
    return store


def test_monitoring_needs_database(client: TestClient) -> None:
    res = client.get("/api/monitoring/summary")
    assert res.status_code == 503
    assert "DATABASE_URL" in res.json()["detail"]


def test_summary(client: TestClient, fake_store: FakeMonitoringStore) -> None:
    data = client.get("/api/monitoring/summary").json()
    assert data["total_calls"] == 3
    assert data["outcomes"] == [{"key": "payment_promised", "count": 1}]


def test_summary_database_error_is_503(
    client: TestClient, fake_store: FakeMonitoringStore
) -> None:
    fake_store.fail = True
    assert client.get("/api/monitoring/summary").status_code == 503


def test_calls_pass_filters(
    client: TestClient, fake_store: FakeMonitoringStore
) -> None:
    res = client.get("/api/monitoring/calls?limit=10&offset=5&status=completed")
    assert res.status_code == 200
    assert res.json()["items"][0]["client_name"] == "Alpha"
    assert fake_store.calls == [{"limit": 10, "offset": 5, "status": "completed"}]


def test_limit_is_validated(
    client: TestClient, fake_store: FakeMonitoringStore
) -> None:
    assert client.get("/api/monitoring/calls?limit=1000").status_code == 422


def test_audit_logs(client: TestClient, fake_store: FakeMonitoringStore) -> None:
    items = client.get("/api/monitoring/audit-logs").json()["items"]
    assert items[0]["action"] == "call.session_started"


def test_call_transcripts(client: TestClient, fake_store: FakeMonitoringStore) -> None:
    res = client.get("/api/monitoring/call-transcripts?speaker=agent")
    assert res.status_code == 200
    assert res.json()["items"][0]["text"] == "Hello"
    assert fake_store.calls[-1]["speaker"] == "agent"
