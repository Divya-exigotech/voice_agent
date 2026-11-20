import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.customer_repository import CustomerDataError

from .conftest import SAMPLE_ORDER, isolated_settings

TS = "2026-11-16T10:00:05Z"


def finalize(client: TestClient, session_id: str, **body):
    body.setdefault("session_status", "completed")
    return client.post(f"/api/sessions/{session_id}/transcript", json=body)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "agent_configured": True,
        "transcript_storage": "memory",
        "customer_source": "json",
    }


def test_list_customers_in_file_order(client: TestClient) -> None:
    data = client.get("/api/customers").json()
    assert data["total"] == 3
    assert [c["customer_id"] for c in data["customers"]] == SAMPLE_ORDER


def test_initial_queue_is_first_record(client: TestClient) -> None:
    q = client.get("/api/queue").json()
    assert q["position"] == 1
    assert q["total"] == 3
    assert q["remaining"] == 3
    assert q["completed"] is False
    assert q["active_session_id"] is None
    assert q["current_customer"]["customer_id"] == SAMPLE_ORDER[0]


def test_session_lifecycle_advances_once(client: TestClient) -> None:
    created = client.post("/api/sessions", json={})
    assert created.status_code == 201
    session = created.json()
    assert session["customer_id"] == SAMPLE_ORDER[0]
    assert session["status"] == "active"
    assert client.get("/api/queue").json()["active_session_id"] == session["session_id"]

    # Second concurrent session is refused.
    assert client.post("/api/sessions").status_code == 409

    res = finalize(
        client,
        session["session_id"],
        transcript=[
            {"speaker": "agent", "text": "Hello", "timestamp": TS},
            {"speaker": "user", "text": "Hi", "timestamp": TS},
        ],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["advanced"] is True
    assert body["already_finalized"] is False
    assert body["session"]["status"] == "completed"
    assert body["session"]["outcome"] == "completed_no_commitment"
    assert body["session"]["transcript_ref"]
    assert body["queue"]["position"] == 2
    assert body["queue"]["current_customer"]["customer_id"] == SAMPLE_ORDER[1]

    # Duplicate end event must not skip a customer.
    dup = finalize(client, session["session_id"])
    assert dup.status_code == 200
    assert dup.json()["advanced"] is False
    assert dup.json()["already_finalized"] is True
    assert client.get("/api/queue").json()["position"] == 2


def test_walks_all_customers_then_completes(client: TestClient) -> None:
    seen = []
    for _ in SAMPLE_ORDER:
        session = client.post("/api/sessions").json()
        seen.append(session["customer_id"])
        finalize(client, session["session_id"])
    assert seen == SAMPLE_ORDER

    q = client.get("/api/queue").json()
    assert q["completed"] is True
    assert q["position"] is None
    assert q["current_customer"] is None
    assert q["remaining"] == 0

    res = client.post("/api/sessions")
    assert res.status_code == 409
    assert "All customers completed" in res.json()["detail"]

    assert len(client.get("/api/sessions").json()) == len(SAMPLE_ORDER)


def test_failed_session_does_not_advance(client: TestClient) -> None:
    session = client.post("/api/sessions").json()
    res = finalize(client, session["session_id"], session_status="failed")
    assert res.json()["session"]["outcome"] == "technical_failure"
    assert res.json()["advanced"] is False
    assert client.get("/api/queue").json()["current_customer"]["customer_id"] == SAMPLE_ORDER[0]


def test_stale_expected_customer_is_rejected(client: TestClient) -> None:
    res = client.post("/api/sessions", json={"expected_customer_id": SAMPLE_ORDER[1]})
    assert res.status_code == 409
    ok = client.post("/api/sessions", json={"expected_customer_id": SAMPLE_ORDER[0]})
    assert ok.status_code == 201


def test_finalize_unknown_session(client: TestClient) -> None:
    assert finalize(client, "missing").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"session_status": "completed", "promised_payment_date": "2026-11-20"},
        {"session_status": "completed", "outcome": "payment_promised", "promised_payment_date": "soon"},
        {"session_status": "completed", "outcome": "made_up"},
        {"session_status": "completed", "outcome": "technical_failure"},
        {"session_status": "failed", "outcome": "reported_paid"},
        {"session_status": "active"},
        {"session_status": "completed", "transcript": [{"speaker": "bot", "text": "x", "timestamp": TS}]},
        {"session_status": "completed", "transcript": [{"speaker": "user", "text": "", "timestamp": TS}]},
        {"session_status": "completed", "transcript": [{"speaker": "user", "text": "x", "timestamp": "2026-11-16T10:00:05"}]},
    ],
    ids=[
        "date-without-promise",
        "bad-date",
        "unknown-outcome",
        "technical-failure-on-completed",
        "failed-with-business-outcome",
        "non-final-status",
        "bad-speaker",
        "empty-text",
        "naive-timestamp",
    ],
)
def test_finalize_validation(client: TestClient, body) -> None:
    session = client.post("/api/sessions").json()
    res = client.post(f"/api/sessions/{session['session_id']}/transcript", json=body)
    assert res.status_code == 422
    # Rejected payloads leave the session open and the queue unchanged.
    q = client.get("/api/queue").json()
    assert q["active_session_id"] == session["session_id"]
    assert q["position"] == 1


def test_payment_promised_with_date(client: TestClient) -> None:
    session = client.post("/api/sessions").json()
    res = finalize(
        client,
        session["session_id"],
        outcome="payment_promised",
        promised_payment_date="2026-11-20",
    )
    assert res.status_code == 200
    assert res.json()["session"]["promised_payment_date"] == "2026-11-20"


def test_reset_queue(client: TestClient) -> None:
    session = client.post("/api/sessions").json()
    assert client.post("/api/queue/reset").status_code == 409
    finalize(client, session["session_id"])
    q = client.post("/api/queue/reset").json()
    assert q["position"] == 1


def test_invalid_customer_file_fails_startup(tmp_path: Path) -> None:
    bad = tmp_path / "customers.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(CustomerDataError):
        create_app(isolated_settings(customers_json_path=bad), connector=None)


# --- M4: transcript storage -------------------------------------------------------


def test_saved_transcript_can_be_read_back(client: TestClient) -> None:
    session = client.post("/api/sessions").json()
    sid = session["session_id"]
    assert client.get(f"/api/sessions/{sid}/transcript").status_code == 409  # not saved yet

    final = finalize(
        client,
        sid,
        outcome="payment_promised",
        promised_payment_date="2026-11-20",
        transcript=[
            {"speaker": "agent", "text": "Hello", "timestamp": TS},
            {"speaker": "user", "text": "I can pay on Friday.", "timestamp": TS},
        ],
    ).json()
    ref = final["session"]["transcript_ref"]
    assert ref.endswith(f"/{SAMPLE_ORDER[0]}/{sid}/transcript.json")
    assert final["session"]["transcript_store"] == "memory"

    doc = client.get(f"/api/sessions/{sid}/transcript").json()
    assert doc["schema_version"] == "1.0"
    assert doc["session_id"] == sid
    assert doc["customer_id"] == SAMPLE_ORDER[0]
    assert doc["outcome"] == "payment_promised"
    assert doc["promised_payment_date"] == "2026-11-20"
    assert [(t["turn"], t["speaker"]) for t in doc["transcript"]] == [(1, "agent"), (2, "user")]


def test_transcript_of_unknown_session_is_not_found(client: TestClient) -> None:
    assert client.get("/api/sessions/session_doesnotexist/transcript").status_code == 404


def test_save_failure_keeps_session_open_until_retry_succeeds(customers_file: Path) -> None:
    from app.services.transcript_storage import InMemoryTranscriptStorage

    class FailingTwice(InMemoryTranscriptStorage):
        failures = 2

        def save(self, document):
            if self.failures:
                self.failures -= 1
                raise ConnectionError("blob endpoint unreachable")
            return super().save(document)

    app = create_app(isolated_settings(customers_json_path=customers_file), storage=FailingTwice(), connector=None)
    with TestClient(app) as client:
        sid = client.post("/api/sessions").json()["session_id"]
        for _ in range(2):
            res = finalize(client, sid)
            assert res.status_code == 502
            assert "blob endpoint unreachable" in res.json()["detail"]
            q = client.get("/api/queue").json()
            assert q["position"] == 1 and q["active_session_id"] == sid

        res = finalize(client, sid)
        assert res.status_code == 200
        assert res.json()["advanced"] is True
        assert client.get("/api/queue").json()["position"] == 2
        assert client.get(f"/api/sessions/{sid}/transcript").status_code == 200


def test_unreadable_transcript_returns_502(customers_file: Path) -> None:
    from app.services.transcript_storage import InMemoryTranscriptStorage

    class LosesBlobs(InMemoryTranscriptStorage):
        def load(self, reference):
            return None

    app = create_app(isolated_settings(customers_json_path=customers_file), storage=LosesBlobs(), connector=None)
    with TestClient(app) as client:
        sid = client.post("/api/sessions").json()["session_id"]
        finalize(client, sid)
        res = client.get(f"/api/sessions/{sid}/transcript")
        assert res.status_code == 502
        assert "not found in storage" in res.json()["detail"]


def test_blob_storage_is_used_when_account_url_is_set(customers_file: Path) -> None:
    from app.main import build_transcript_storage
    from app.services.transcript_storage import BlobTranscriptStorage

    settings = isolated_settings(
        customers_json_path=customers_file,
        azure_storage_account_url="https://example.blob.core.windows.net",
        azure_storage_container="call-transcripts",
    )
    storage = build_transcript_storage(settings)  # constructs clients; no network call
    try:
        assert isinstance(storage, BlobTranscriptStorage)
        assert storage.container_name == "call-transcripts"
    finally:
        storage.close()


def test_health_is_reachable_under_api_prefix(client: TestClient) -> None:
    assert client.get("/api/health").json() == client.get("/health").json()


def test_payment_promise_requeues_customer_after_everyone_else(
    customers_file: Path, fake_agent
) -> None:
    app = create_app(
        isolated_settings(customers_json_path=customers_file, followup_delay_seconds=1),
        connector=fake_agent,
    )
    with TestClient(app) as client:
        session = client.post("/api/sessions").json()
        finalize(
            client,
            session["session_id"],
            outcome="payment_promised",
            promised_payment_date="2026-11-20",
        )
        time.sleep(1.5)
        queue = client.get("/api/queue").json()
        # The follow-up waits at the end; the other customers are called first.
        assert queue["total"] == 4
        assert queue["current_customer"]["customer_id"] == SAMPLE_ORDER[1]
        for expected in SAMPLE_ORDER[1:]:
            created = client.post("/api/sessions").json()
            assert created["customer_id"] == expected
            finalize(client, created["session_id"])
        queue = client.get("/api/queue").json()
        assert queue["current_customer"]["customer_id"] == SAMPLE_ORDER[0]
        assert client.post("/api/sessions").json()["customer_id"] == SAMPLE_ORDER[0]
