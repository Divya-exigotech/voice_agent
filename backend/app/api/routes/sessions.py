from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import ConversationServiceDep, FollowUpSchedulerDep, SessionManagerDep
from app.schemas.session import (
    CreateSessionRequest,
    FinalizeSessionRequest,
    FinalizeSessionResponse,
    SessionRecord,
    TranscriptDocument,
)
from app.services.customer_queue import QueueError
from app.services.session_manager import (
    FinalizeInProgressError,
    SessionNotFoundError,
    SessionStartError,
    TranscriptLoadError,
    TranscriptNotSavedError,
    TranscriptSaveError,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionRecord, status_code=status.HTTP_201_CREATED)
def create_session(  # sync: database I/O runs in FastAPI's threadpool
    manager: SessionManagerDep, body: CreateSessionRequest | None = None
) -> SessionRecord:
    """Start a session for the current customer in the queue (no selection)."""
    expected = body.expected_customer_id if body else None
    try:
        return manager.start_session(expected_customer_id=expected)
    except QueueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except SessionStartError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"The session could not be recorded ({exc}); the queue did not move, please retry",
        ) from exc


@router.get("", response_model=list[SessionRecord])
def list_sessions(manager: SessionManagerDep) -> list[SessionRecord]:
    return manager.list_sessions()


@router.post("/{session_id}/transcript", response_model=FinalizeSessionResponse)
async def finalize_session(
    session_id: str,
    body: FinalizeSessionRequest,
    manager: SessionManagerDep,
    conversation: ConversationServiceDep,
    followups: FollowUpSchedulerDep,
) -> FinalizeSessionResponse:
    """Save the transcript to storage, then finalize and advance (idempotent)."""
    try:
        # Blob upload is blocking I/O: keep it off the event loop (voice relay).
        result = await run_in_threadpool(manager.finalize_session, session_id, body)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc
    except FinalizeInProgressError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The transcript for this session is already being saved"
        ) from exc
    except TranscriptSaveError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Transcript could not be saved ({exc}); the session is still open, please retry",
        ) from exc
    if result.session.status != "active":
        # Session is final: hang up the live voice agent connection.
        await conversation.close(session_id)
    if not result.already_finalized:
        followups.schedule(result.session)
    return FinalizeSessionResponse(
        session=result.session,
        queue=manager.queue_state(),
        advanced=result.advanced,
        already_finalized=result.already_finalized,
    )


@router.get("/{session_id}/transcript", response_model=TranscriptDocument)
def get_transcript(session_id: str, manager: SessionManagerDep) -> TranscriptDocument:
    """Read a saved transcript back from storage through the backend.

    Only sessions this backend created can be read, by session id; blob names
    and storage URLs are never accepted from the client, and the container
    itself stays private.
    """
    try:
        return manager.load_transcript(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc
    except TranscriptNotSavedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The session is still active; no transcript saved yet"
        ) from exc
    except TranscriptLoadError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Transcript could not be read: {exc}"
        ) from exc
