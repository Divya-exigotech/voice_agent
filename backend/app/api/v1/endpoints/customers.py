from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_sequence_service
from app.schemas.customer import Customer
from app.schemas.session import QueueState, SessionRecord
from app.services.sequence_service import (
    QueueCompletedError,
    SequenceService,
    SessionActiveError,
    SessionNotFoundError,
)

router = APIRouter()
Service = Annotated[SequenceService, Depends(get_sequence_service)]


@router.get("/customers")
async def list_customers(service: Service) -> list[Customer]:
    return await service.list_customers()


@router.get("/queue")
async def get_queue(service: Service) -> QueueState:
    return await service.state()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(service: Service) -> SessionRecord:
    try:
        return await service.start_session()
    except QueueCompletedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "All customers completed") from None
    except SessionActiveError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A session is already active") from None


@router.post("/sessions/{session_id}/finalize")
async def finalize_session(session_id: str, service: Service) -> QueueState:
    try:
        return await service.finalize_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
