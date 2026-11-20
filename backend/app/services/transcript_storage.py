"""Transcript persistence behind a small interface.

* `BlobTranscriptStorage` (M4): private Azure Blob container, Microsoft Entra ID
  auth (DefaultAzureCredential / managed identity), one JSON document per
  session at `{yyyy}/{mm}/{customer_id}/{session_id}/transcript.json`.
* `InMemoryTranscriptStorage`: local development and tests (lost on restart).

Both are synchronous (blocking I/O); async callers run them in a worker thread.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC
from typing import Any, Protocol

from app.schemas.session import TranscriptDocument

logger = logging.getLogger(__name__)

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BLOB_NAME = re.compile(r"^\d{4}/\d{2}/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}/transcript\.json$")


class TranscriptStorage(Protocol):
    kind: str

    def save(self, document: TranscriptDocument) -> str:
        """Persist the document and return its storage reference."""
        ...

    def load(self, reference: str) -> TranscriptDocument | None: ...

    def close(self) -> None: ...


def transcript_blob_name(document: TranscriptDocument) -> str:
    """`{yyyy}/{mm}/{customer_id}/{session_id}/transcript.json`, dated by session start (UTC)."""
    for label, value in (("customer_id", document.customer_id), ("session_id", document.session_id)):
        if not _SAFE_SEGMENT.match(value):
            raise ValueError(f"{label} {value!r} is not safe to use in a blob path")
    started = document.started_at.astimezone(UTC)
    return f"{started:%Y}/{started:%m}/{document.customer_id}/{document.session_id}/transcript.json"


class InMemoryTranscriptStorage:
    kind = "memory"

    def __init__(self) -> None:
        self._documents: dict[str, TranscriptDocument] = {}

    def save(self, document: TranscriptDocument) -> str:
        reference = transcript_blob_name(document)
        self._documents[reference] = document
        return reference

    def load(self, reference: str) -> TranscriptDocument | None:
        return self._documents.get(reference)

    def close(self) -> None:
        pass


class BlobTranscriptStorage:
    """Transcripts as private block blobs in an Azure Storage container."""

    kind = "azure_blob"

    def __init__(self, container_client: Any, *, on_close: Any = None) -> None:
        self._container = container_client
        self._on_close = on_close

    @property
    def container_name(self) -> str:
        return self._container.container_name

    @classmethod
    def from_settings(
        cls, *, account_url: str, container: str, credential: Any = None
    ) -> BlobTranscriptStorage:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        owned_credential = credential is None
        credential = credential or DefaultAzureCredential()
        service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
            # Fail fast enough for the operator to retry End Session.
            retry_total=3,
            connection_timeout=10,
            read_timeout=30,
        )

        def close() -> None:
            service.close()
            if owned_credential:
                credential.close()

        return cls(service.get_container_client(container), on_close=close)

    def save(self, document: TranscriptDocument) -> str:
        from azure.storage.blob import ContentSettings

        name = transcript_blob_name(document)
        # overwrite=True keeps a retried save idempotent (same session, same path).
        self._container.upload_blob(
            name=name,
            data=document.model_dump_json(indent=2).encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json; charset=utf-8"),
            metadata={
                "schema_version": document.schema_version,
                "session_status": document.session_status,
                "outcome": document.outcome.value,
            },
        )
        logger.info("Saved transcript to blob %s/%s", self.container_name, name)
        return name

    def load(self, reference: str) -> TranscriptDocument | None:
        from azure.core.exceptions import ResourceNotFoundError

        if not _BLOB_NAME.match(reference):
            raise ValueError(f"Not a transcript blob name: {reference!r}")
        try:
            data = self._container.download_blob(reference).readall()
        except ResourceNotFoundError:
            return None
        return TranscriptDocument.model_validate_json(data)

    def close(self) -> None:
        if self._on_close is not None:
            self._on_close()
