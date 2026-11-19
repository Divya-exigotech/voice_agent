from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from azure.ai.projects.models import RealtimeServerEventType as T
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.deps import get_conversation_opener, get_sequence_service
from app.core.config import settings
from app.main import app
from app.schemas.customer import Customer
from app.services.customer_loader import parse_customers
from app.services.foundry_session import AgentConversation
from app.services.sequence_service import SequenceService
from tests.fakes import QueueConnection

ORIGIN = {"origin": settings.CORS_ORIGINS[0]}


def _fake_opener() -> Callable[[Customer], AbstractAsyncContextManager[AgentConversation]]:
    def opener(_customer: Customer) -> AbstractAsyncContextManager[AgentConversation]:
        @asynccontextmanager
        async def manager() -> AsyncIterator[AgentConversation]:
            conn = QueueConnection()
            await conn.events.put(
                {"type": T.RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DONE.value, "transcript": "Hello"}
            )
            await conn.events.put(
                {
                    "type": T.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED.value,
                    "transcript": "I will pay Friday",
                }
            )
            conversation = AgentConversation(conn)
            conversation.conversation_id = "conv_test"
            yield conversation

        return manager()

    return opener


@pytest.fixture
def api() -> Iterator[TestClient]:
    service = SequenceService(parse_customers(settings.CUSTOMERS_FILE.read_text("utf-8")))
    app.dependency_overrides[get_sequence_service] = lambda: service
    app.dependency_overrides[get_conversation_opener] = _fake_opener
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _new_session(api: TestClient) -> str:
    return api.post("/api/v1/sessions").json()["session_id"]


def test_voice_session_streams_transcript_and_saves_it(api: TestClient) -> None:
    session_id = _new_session(api)
    with api.websocket_connect(f"/api/v1/ws/sessions/{session_id}", headers=ORIGIN) as ws:
        assert ws.receive_json() == {"type": "status", "state": "active"}
        first = ws.receive_json()
        second = ws.receive_json()
        assert (first["speaker"], first["text"]) == ("agent", "Hello")
        assert (second["speaker"], second["text"]) == ("user", "I will pay Friday")
        ws.send_json({"type": "end"})
    saved = api.get(f"/api/v1/sessions/{session_id}/transcript").json()
    assert [t["speaker"] for t in saved] == ["agent", "user"]


def test_rejects_foreign_origin(api: TestClient) -> None:
    session_id = _new_session(api)
    url = f"/api/v1/ws/sessions/{session_id}"
    with (
        pytest.raises(WebSocketDisconnect),
        api.websocket_connect(url, headers={"origin": "https://evil.example"}),
    ):
        pass


def test_rejects_missing_origin(api: TestClient) -> None:
    session_id = _new_session(api)
    with (
        pytest.raises(WebSocketDisconnect),
        api.websocket_connect(f"/api/v1/ws/sessions/{session_id}"),
    ):
        pass


def test_unknown_session_gets_error_message(api: TestClient) -> None:
    with api.websocket_connect("/api/v1/ws/sessions/nope", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "error"


def test_second_stream_for_same_session_rejected(api: TestClient) -> None:
    session_id = _new_session(api)
    url = f"/api/v1/ws/sessions/{session_id}"
    with api.websocket_connect(url, headers=ORIGIN) as first:
        first.receive_json()
        with api.websocket_connect(url, headers=ORIGIN) as second:
            assert second.receive_json()["type"] == "error"
        first.send_json({"type": "end"})


def test_reports_not_configured_without_foundry_client(api: TestClient) -> None:
    app.dependency_overrides.pop(get_conversation_opener)
    session_id = _new_session(api)
    with api.websocket_connect(f"/api/v1/ws/sessions/{session_id}", headers=ORIGIN) as ws:
        message = ws.receive_json()
    assert message == {"type": "error", "message": "The voice agent is not configured"}


def test_transcript_unknown_session_404(api: TestClient) -> None:
    assert api.get("/api/v1/sessions/nope/transcript").status_code == 404
