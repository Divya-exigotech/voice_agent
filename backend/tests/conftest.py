import asyncio
import base64
import copy
import json
import sys
import warnings
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.customer import Customer
from app.services.customer_repository import parse_customers

# Deliberately not in alphabetical/ID order, so tests prove file order is kept.
SAMPLE_RECORDS: list[dict[str, Any]] = [
    {
        "customer_id": "cust_c",
        "customer_name": "Charlie Test",
        "phone_number": "+91XXXXXXXXXX",
        "preferred_language": "English",
        "invoice": {
            "invoice_id": "inv_c",
            "invoice_number": "INV-3",
            "amount_due": 300,
            "currency": "INR",
            "due_date": "2026-11-03",
            "payment_status": "unpaid",
        },
    },
    {
        "customer_id": "cust_a",
        "customer_name": "Alpha Test",
        "phone_number": "+91XXXXXXXXXX",
        "preferred_language": "Hindi",
        "invoice": {
            "invoice_id": "inv_a",
            "invoice_number": "INV-1",
            "amount_due": 100.5,
            "currency": "INR",
            "due_date": "2026-11-01",
            "payment_status": "overdue",
        },
    },
    {
        "customer_id": "cust_b",
        "customer_name": "Bravo Test",
        "phone_number": "+91XXXXXXXXXX",
        "preferred_language": "English",
        "invoice": {
            "invoice_id": "inv_b",
            "invoice_number": "INV-2",
            "amount_due": 200,
            "currency": "INR",
            "due_date": "2026-11-02",
            "payment_status": "partially_paid",
        },
    },
]
SAMPLE_ORDER = ["cust_c", "cust_a", "cust_b"]


@pytest.fixture
def sample_records() -> list[dict[str, Any]]:
    return copy.deepcopy(SAMPLE_RECORDS)


@pytest.fixture
def sample_customers() -> list[Customer]:
    return parse_customers(json.dumps(SAMPLE_RECORDS))


@pytest.fixture
def customers_file(tmp_path: Path) -> Path:
    path = tmp_path / "customers.json"
    path.write_text(json.dumps(SAMPLE_RECORDS), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def event_loop_policy():
    """psycopg's async mode needs the selector loop, which is not Windows' default."""
    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.get_event_loop_policy()


TODAY = date(2026, 11, 17)
TEST_TIMEOUT_SECONDS = 1.0


def isolated_settings(**overrides: Any) -> Settings:
    """Settings that never touch real Azure or the database: ignore .env and clear endpoints."""
    values: dict[str, Any] = {
        "azure_ai_project_endpoint": None,
        "azure_ai_agent_name": None,
        "azure_storage_account_url": None,
        "database_url": None,
        "database_organization_id": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)


# --- Fake Foundry voice agent ---------------------------------------------------
# Mimics client.voice_agents.realtime.connect(agent_name=...): an async
# context manager yielding a connection with send(), response.create() and
# async iteration over server events. Each response.create() emits the next
# scripted list of events (dicts, or an Exception to break the connection).


def agent_says(text: str) -> list[Any]:
    return [
        {"type": "response.output_audio_transcript.done", "transcript": text},
        {"type": "response.done", "response": {"status": "completed", "output": []}},
    ]


def agent_calls_tool(
    arguments: dict | str, call_id: str = "call_1", name: str = "record_outcome"
) -> list[Any]:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return [
        {
            "type": "response.done",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": name,
                        "call_id": call_id,
                        "arguments": args,
                    }
                ],
            },
        }
    ]


_END = object()


def user_speaks(text: str, item_id: str = "item_user_1") -> list[Any]:
    """Server-VAD events for one spoken user utterance."""
    return [
        {"type": "input_audio_buffer.speech_started", "item_id": item_id},
        {"type": "input_audio_buffer.speech_stopped", "item_id": item_id},
        {"type": "input_audio_buffer.committed", "item_id": item_id},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "transcript": text,
        },
    ]


def agent_audio(pcm: bytes = b"\x01\x00\x02\x00") -> dict[str, Any]:
    return {
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(pcm).decode(),
    }


class FakeConnection:
    def __init__(self, connector: "FakeConnector") -> None:
        self.connector = connector
        self.sent: list[dict[str, Any]] = []
        self.audio: list[bytes] = []
        self.creates = 0
        self.exited = False
        self.response = SimpleNamespace(create=self._create)
        self.input_audio_buffer = SimpleNamespace(append=self._append_audio)
        self._queue: asyncio.Queue | None = None

    async def _append_audio(self, *, audio: bytes) -> None:
        self.audio.append(audio)
        # Each audio chunk can trigger scripted server events (e.g. VAD + reply).
        if self.connector.audio_script:
            await self.emit(*self.connector.audio_script.pop(0))

    async def __aenter__(self) -> Self:
        self._queue = asyncio.Queue()
        if self.connector.fail_connect is not None:
            raise self.connector.fail_connect
        if self.connector.emit_session_created:
            await self._queue.put(
                {
                    "type": "session.created",
                    "conversation_id": f"conv_{len(self.connector.connections)}",
                    "session": {
                        "model": "gpt-realtime",
                        "output_modalities": ["audio"],
                        "tools": [{"type": "function", "name": "record_outcome"}],
                    },
                }
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.exited = True
        if self._queue is not None:
            await self._queue.put(_END)

    async def send(self, event: dict[str, Any]) -> None:
        self.sent.append(event)

    async def emit(self, *events: Any) -> None:
        for event in events:
            await self._queue.put(event)

    async def _create(self) -> None:
        self.creates += 1
        script = self.connector.script
        await self.emit(
            *(script.pop(0) if script else agent_says(f"Agent reply {self.creates}"))
        )

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        while True:
            event = await self._queue.get()
            if event is _END:
                return
            if isinstance(event, BaseException):
                raise event
            yield event

    def items(self, item_type: str) -> list[dict[str, Any]]:
        return [
            e["item"] for e in self.sent if e.get("item", {}).get("type") == item_type
        ]

    @property
    def system_context(self) -> str:
        return self.sent[0]["item"]["content"][0]["text"]


class FakeConnector:
    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.audio_script: list[list[Any]] = []
        self.connections: list[FakeConnection] = []
        self.fail_connect: BaseException | None = None
        self.emit_session_created = True
        self.closed = False

    def connect(self) -> FakeConnection:
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_agent() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def client(customers_file: Path, fake_agent: FakeConnector) -> Iterator[TestClient]:
    app = create_app(
        isolated_settings(
            customers_json_path=customers_file,
            azure_ai_agent_timeout_seconds=TEST_TIMEOUT_SECONDS,
        ),
        connector=fake_agent,
        today=lambda: TODAY,
    )
    # Context manager keeps one event loop alive across requests, so the
    # live agent session's background pump survives between calls.
    with TestClient(app) as test_client:
        yield test_client
