"""Browser voice WebSocket, end to end against the fake Foundry voice agent."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app

from .conftest import (
    TODAY,
    FakeConnector,
    agent_audio,
    agent_calls_tool,
    agent_says,
    isolated_settings,
    user_speaks,
)

ORIGIN = "http://localhost:3000"
PCM_FRAME = b"\x00\x01" * 1200  # 50 ms of 24 kHz PCM16


def start(client: TestClient) -> str:
    return client.post("/api/sessions").json()["session_id"]


def connect(client: TestClient, session_id: str, origin: str = ORIGIN):
    return client.websocket_connect(
        f"/api/sessions/{session_id}/voice", headers={"origin": origin}
    )


def next_message(ws) -> Any:
    message = ws.receive()
    if message["type"] == "websocket.close":
        raise AssertionError(f"socket closed with code {message.get('code')}")
    if message.get("bytes") is not None:
        return message["bytes"]
    return json.loads(message["text"])


def receive_until(ws, predicate, limit: int = 30) -> list[Any]:
    seen = []
    for _ in range(limit):
        message = next_message(ws)
        seen.append(message)
        if predicate(message):
            return seen
    raise AssertionError(f"condition not met; received {seen}")


def wait_closed(ws, limit: int = 30) -> int:
    """Drain queued frames until the server closes; return the close code."""
    for _ in range(limit):
        message = ws.receive()
        if message["type"] == "websocket.close":
            return message.get("code", 1000)
    raise AssertionError("socket was not closed")


def is_transcript(n: int):
    return lambda m: (
        isinstance(m, dict)
        and m.get("type") == "transcript"
        and len(m["transcript"]) == n
    )


def test_agent_speaks_first_with_audio_and_transcript(
    client, fake_agent: FakeConnector
) -> None:
    fake_agent.script = [
        [agent_audio(b"\x01\x00\x02\x00"), *agent_says("Hello Charlie.")]
    ]
    sid = start(client)

    with connect(client, sid) as ws:
        ready = next_message(ws)
        assert ready == {
            "type": "ready",
            "transcript": [],
            "proposed_outcome": None,
            "review_flags": [],
        }
        seen = receive_until(ws, is_transcript(1))

    assert b"\x01\x00\x02\x00" in seen  # agent audio relayed as binary PCM
    assert seen[-1]["transcript"][0]["speaker"] == "agent"
    assert seen[-1]["transcript"][0]["text"] == "Hello Charlie."
    conn = fake_agent.connections[0]
    assert "Charlie Test" in conn.system_context
    assert conn.creates == 1


def test_spoken_conversation_with_outcome_and_finalize(
    client, fake_agent: FakeConnector
) -> None:
    fake_agent.script = [
        agent_says("Hello."),
        agent_says("Thank you, noted for Friday."),
    ]
    fake_agent.audio_script = [
        [
            *user_speaks("I will pay on Friday"),
            *agent_calls_tool(
                {"outcome": "payment_promised", "promised_payment_date": "2026-11-20"}
            ),
        ]
    ]
    sid = start(client)

    with connect(client, sid) as ws:
        receive_until(ws, is_transcript(1))
        ws.send_bytes(PCM_FRAME)
        seen = receive_until(ws, is_transcript(3))

        final_transcript = seen[-1]
        assert [(t["speaker"], t["text"]) for t in final_transcript["transcript"]] == [
            ("agent", "Hello."),
            ("user", "I will pay on Friday"),
            ("agent", "Thank you, noted for Friday."),
        ]
        assert final_transcript["proposed_outcome"]["outcome"] == "payment_promised"
        assert {
            "type": "speech_started"
        } in seen  # browser flushes agent audio (barge-in)

        res = client.post(
            f"/api/sessions/{sid}/transcript", json={"session_status": "completed"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["session"]["outcome"] == "payment_promised"
        assert body["session"]["promised_payment_date"] == "2026-11-20"
        assert body["advanced"] is True
        receive_until(ws, lambda m: m == {"type": "session_ended"})

    conn = fake_agent.connections[0]
    assert conn.audio == [PCM_FRAME]
    assert conn.exited is True
    storage = client.app.state.session_manager._storage
    doc = client.portal.call(storage.load, body["session"]["transcript_ref"])
    assert [t.speaker for t in doc.transcript] == ["agent", "user", "agent"]


def test_invalid_audio_frames_are_ignored(client, fake_agent: FakeConnector) -> None:
    fake_agent.audio_script = [[{"type": "input_audio_buffer.speech_started"}]]
    sid = start(client)
    with connect(client, sid) as ws:
        receive_until(ws, is_transcript(1))
        ws.send_bytes(b"\x00")  # odd length: not PCM16
        ws.send_bytes(b"\x00" * 200_000)  # too large
        ws.send_text("not json")
        ws.send_bytes(PCM_FRAME)
        receive_until(ws, lambda m: m == {"type": "speech_started"})
    assert fake_agent.connections[0].audio == [PCM_FRAME]


def test_stop_detaches_browser_but_keeps_agent_session(
    client, fake_agent: FakeConnector
) -> None:
    sid = start(client)
    with connect(client, sid) as ws:
        receive_until(ws, is_transcript(1))
        ws.send_json({"type": "stop"})
        assert wait_closed(ws) == 1000

    # The live agent session survives; typed turns still work until End Session.
    res = client.post(f"/api/sessions/{sid}/messages", json={"text": "Are you there?"})
    assert res.status_code == 200
    assert len(fake_agent.connections) == 1


def test_reconnecting_browser_gets_transcript_without_new_opening(
    client, fake_agent: FakeConnector
) -> None:
    sid = start(client)
    with connect(client, sid) as ws:
        receive_until(ws, is_transcript(1))
    with connect(client, sid) as ws:
        ready = next_message(ws)
        assert ready["type"] == "ready"
        assert len(ready["transcript"]) == 1
    assert fake_agent.connections[0].creates == 1  # opening not repeated


def test_agent_disconnect_is_reported_to_browser(
    client, fake_agent: FakeConnector
) -> None:
    fake_agent.audio_script = [[ConnectionResetError("network down")]]
    sid = start(client)
    with connect(client, sid) as ws:
        receive_until(ws, is_transcript(1))
        ws.send_bytes(PCM_FRAME)
        closed = receive_until(
            ws, lambda m: isinstance(m, dict) and m.get("type") == "agent_closed"
        )
        assert "network down" in closed[-1]["reason"]
        ws.send_bytes(PCM_FRAME)  # agent gone: server reports and closes
        error = receive_until(
            ws, lambda m: isinstance(m, dict) and m.get("type") == "error"
        )
        assert "not connected" in error[-1]["message"]
        assert wait_closed(ws) == 4502


def test_cross_site_origin_is_rejected(client) -> None:
    sid = start(client)
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        connect(client, sid, origin="https://evil.example"),
    ):
        pass
    assert exc.value.code == 4403


@pytest.mark.parametrize(
    ("session", "code", "message"),
    [("missing", 4404, "Session not found"), ("ended", 4409, "already ended")],
)
def test_unknown_or_ended_session(client, session, code, message) -> None:
    sid = "missing"
    if session == "ended":
        sid = start(client)
        client.post(
            f"/api/sessions/{sid}/transcript", json={"session_status": "completed"}
        )
    with connect(client, sid) as ws:
        error = next_message(ws)
        assert error["type"] == "error" and message in error["message"]
        assert wait_closed(ws) == code


def test_agent_not_configured(customers_file: Path) -> None:
    app = create_app(
        isolated_settings(customers_json_path=customers_file),
        connector=None,
        today=lambda: TODAY,
    )
    with TestClient(app) as client:
        sid = start(client)
        with connect(client, sid) as ws:
            error = next_message(ws)
            assert "not configured" in error["message"]
            assert wait_closed(ws) == 4503


def test_agent_connect_failure(client, fake_agent: FakeConnector) -> None:
    fake_agent.fail_connect = PermissionError("401 Unauthorized")
    sid = start(client)
    with connect(client, sid) as ws:
        error = next_message(ws)
        assert "401 Unauthorized" in error["message"]
        assert wait_closed(ws) == 4502
