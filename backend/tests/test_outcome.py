from datetime import date

import pytest

from app.schemas.outcome import Outcome
from app.services.foundry_session import AgentConversation
from app.services.outcome import OutcomeValidationError, parse_outcome_text, resolve_outcome
from tests.test_foundry_session import FakeConnection

TODAY = date(2026, 11, 18)


def test_valid_promise_with_date() -> None:
    text = 'Sure: {"outcome": "payment_promised", "promised_payment_date": "2026-11-20"}'
    parsed = parse_outcome_text(text, today=TODAY)
    assert parsed.outcome is Outcome.PAYMENT_PROMISED
    assert parsed.promised_payment_date == date(2026, 11, 20)


def test_valid_outcome_without_date() -> None:
    parsed = parse_outcome_text('{"outcome": "callback_requested"}', today=TODAY)
    assert parsed.promised_payment_date is None


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        "{not json}",
        '{"outcome": "made_up"}',
        '{"outcome": "reported_paid", "promised_payment_date": "2026-11-20"}',
        '{"outcome": "payment_promised", "promised_payment_date": "2026-11-01"}',
        '{"outcome": "payment_promised", "promised_payment_date": "not-a-date"}',
        '{"outcome": "technical_failure"}',
    ],
)
def test_invalid_outcome_rejected(text: str) -> None:
    with pytest.raises(OutcomeValidationError):
        parse_outcome_text(text, today=TODAY)


@pytest.mark.anyio
async def test_resolve_falls_back_to_human_review_on_garbage() -> None:
    events = [
        {"type": "response.output_text.done", "text": "I think they will pay"},
        {"type": "response.done"},
    ]
    outcome = await resolve_outcome(AgentConversation(FakeConnection(events)))
    assert outcome.outcome is Outcome.NEEDS_HUMAN_REVIEW


@pytest.mark.anyio
async def test_resolve_technical_failure_on_session_error() -> None:
    events = [{"type": "error", "error": {"message": "x"}}]
    outcome = await resolve_outcome(AgentConversation(FakeConnection(events)))
    assert outcome.outcome is Outcome.TECHNICAL_FAILURE
