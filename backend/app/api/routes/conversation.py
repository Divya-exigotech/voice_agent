from fastapi import APIRouter, HTTPException, status

from app.api.deps import ConversationServiceDep
from app.schemas.session import ChatRequest, ChatResponse
from app.services.conversation import (
    AgentBusyError,
    AgentNotConfiguredError,
    ConversationStateError,
)
from app.services.session_manager import SessionNotActiveError, SessionNotFoundError
from app.services.voice_agent import AgentError

router = APIRouter(prefix="/sessions", tags=["conversation"])


@router.post("/{session_id}/messages", response_model=ChatResponse)
async def send_message(
    session_id: str, body: ChatRequest, service: ConversationServiceDep
) -> ChatResponse:
    """Typed turn with the Foundry voice agent. `text: null` asks for the opening line."""
    try:
        result = await service.send(session_id, body.text)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc
    except SessionNotActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session has already ended") from exc
    except (AgentBusyError, ConversationStateError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except AgentNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except AgentError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Voice agent error: {exc}") from exc
    return ChatResponse(
        agent_turn=result.agent_turn,
        transcript=list(result.transcript),
        proposed_outcome=result.proposal,
        review_flags=list(result.flags),
    )
