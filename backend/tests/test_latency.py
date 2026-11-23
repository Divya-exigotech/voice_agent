from fastapi.testclient import TestClient

from app.services import latency
from app.services.latency import LatencyRecorder


def test_recorder_stats_and_paging() -> None:
    rec = LatencyRecorder(max_samples=3)
    for ms in (100, 200, 300, 400):
        rec.record(latency.VOICE_REPLY, ms, session_id="s")
    stats = {s.metric: s for s in rec.stats()}
    voice = stats[latency.VOICE_REPLY]
    assert voice.count == 3  # oldest sample dropped
    assert (voice.min_ms, voice.max_ms, voice.last_ms) == (200, 400, 400)
    assert voice.average_ms == 300
    assert voice.p95_ms == 400
    assert stats[latency.HTTP].count == 0
    total, page = rec.recent(limit=2, offset=0, metric=None)
    assert total == 3
    assert [s.milliseconds for s in page] == [400, 300]


def test_latency_endpoints_need_no_database(client: TestClient) -> None:
    latency.recorder.record(latency.DB_FINAL_SAVE, 123.4, session_id="abc")
    summary = client.get("/api/monitoring/latency/summary").json()
    final = next(m for m in summary["metrics"] if m["metric"] == latency.DB_FINAL_SAVE)
    assert final["count"] >= 1
    page = client.get(
        "/api/monitoring/latency/samples?metric=db_final_save&limit=5"
    ).json()
    assert page["items"][0]["session_id"] == "abc"
    assert page["items"][0]["milliseconds"] == 123.4


def test_http_requests_are_recorded_but_monitoring_polling_is_not(
    client: TestClient,
) -> None:
    client.get("/api/queue")
    client.get("/api/monitoring/latency/summary")
    page = client.get("/api/monitoring/latency/samples?metric=http&limit=200").json()
    labels = {i["label"] for i in page["items"]}
    assert "GET /api/queue" in labels
    assert not any(label.startswith("GET /api/monitoring") for label in labels)
