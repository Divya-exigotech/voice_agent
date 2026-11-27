"""FastAPI application factory.

Run locally with:
    uvicorn app.main:create_app --factory --reload --port 8000
"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import api_router, auth
from app.config import Settings, get_settings
from app.schemas.session import TranscriptTurn
from app.services import latency
from app.services.auth_store import AuthStore
from app.services.azure_auth import build_async_credential
from app.services.conversation import ConversationService
from app.services.customer_repository import load_customers
from app.services.database import (
    LiveTranscriptWriter,
    PostgresStore,
    PostgresTranscriptStorage,
)
from app.services.followup import FollowUpScheduler
from app.services.monitoring import MonitoringStore
from app.services.session_manager import SessionManager
from app.services.transcript_storage import (
    BlobTranscriptStorage,
    InMemoryTranscriptStorage,
    TranscriptStorage,
)
from app.services.voice_agent import AgentConnector, FoundryAgentConnector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OAuth callback query values (authorization code, state) must not reach logs.
_SECRET_QUERY_VALUE = re.compile(r"(?i)\b(code|state|session_state)=[^&\s\"]*")


class RedactSecretQueryFilter(logging.Filter):
    """Redact sign-in codes from uvicorn's access log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SECRET_QUERY_VALUE.sub(r"\1=[redacted]", arg)
                if isinstance(arg, str)
                else arg
                for arg in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(RedactSecretQueryFilter())

_FROM_SETTINGS = object()
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


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


async def build_transcript_storage(
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
            else build_async_credential(settings),
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
    auth_store: AuthStore | None = None,
) -> FastAPI:
    """Build the app. Tests inject `storage`, `connector`, `today` and `database`.

    The database, storage and services are created in the lifespan (startup),
    because opening the async connection pool and loading the queue are awaited.
    """
    settings = settings or get_settings()
    configured_jwt_secret = (
        settings.auth_jwt_secret.get_secret_value().encode("utf-8")
        if settings.auth_jwt_secret is not None
        else None
    )
    user_store = auth_store or AuthStore(
        settings.auth_database_path, signing_key=configured_jwt_secret
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auth_enabled:
            await user_store.initialize()
        db: PostgresStore | None = (
            (
                await PostgresStore.from_settings(settings)
                if settings.database_configured
                else None
            )
            if database is _FROM_SETTINGS
            else database  # type: ignore[assignment]
        )
        try:
            if db is not None:
                # The queue comes from call_tasks; sessions are calls rows (uuid ids).
                await db.recover_interrupted_sessions()
                customers = await db.load_queue()
                manager_options: dict[str, Any] = {
                    "id_factory": lambda: str(uuid.uuid4()),
                    "on_session_start": db.session_started,
                    "allow_reset": False,
                }
            else:
                # Fail fast at startup if the customer file is missing or invalid.
                customers = await load_customers(settings.customers_json_path)
                manager_options = {}
            transcript_storage = storage or await build_transcript_storage(settings, db)
        except Exception:
            if db is not None:
                await db.close()
            raise

        agent_connector = (
            build_agent_connector(settings)
            if connector is _FROM_SETTINGS
            else connector
        )
        business_tz = ZoneInfo(settings.business_timezone)
        manager = SessionManager(customers, transcript_storage, **manager_options)
        live_writer: LiveTranscriptWriter | None = None
        if db is not None:
            # Turns are saved to call_transcripts as they happen, not only at the end.
            async def current_turns(session_id: str) -> Sequence[TranscriptTurn]:
                return (await manager.get_conversation(session_id)).turns

            live_writer = LiveTranscriptWriter(db, current_turns)
            manager.on_turns_changed = live_writer.notify
        conversation = ConversationService(
            manager,
            agent_connector,  # type: ignore[arg-type]
            organization=settings.agent_organization_name,
            today=today or (lambda: datetime.now(business_tz).date()),
            timeout_seconds=settings.azure_ai_agent_timeout_seconds,
        )

        # Read-only async reader for the live monitoring pages (needs the database).
        monitoring: MonitoringStore | None = (
            MonitoringStore.from_settings(settings, db.organization_id)
            if isinstance(db, PostgresStore)
            else None
        )

        followups = FollowUpScheduler(
            manager,
            db,
            delay_seconds=settings.followup_delay_seconds,
            business_timezone=business_tz,
            call_hour=settings.followup_call_hour,
            poll_seconds=settings.followup_poll_seconds,
        )

        app.state.session_manager = manager
        app.state.conversation_service = conversation
        app.state.followup_scheduler = followups
        app.state.monitoring = monitoring
        app.state.database = db

        if live_writer is not None:
            await live_writer.start()
        await followups.start()
        if monitoring is not None:
            await monitoring.open()
        yield
        if monitoring is not None:
            await monitoring.aclose()
        await followups.aclose()
        if live_writer is not None:
            await live_writer.aclose()
        await conversation.close_all()
        if agent_connector is not None:
            await agent_connector.aclose()  # type: ignore[union-attr]
        await transcript_storage.close()
        if db is not None:
            await db.close()

    app = FastAPI(
        title="Payment Follow-up Agent PoC", version="0.2.0", lifespan=lifespan
    )
    app.state.settings = settings
    app.state.auth_store = user_store
    app.include_router(api_router)

    @app.middleware("http")
    async def log_request_latency(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Any:
        started = time.monotonic()
        path = request.url.path
        public_api_paths = {
            "/api/auth/microsoft/login",
            "/api/auth/microsoft/callback",
            "/api/auth/session",
            "/api/auth/logout",
            "/api/health",
        }
        if (
            settings.auth_enabled
            and path.startswith("/api/")
            and path not in public_api_paths
        ):
            if (
                request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and request.cookies.get(auth.ACCESS_TOKEN_COOKIE)
                and not auth.is_allowed_origin(request)
            ):
                response = JSONResponse(
                    {"detail": "Request origin is not allowed."}, status_code=403
                )
            else:
                user = await auth.user_from_request(request)
                if user is None:
                    response = JSONResponse(
                        {"detail": "Authentication required."}, status_code=401
                    )
                else:
                    request.state.user = user
                    response = await call_next(request)
        else:
            response = await call_next(request)
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info(
            "latency http_ms=%d %s %s -> %d",
            elapsed_ms,
            request.method,
            request.url.path,
            response.status_code,
        )
        # The dashboard's own polling is left out so it does not drown real traffic.
        if not request.url.path.startswith("/api/monitoring"):
            path = _UUID_RE.sub("{id}", request.url.path)
            latency.recorder.record(
                latency.HTTP, elapsed_ms, label=f"{request.method} {path}"
            )
        return response

    # /api/health is reachable through the Next.js /api proxy for the dashboard.
    @app.get("/health", tags=["health"])
    @app.get("/api/health", tags=["health"])
    async def health(request: Request) -> dict[str, object]:
        state = request.app.state
        return {
            "status": "ok",
            "agent_configured": state.conversation_service.configured,
            "transcript_storage": state.session_manager.storage_kind,
            "customer_source": "postgres" if state.database is not None else "json",
        }

    return app
