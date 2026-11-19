"""Validate the session outcome. Model output is advisory; the backend decides."""

import json
from datetime import UTC, date, datetime

from pydantic import ValidationError

from app.schemas.outcome import Outcome, SessionOutcome
from app.services.foundry_session import AgentConversation, AgentSessionError

OUTCOME_INSTRUCTION = (
    "The conversation is over. Reply with ONLY a JSON object and no other text, in the form "
    '{"outcome": "<one of: payment_promised, reported_paid, callback_requested, '
    'disputed_invoice, needs_human_review, stop_requested, completed_no_commitment>", '
    '"promised_payment_date": "<YYYY-MM-DD or null>"}. '
    "Use payment_promised only if the customer gave a payment date. "
    "reported_paid means the customer said they paid; it is not verified."
)


class OutcomeValidationError(ValueError):
    """The model's outcome text was missing, malformed or inconsistent."""


def parse_outcome_text(text: str, *, today: date | None = None) -> SessionOutcome:
    """Extract and validate the first JSON object in ``text``."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start == -1:
        msg = "No JSON object in outcome text"
        raise OutcomeValidationError(msg)
    try:
        raw, _ = decoder.raw_decode(text[start:])
        parsed = SessionOutcome.model_validate(raw)
    except (json.JSONDecodeError, ValidationError):
        msg = "Outcome text failed validation"
        raise OutcomeValidationError(msg) from None

    today = today or datetime.now(UTC).date()
    if parsed.promised_payment_date is not None and parsed.promised_payment_date < today:
        msg = "promised_payment_date is in the past"
        raise OutcomeValidationError(msg)
    if parsed.outcome is Outcome.TECHNICAL_FAILURE:
        msg = "technical_failure is set by the backend, not the model"
        raise OutcomeValidationError(msg)
    return parsed


async def resolve_outcome(conversation: AgentConversation) -> SessionOutcome:
    """Ask the agent for an outcome; fall back to human review if it is not valid."""
    try:
        turn = await conversation.ask_system(OUTCOME_INSTRUCTION)
        return parse_outcome_text(turn.agent_text)
    except OutcomeValidationError:
        return SessionOutcome(outcome=Outcome.NEEDS_HUMAN_REVIEW)
    except AgentSessionError:
        return SessionOutcome(outcome=Outcome.TECHNICAL_FAILURE)
