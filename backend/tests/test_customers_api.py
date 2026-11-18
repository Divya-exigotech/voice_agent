import httpx
import pytest

pytestmark = pytest.mark.anyio


async def test_customers_returned_in_file_order(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/customers")
    assert response.status_code == 200
    assert [c["customer_id"] for c in response.json()] == [
        "cust_demo_001",
        "cust_demo_002",
        "cust_demo_003",
        "cust_demo_004",
    ]


async def test_queue_starts_at_first_customer(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/queue")).json()
    assert body["current_position"] == 1
    assert body["total"] == 4
    assert body["current_customer"]["customer_id"] == "cust_demo_001"


async def test_full_sequence_and_completion(client: httpx.AsyncClient) -> None:
    for _ in range(4):
        created = await client.post("/api/v1/sessions")
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        assert (await client.post(f"/api/v1/sessions/{session_id}/finalize")).status_code == 200
    body = (await client.get("/api/v1/queue")).json()
    assert body["all_completed"] is True
    assert (await client.post("/api/v1/sessions")).status_code == 409


async def test_double_finalize_is_idempotent(client: httpx.AsyncClient) -> None:
    session_id = (await client.post("/api/v1/sessions")).json()["session_id"]
    first = (await client.post(f"/api/v1/sessions/{session_id}/finalize")).json()
    second = (await client.post(f"/api/v1/sessions/{session_id}/finalize")).json()
    assert first["current_index"] == second["current_index"] == 1


async def test_second_active_session_rejected(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/sessions")
    assert (await client.post("/api/v1/sessions")).status_code == 409


async def test_finalize_unknown_session_404(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/sessions/nope/finalize")).status_code == 404
