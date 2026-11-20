import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import SAMPLE_ORDER, TODAY, FakeConnector, agent_calls_tool, agent_says, isolated_settings


def start(client: TestClient) -> str:
    return client.post("/api/sessions").json()["session_id"]


def say(client: TestClient, session_id: str, text: str | None):
    return client.post(f"/api/sessions/{session_id}/messages", json={"text": text})


def finalize(client: TestClient, session_id: str, **body):
    body.setdefault("session_status", "completed")
    return client.post(f"/api/sessions/{session_id}/transcript", json=body)


def test_opening_connects_agent_with_current_customer_context(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [agent_says("Hello Charlie, this is the payment assistant.")]
    sid = start(client)

    res = say(client, sid, None)
    assert res.status_code == 200
    body = res.json()
    assert body["agent_turn"]["speaker"] == "agent"
    assert body["agent_turn"]["text"] == "Hello Charlie, this is the payment assistant."
    assert [t["speaker"] for t in body["transcript"]] == ["agent"]
    assert body["proposed_outcome"] is None

    conn = fake_agent.connections[0]
    assert conn.sent[0]["item"]["role"] == "system"
    context = conn.system_context
    assert "Charlie Test" in context  # first record in file order
    assert "INV-3" in context
    assert "Start of conversation" in context
    assert conn.items("message")[1:] == []  # opening sends no user message
    assert conn.creates == 1


def test_second_opening_is_rejected(client) -> None:
    sid = start(client)
    assert say(client, sid, None).status_code == 200
    assert say(client, sid, None).status_code == 409


def test_turns_reuse_one_live_connection(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [agent_says("Hello."), agent_says("When can you pay?")]
    sid = start(client)
    say(client, sid, None)
    body = say(client, sid, "  I have not paid yet.  ").json()

    assert [(t["turn"], t["speaker"], t["text"]) for t in body["transcript"]] == [
        (1, "agent", "Hello."),
        (2, "user", "I have not paid yet."),
        (3, "agent", "When can you pay?"),
    ]
    assert len(fake_agent.connections) == 1
    user_item = fake_agent.connections[0].items("message")[1]
    assert user_item["role"] == "user"
    assert user_item["content"][0]["text"] == "I have not paid yet."


def test_valid_tool_call_is_recorded_and_used_on_finalize(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_says("Hello."),
        agent_calls_tool({
            "outcome": "payment_promised",
            "promised_payment_date": "2026-11-20",
            "summary": "Customer will pay on Friday.",
        }),
        agent_says("Thank you, I have noted Friday 20 November."),
    ]
    sid = start(client)
    say(client, sid, None)
    body = say(client, sid, "I'll pay on Friday").json()

    conn = fake_agent.connections[0]
    [output] = conn.items("function_call_output")
    assert output["call_id"] == "call_1"
    assert json.loads(output["output"]) == {"recorded": True}
    assert body["agent_turn"]["text"] == "Thank you, I have noted Friday 20 November."
    assert body["proposed_outcome"]["outcome"] == "payment_promised"
    assert body["proposed_outcome"]["promised_payment_date"] == "2026-11-20"

    final = finalize(client, sid).json()
    assert final["session"]["outcome"] == "payment_promised"
    assert final["session"]["promised_payment_date"] == "2026-11-20"
    assert final["advanced"] is True
    assert final["queue"]["current_customer"]["customer_id"] == SAMPLE_ORDER[1]
    assert conn.exited is True  # live agent connection closed on finalize


def test_invalid_tool_call_is_rejected_and_not_stored(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_calls_tool({"outcome": "payment_promised", "promised_payment_date": "2026-11-01"}),
        agent_says("Could you confirm the date?"),
    ]
    sid = start(client)
    body = say(client, sid, "I will pay on the 1st").json()

    output = json.loads(fake_agent.connections[0].items("function_call_output")[0]["output"])
    assert output["recorded"] is False
    assert "in the past" in output["error"]
    assert body["proposed_outcome"] is None
    assert finalize(client, sid).json()["session"]["outcome"] == "completed_no_commitment"


def test_unknown_tool_is_refused(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [agent_calls_tool({}, name="transfer_money"), agent_says("Sorry.")]
    sid = start(client)
    assert say(client, sid, "hi").status_code == 200
    output = json.loads(fake_agent.connections[0].items("function_call_output")[0]["output"])
    assert output == {"recorded": False, "error": "Unknown tool transfer_money"}


def test_operator_outcome_overrides_model_proposal(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [agent_calls_tool({"outcome": "reported_paid"}), agent_says("Thanks.")]
    sid = start(client)
    say(client, sid, "I paid already")
    final = finalize(client, sid, outcome="disputed_invoice").json()
    assert final["session"]["outcome"] == "disputed_invoice"


def test_server_transcript_is_saved_on_finalize(client) -> None:
    sid = start(client)
    say(client, sid, None)
    say(client, sid, "hello")
    final = finalize(client, sid).json()
    storage = client.app.state.session_manager._storage  # in-memory storage in tests
    doc = storage.load(final["session"]["transcript_ref"])
    assert [t.speaker for t in doc.transcript] == ["agent", "user", "agent"]


def test_agent_error_returns_502_and_session_continues(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_says("Hello."),
        [{"type": "error", "error": {"message": "rate limited"}}],
        agent_says("Sorry, could you repeat that?"),
    ]
    sid = start(client)
    say(client, sid, None)
    res = say(client, sid, "I will pay tomorrow")
    assert res.status_code == 502
    assert "rate limited" in res.json()["detail"]

    body = say(client, sid, "Tomorrow").json()
    assert [t["speaker"] for t in body["transcript"]] == ["agent", "user", "user", "agent"]
    assert len(fake_agent.connections) == 1


def test_dropped_connection_reconnects_with_history(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_says("Hello."),
        [ConnectionResetError("socket closed")],
        agent_says("Thanks for confirming."),
    ]
    sid = start(client)
    say(client, sid, None)
    res = say(client, sid, "I paid")
    assert res.status_code == 502
    assert "socket closed" in res.json()["detail"]

    assert say(client, sid, "I paid yesterday").status_code == 200
    assert len(fake_agent.connections) == 2
    assert fake_agent.connections[0].exited is True
    context = fake_agent.connections[1].system_context
    assert "Conversation so far" in context
    assert "Agent: Hello." in context
    assert 'Customer: "I paid"' in context


def test_agent_timeout_returns_502(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [[]]  # agent never answers
    sid = start(client)
    res = say(client, sid, None)
    assert res.status_code == 502
    assert "did not respond" in res.json()["detail"]


def test_connect_failure_returns_502(client, fake_agent: FakeConnector) -> None:
    fake_agent.fail_connect = PermissionError("401 Unauthorized")
    sid = start(client)
    res = say(client, sid, None)
    assert res.status_code == 502
    assert "401 Unauthorized" in res.json()["detail"]


def test_message_validation(client) -> None:
    sid = start(client)
    assert say(client, sid, "   ").status_code == 422
    assert say(client, sid, "x" * 2001).status_code == 422
    res = client.post(f"/api/sessions/{sid}/messages", json={"text": "hi", "role": "system"})
    assert res.status_code == 422


def test_unknown_and_ended_sessions(client) -> None:
    assert say(client, "missing", "hi").status_code == 404
    sid = start(client)
    finalize(client, sid)
    assert say(client, sid, "hi").status_code == 409


def test_not_configured_returns_503(customers_file: Path) -> None:
    app = create_app(isolated_settings(customers_json_path=customers_file), connector=None, today=lambda: TODAY)
    with TestClient(app) as client:
        assert client.get("/health").json()["agent_configured"] is False
        sid = start(client)
        res = say(client, sid, "hi")
        assert res.status_code == 503
        assert "AZURE_AI_PROJECT_ENDPOINT" in res.json()["detail"]
        # Queue/session flow still works without the agent.
        assert finalize(client, sid).status_code == 200


def test_shutdown_closes_live_sessions(customers_file: Path) -> None:
    connector = FakeConnector()
    app = create_app(isolated_settings(customers_json_path=customers_file), connector=connector, today=lambda: TODAY)
    with TestClient(app) as client:
        say(client, start(client), None)
    assert connector.connections[0].exited is True
    assert connector.closed is True


# --- M5: screening and redaction ---------------------------------------------------


def test_typed_secrets_are_redacted_before_storage_and_agent(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [agent_says("Hello."), agent_says("Please don't share codes like that.")]
    sid = start(client)
    say(client, sid, None)
    body = say(client, sid, "My OTP is 482913, go ahead").json()

    user_turn = body["transcript"][1]
    assert "482913" not in user_turn["text"]
    assert "[removed]" in user_turn["text"]
    sent_to_agent = fake_agent.connections[0].items("message")[-1]["content"][0]["text"]
    assert "482913" not in sent_to_agent
    assert [f["kind"] for f in body["review_flags"]] == ["sensitive_data_shared"]

    final = client.post(f"/api/sessions/{sid}/transcript", json={"session_status": "completed"}).json()
    doc = client.get(f"/api/sessions/{sid}/transcript").json()
    assert "482913" not in str(doc)
    assert final["session"]["review_flag_count"] == 1


def test_agent_credential_request_and_invented_facts_are_flagged(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_says("Hello Charlie, invoice INV-3 for INR 300 is overdue."),
        agent_says("Could you share your card number and CVV? A late fee of INR 50 will apply."),
    ]
    sid = start(client)
    first = say(client, sid, None).json()
    assert first["review_flags"] == []  # correct facts are not flagged

    body = say(client, sid, "How do I pay?").json()
    flag_kinds = sorted(f["kind"] for f in body["review_flags"])
    assert flag_kinds == ["pressure_or_policy", "sensitive_data_request", "unverified_amount"]
    assert all(f["speaker"] == "agent" for f in body["review_flags"])

    final = client.post(f"/api/sessions/{sid}/transcript", json={"session_status": "completed"}).json()
    assert final["session"]["review_flag_count"] == 3
    doc = client.get(f"/api/sessions/{sid}/transcript").json()
    assert sorted(f["kind"] for f in doc["review_flags"]) == flag_kinds


def test_agent_may_repeat_the_customers_own_amount_and_promised_date(client, fake_agent: FakeConnector) -> None:
    fake_agent.script = [
        agent_says("Hello."),
        agent_calls_tool({"outcome": "payment_promised", "promised_payment_date": "2026-11-20"}),
        agent_says("Noted: INR 150 now and the rest by 20 November."),
    ]
    sid = start(client)
    say(client, sid, None)
    body = say(client, sid, "I can pay 150 rupees now and the rest on Friday").json()
    assert body["review_flags"] == []
