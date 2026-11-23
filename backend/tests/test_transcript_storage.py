from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app.schemas.session import SessionOutcome, TranscriptDocument, TranscriptTurn
from app.services.transcript_storage import (
    BlobTranscriptStorage,
    InMemoryTranscriptStorage,
    transcript_blob_name,
)

STARTED = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def document(**overrides) -> TranscriptDocument:
    values: dict[str, Any] = {
        "session_id": "session_abc123",
        "customer_id": "cust_demo_001",
        "invoice_id": "inv_demo_001",
        "started_at": STARTED,
        "ended_at": STARTED + timedelta(minutes=2),
        "session_status": "completed",
        "outcome": SessionOutcome.PAYMENT_PROMISED,
        "promised_payment_date": "2026-11-20",
        "transcript": [
            TranscriptTurn(turn=1, speaker="agent", text="Hello", timestamp=STARTED),
            TranscriptTurn(
                turn=2, speaker="user", text="I can pay on Friday.", timestamp=STARTED
            ),
        ],
        "summary": "The user indicated a payment date.",
    }
    values.update(overrides)
    return TranscriptDocument(**values)


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def readall(self) -> bytes:
        return self._data


class FakeContainer:
    """Stands in for azure.storage.blob.ContainerClient."""

    container_name = "call-transcripts"

    def __init__(self, fail_uploads: int = 0) -> None:
        self.blobs: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.fail_uploads = fail_uploads

    async def upload_blob(self, name, data, **kwargs):
        if self.fail_uploads:
            self.fail_uploads -= 1
            raise ConnectionError("storage unavailable")
        self.uploads.append({"name": name, **kwargs})
        self.blobs[name] = data

    async def download_blob(self, name):
        if name not in self.blobs:
            raise ResourceNotFoundError("BlobNotFound")
        return FakeDownload(self.blobs[name])


def test_blob_name_follows_plan_layout() -> None:
    assert (
        transcript_blob_name(document())
        == "2026/11/cust_demo_001/session_abc123/transcript.json"
    )


def test_blob_name_uses_utc_start_date() -> None:
    # 1 Dec 02:00 in India is still 30 Nov in UTC.
    ist = timezone(timedelta(hours=5, minutes=30))
    started = datetime(2026, 12, 1, 2, 0, tzinfo=ist)
    assert transcript_blob_name(document(started_at=started)).startswith("2026/11/")


@pytest.mark.parametrize("field", ["customer_id", "session_id"])
@pytest.mark.parametrize("value", ["../etc", "a/b", "", "x" * 65, "café"])
def test_blob_name_rejects_unsafe_segments(field, value) -> None:
    with pytest.raises(ValueError):
        transcript_blob_name(document(**{field: value}))


async def test_blob_save_uploads_private_json_with_metadata() -> None:
    container = FakeContainer()
    storage = BlobTranscriptStorage(container)

    reference = await storage.save(document())

    assert storage.kind == "azure_blob"
    assert reference == "2026/11/cust_demo_001/session_abc123/transcript.json"
    [upload] = container.uploads
    assert upload["overwrite"] is True  # retried saves are idempotent
    assert upload["content_settings"].content_type == "application/json; charset=utf-8"
    assert upload["metadata"] == {
        "schema_version": "1.0",
        "session_status": "completed",
        "outcome": "payment_promised",
    }


async def test_blob_round_trip_matches_plan_schema() -> None:
    storage = BlobTranscriptStorage(FakeContainer())
    reference = await storage.save(document())

    loaded = await storage.load(reference)
    assert loaded == document()
    raw = loaded.model_dump(mode="json")
    assert raw["schema_version"] == "1.0"
    assert raw["promised_payment_date"] == "2026-11-20"
    assert [t["speaker"] for t in raw["transcript"]] == ["agent", "user"]


async def test_blob_load_missing_returns_none() -> None:
    storage = BlobTranscriptStorage(FakeContainer())
    assert await storage.load("2026/11/cust_x/session_y/transcript.json") is None


@pytest.mark.parametrize(
    "reference", ["../secret.json", "2026/11/a/b/other.json", "https://x/y"]
)
async def test_blob_load_rejects_non_transcript_names(reference) -> None:
    with pytest.raises(ValueError):
        await BlobTranscriptStorage(FakeContainer()).load(reference)


async def test_blob_upload_failure_propagates() -> None:
    storage = BlobTranscriptStorage(FakeContainer(fail_uploads=1))
    with pytest.raises(ConnectionError):
        await storage.save(document())


async def test_in_memory_storage_uses_same_layout() -> None:
    storage = InMemoryTranscriptStorage()
    reference = await storage.save(document())
    assert reference == "2026/11/cust_demo_001/session_abc123/transcript.json"
    assert await storage.load(reference) == document()
    assert storage.kind == "memory"


async def test_close_calls_hook() -> None:
    closed = []

    async def on_close() -> None:
        closed.append(True)

    await BlobTranscriptStorage(FakeContainer(), on_close=on_close).close()
    assert closed == [True]
