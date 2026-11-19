from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, model_validator


class Outcome(StrEnum):
    PAYMENT_PROMISED = "payment_promised"
    REPORTED_PAID = "reported_paid"
    CALLBACK_REQUESTED = "callback_requested"
    DISPUTED_INVOICE = "disputed_invoice"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    STOP_REQUESTED = "stop_requested"
    COMPLETED_NO_COMMITMENT = "completed_no_commitment"
    TECHNICAL_FAILURE = "technical_failure"


class SessionOutcome(BaseModel):
    outcome: Outcome
    promised_payment_date: date | None = None

    @model_validator(mode="after")
    def _date_only_with_promise(self) -> Self:
        if self.promised_payment_date is not None and self.outcome is not Outcome.PAYMENT_PROMISED:
            msg = "promised_payment_date is only valid for payment_promised"
            raise ValueError(msg)
        return self
