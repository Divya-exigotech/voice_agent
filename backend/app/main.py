"""FastAPI application factory.

Run locally with:
    uvicorn app.main:create_app --factory --reload --port 8000
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from app.api.routes import api_router
from app.config import Settings, get_settings
from app.services.azure_auth import build_async_credential, build_credential
from app.services.conversation import ConversationService
from app.services.customer_repository import load_customers
from app.services.database import PostgresStore, PostgresTranscriptStorage
from app.services.session_manager import SessionManager
from app.services.transcript_storage import (
    BlobTranscriptStorage,
    InMemoryTranscriptStorage,
    TranscriptStorage,
)
from app.services.voice_agent import AgentConnector, FoundryAgentConnector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_FROM_SETTINGS = object()


def build_agent_connector(settings: Settings) -> AgentConnector | None:
    if not settings.agent_configured:
        logger.warning(
            "Foundry voice agent is not configured; chat endpoints will return 503"
        )
        return None
    logger.info("Azure sign-in method: %s", settings.azure_auth_method)
    return FoundryAgentConnector(
        project_endpoint=settings.azure_ai_project_endpoint or "",
        agent_name=settings.azure_ai_agent_name or "",
        credential_factory=lambda: build_async_credential(settings),
    )


def build_transcript_storage(
    settings: Settings, database: PostgresStore | None = None
) -> TranscriptStorage:
    blob: BlobTranscriptStorage | None = None
    if settings.azure_storage_account_url:
        logger.info(
            "Transcripts are saved to Azure Blob container %s at %s",
            settings.azure_storage_container,
            settings.azure_storage_account_url,
        )
        blob = BlobTranscriptStorage.from_settings(
            account_url=settings.azure_storage_account_url,
            container=settings.azure_storage_container,
            # None keeps the storage's own DefaultAzureCredential (closed with it).
            credential=None
            if settings.azure_auth_method == "default"
            else build_credential(settings),
        )
    if database is not None:
        logger.info("Sessions, outcomes and transcript turns are saved to PostgreSQL")
        return PostgresTranscriptStorage(database, blob)
    if blob is not None:
        return blob
    logger.warning(
        "Neither DATABASE_URL nor AZURE_STORAGE_ACCOUNT_URL is set; "
        "transcripts are kept in memory only"
    )
    return InMemoryTranscriptStorage()


def create_app(
    settings: Settings | None = None,
    storage: TranscriptStorage | None = None,
    connector: AgentConnector | None | object = _FROM_SETTINGS,
    today: Callable[[], date] | None = None,
    database: PostgresStore | None | object = _FROM_SETTINGS,
) -> FastAPI:
    """Build the app. Tests inject `storage`, `connector`, `today` and `database`."""
    settings = settings or get_settings()

    db: PostgresStore | None = (
        (PostgresStore.from_settings(settings) if settings.database_configured else None)
        if database is _FROM_SETTINGS
        else database  # type: ignore[assignment]
    )
    try:
        if db is not None:
            # The queue comes from call_tasks; sessions are calls rows (uuid ids).
            db.recover_interrupted_sessions()
            customers = db.load_queue()
            manager_options: dict[str, Any] = {
                "id_factory": lambda: str(uuid.uuid4()),
                "on_session_start": db.session_started,
                "allow_reset": False,
            }
        else:
            # Fail fast at startup if the customer file is missing or invalid.
            customers = load_customers(settings.customers_json_path)
            manager_options = {}
        transcript_storage = storage or build_transcript_storage(settings, db)
    except Exception:
        if db is not None:
            db.close()
        raise

    agent_connector = (
        build_agent_connector(settings) if connector is _FROM_SETTINGS else connector
    )
    business_tz = ZoneInfo(settings.business_timezone)
    manager = SessionManager(customers, transcript_storage, **manager_options)
    conversation = ConversationService(
        manager,
        agent_connector,  # type: ignore[arg-type]
        organization=settings.agent_organization_name,
        today=today or (lambda: datetime.now(business_tz).date()),
        timeout_seconds=settings.azure_ai_agent_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await conversation.close_all()
        if agent_connector is not None:
            await agent_connector.aclose()  # type: ignore[union-attr]
        transcript_storage.close()
        if db is not None:
            db.close()

    app = FastAPI(title="Payment Follow-up Agent PoC", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_manager = manager
    app.state.conversation_service = conversation
    app.include_router(api_router)

    # /api/health is reachable through the Next.js /api proxy for the dashboard.
    @app.get("/health", tags=["health"])
    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "agent_configured": conversation.configured,
            "transcript_storage": manager.storage_kind,
            "customer_source": "postgres" if db is not None else "json",
        }

    return app
