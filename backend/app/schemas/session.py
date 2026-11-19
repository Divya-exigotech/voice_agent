from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from app.schemas.customer import Customer
from app.schemas.transcript import TranscriptTurn


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class SessionRecord(BaseModel):
    session_id: str
    customer_id: str
    invoice_id: str
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    conversation_id: str | None = None
    transcript: list[TranscriptTurn] = []


class QueueState(BaseModel):
    """Position of the sequential customer queue (current_position is 1-based)."""

    total: int
    current_index: int
    current_position: int | None
    remaining: int
    all_completed: bool
    current_customer: Customer | None
    active_session_id: str | None
