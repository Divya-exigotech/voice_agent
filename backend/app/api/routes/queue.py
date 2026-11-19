from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionManagerDep
from app.schemas.session import QueueState
from app.services.customer_queue import QueueError

router = APIRouter(tags=["queue"])


@router.get("/queue", response_model=QueueState)
def get_queue(manager: SessionManagerDep) -> QueueState:
    """Current position in the sequential customer queue."""
    return manager.queue_state()


@router.post("/queue/reset", response_model=QueueState)
def reset_queue(manager: SessionManagerDep) -> QueueState:
    """Demo helper: restart the queue from the first customer."""
    try:
        return manager.reset_queue()
    except QueueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
