"""Backend validation of outcomes proposed by the model.

Model output is never authoritative: tool-call arguments are parsed and checked
here, and only valid proposals are stored on the session.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.schemas.session import OutcomeProposal, SessionOutcome
from app.services.agent_prompt import MODEL_OUTCOMES

MAX_PROMISE_DAYS_AHEAD = 90
MAX_SUMMARY_LENGTH = 500


@dataclass(frozen=True)
class ValidationResult:
    proposal: OutcomeProposal | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.proposal is not None


def _fail(message: str) -> ValidationResult:
    return ValidationResult(proposal=None, error=message)


def validate_outcome_arguments(
    arguments: str | dict, *, today: date, now: datetime
) -> ValidationResult:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return _fail("arguments must be a JSON object")
    if not isinstance(arguments, dict):
        return _fail("arguments must be a JSON object")

    raw_outcome = arguments.get("outcome")
    allowed = {o.value for o in MODEL_OUTCOMES}
    if not isinstance(raw_outcome, str) or raw_outcome not in allowed:
        return _fail(f"outcome must be one of: {', '.join(sorted(allowed))}")
    outcome = SessionOutcome(raw_outcome)

    promised: date | None = None
    raw_date = arguments.get("promised_payment_date")
    if raw_date not in (None, ""):
        if outcome is not SessionOutcome.PAYMENT_PROMISED:
            return _fail("promised_payment_date is only allowed with payment_promised")
        if not isinstance(raw_date, str):
            return _fail("promised_payment_date must be a YYYY-MM-DD string")
        try:
            promised = date.fromisoformat(raw_date)
        except ValueError:
            return _fail("promised_payment_date must be a valid YYYY-MM-DD date")
        if promised < today:
            return _fail(
                f"promised_payment_date {promised.isoformat()} is in the past "
                f"(today is {today.isoformat()}); confirm the date with the customer"
            )
        if promised > today + timedelta(days=MAX_PROMISE_DAYS_AHEAD):
            return _fail(
                f"promised_payment_date is more than {MAX_PROMISE_DAYS_AHEAD} days away; "
                "record needs_human_review or confirm a nearer date"
            )

    summary = arguments.get("summary")
    if summary is not None:
        if not isinstance(summary, str):
            return _fail("summary must be a string")
        summary = summary.strip()[:MAX_SUMMARY_LENGTH] or None

    end_call = arguments.get("end_call", False)
    if end_call is None:
        end_call = False
    if not isinstance(end_call, bool):
        return _fail("end_call must be true or false")

    return ValidationResult(
        proposal=OutcomeProposal(
            outcome=outcome,
            promised_payment_date=promised,
            summary=summary,
            proposed_at=now,
            end_call=end_call,
        ),
        error=None,
    )
