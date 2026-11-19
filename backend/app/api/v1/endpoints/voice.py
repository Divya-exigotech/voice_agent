import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status

from app.api.deps import ConversationOpener, get_conversation_opener, get_sequence_service
from app.core.config import settings
from app.schemas.transcript import TranscriptTurn
from app.services.foundry_session import (
    AgentSessionError,
    FoundryNotConfiguredError,
    describe_connection_failure,
)
from app.services.sequence_service import (
    SequenceService,
    SessionNotFoundError,
    StreamUnavailableError,
)
from app.services.voice_relay import VoiceRelay

logger = logging.getLogger(__name__)
router = APIRouter()
Service = Annotated[SequenceService, Depends(get_sequence_service)]
Opener = Annotated[ConversationOpener, Depends(get_conversation_opener)]


async def _fail(
    websocket: WebSocket, message: str, code: int = status.WS_1011_INTERNAL_ERROR
) -> None:
    with contextlib.suppress(RuntimeError):
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close(code=code)


@router.websocket("/ws/sessions/{session_id}")
async def voice_session(
    websocket: WebSocket, session_id: str, service: Service, opener: Opener
) -> None:
    # Browsers do not apply CORS to WebSockets, so check the Origin ourselves.
    if websocket.headers.get("origin") not in settings.CORS_ORIGINS:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()

    try:
        customer = await service.open_stream(session_id)
    except (SessionNotFoundError, StreamUnavailableError):
        await _fail(websocket, "Session is not available", status.WS_1008_POLICY_VIOLATION)
        return

    turns: list[TranscriptTurn] = []
    conversation_id: str | None = None
    try:
        async with opener(customer) as conversation:
            try:
                await VoiceRelay(
                    websocket,
                    conversation,
                    customer,
                    turns=turns,
                    max_seconds=settings.VOICE_SESSION_MAX_SECONDS,
                    request_greeting=settings.AZURE_AI_REQUEST_GREETING,
                ).run()
            finally:
                conversation_id = conversation.conversation_id
    except FoundryNotConfiguredError:
        await _fail(websocket, "The voice agent is not configured")
    except TimeoutError:
        await _fail(websocket, "Session time limit reached", status.WS_1000_NORMAL_CLOSURE)
    except AgentSessionError:
        await _fail(websocket, "The voice session ended with an error")
    except Exception as exc:  # report a short, non-sensitive reason; never leak details
        reason = describe_connection_failure(exc)
        logger.warning("Voice session failed (%s): %s", type(exc).__name__, reason)
        await _fail(websocket, reason)
    finally:
        await service.close_stream(session_id, turns, conversation_id)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


@router.get("/sessions/{session_id}/transcript")
async def get_transcript(session_id: str, service: Service) -> list[TranscriptTurn]:
    try:
        return (await service.get_session(session_id)).transcript
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
