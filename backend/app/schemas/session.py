"""Schemas for voice sessions, transcripts and the sequential customer queue."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.customer import Customer

SessionStatus = Literal["active", "completed", "failed"]
FinalSessionStatus = Literal["completed", "failed"]
Speaker = Literal["agent", "user"]


class SessionOutcome(StrEnum):
    PAYMENT_PROMISED = "payment_promised"
    REPORTED_PAID = "reported_paid"
    CALLBACK_REQUESTED = "callback_requested"
    DISPUTED_INVOICE = "disputed_invoice"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    STOP_REQUESTED = "stop_requested"
    COMPLETED_NO_COMMITMENT = "completed_no_commitment"
    TECHNICAL_FAILURE = "technical_failure"


ReviewFlagKind = Literal[
    "sensitive_data_request",  # agent appeared to ask for OTP/PIN/CVV/card/password
    "unverified_amount",  # agent stated an amount not in the customer record
    "unverified_invoice",  # agent stated an invoice number not in the record
    "unverified_date",  # agent stated a date not in the record/conversation
    "pressure_or_policy",  # agent mentioned fees, penalties, legal action, etc.
    "sensitive_data_shared",  # customer said/typed a card number or code (redacted)
]


class ReviewFlag(BaseModel):
    """Heuristic screening result for the operator to review (not proof of a problem)."""

    kind: ReviewFlagKind
    speaker: Literal["agent", "user"]
    detail: str
    excerpt: str


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: int | None = Field(default=None, ge=1)
    speaker: Speaker
    text: str = Field(min_length=1, max_length=5000)
    timestamp: AwareDatetime


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional optimistic-concurrency check: the customer the UI is showing.
    # If the queue has moved on, the request is rejected instead of starting
    # a session for a customer the operator has not seen.
    expected_customer_id: str | None = None


class FinalizeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_status: FinalSessionStatus
    outcome: SessionOutcome | None = None
    promised_payment_date: date | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list, max_length=1000)
    summary: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _check_outcome(self) -> "FinalizeSessionRequest":
        if (
            self.promised_payment_date is not None
            and self.outcome != SessionOutcome.PAYMENT_PROMISED
        ):
            raise ValueError(
                "promised_payment_date is only allowed when outcome is payment_promised"
            )
        if self.session_status == "failed" and self.outcome not in (
            None,
            SessionOutcome.TECHNICAL_FAILURE,
        ):
            raise ValueError("a failed session can only have outcome technical_failure")
        if (
            self.session_status == "completed"
            and self.outcome == SessionOutcome.TECHNICAL_FAILURE
        ):
            raise ValueError("outcome technical_failure requires session_status failed")
        return self


class SessionRecord(BaseModel):
    session_id: str
    customer_id: str
    invoice_id: str
    sequence_index: int = Field(
        description="0-based position of the customer in the JSON file"
    )
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    outcome: SessionOutcome | None = None
    promised_payment_date: date | None = None
    transcript_ref: str | None = Field(
        default=None,
        description="Storage reference of the saved transcript (blob name for Azure Blob)",
    )
    transcript_store: str | None = Field(
        default=None,
        description='Where the transcript was saved: "azure_blob" or "memory"',
    )
    review_flag_count: int = 0


class TranscriptDocument(BaseModel):
    """The transcript JSON that is persisted for each finalized session."""

    schema_version: Literal["1.0"] = "1.0"
    session_id: str
    customer_id: str
    invoice_id: str
    started_at: datetime
    ended_at: datetime
    session_status: FinalSessionStatus
    outcome: SessionOutcome
    promised_payment_date: date | None = None
    transcript: list[TranscriptTurn]
    summary: str | None = None
    # Optional addition to schema 1.0: heuristic screening results for review.
    review_flags: list[ReviewFlag] = Field(default_factory=list)


class QueueState(BaseModel):
    total: int
    current_index: int = Field(
        description="0-based; equals total once every record is processed"
    )
    position: int | None = Field(
        description="1-based position for display; null when completed"
    )
    remaining: int
    completed: bool
    current_customer: Customer | None
    active_session_id: str | None


class FinalizeSessionResponse(BaseModel):
    session: SessionRecord
    queue: QueueState
    advanced: bool
    already_finalized: bool


class OutcomeProposal(BaseModel):
    """An outcome the model recorded via its tool, after backend validation."""

    outcome: SessionOutcome
    promised_payment_date: date | None = None
    summary: str | None = None
    proposed_at: datetime


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Typed customer/operator message. Omit (null) to ask the agent for its
    # opening line, which is only allowed before any turn exists.
    text: str | None = Field(default=None, min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    agent_turn: TranscriptTurn
    transcript: list[TranscriptTurn]
    proposed_outcome: OutcomeProposal | None
    review_flags: list[ReviewFlag] = Field(default_factory=list)
