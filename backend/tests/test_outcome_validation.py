import json
from datetime import UTC, date, datetime

import pytest

from app.schemas.session import SessionOutcome
from app.services.outcome_validation import validate_outcome_arguments

TODAY = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def validate(args):
    return validate_outcome_arguments(args, today=TODAY, now=NOW)


@pytest.mark.parametrize(
    "outcome",
    [
        "payment_promised",
        "reported_paid",
        "callback_requested",
        "disputed_invoice",
        "needs_human_review",
        "stop_requested",
        "completed_no_commitment",
    ],
)
def test_accepts_business_outcomes(outcome) -> None:
    result = validate({"outcome": outcome})
    assert result.ok
    assert result.proposal.outcome == SessionOutcome(outcome)
    assert result.proposal.promised_payment_date is None


def test_payment_promised_with_date_from_json_string() -> None:
    result = validate(
        json.dumps(
            {
                "outcome": "payment_promised",
                "promised_payment_date": "2026-11-20",
                "summary": "  Will pay Friday.  ",
            }
        )
    )
    assert result.ok
    assert result.proposal.promised_payment_date == date(2026, 11, 20)
    assert result.proposal.summary == "Will pay Friday."
    assert result.proposal.proposed_at == NOW


def test_today_is_allowed() -> None:
    assert validate(
        {"outcome": "payment_promised", "promised_payment_date": "2026-11-17"}
    ).ok


def test_empty_date_is_treated_as_missing() -> None:
    result = validate({"outcome": "payment_promised", "promised_payment_date": ""})
    assert result.ok and result.proposal.promised_payment_date is None


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ("not json", "JSON object"),
        ("[1]", "JSON object"),
        ({}, "outcome must be one of"),
        ({"outcome": "technical_failure"}, "outcome must be one of"),
        ({"outcome": "paid_in_full"}, "outcome must be one of"),
        ({"outcome": ["reported_paid"]}, "outcome must be one of"),
        (
            {"outcome": "reported_paid", "promised_payment_date": "2026-11-20"},
            "only allowed",
        ),
        (
            {"outcome": "payment_promised", "promised_payment_date": "Friday"},
            "valid YYYY-MM-DD",
        ),
        (
            {"outcome": "payment_promised", "promised_payment_date": "2026-02-30"},
            "valid YYYY-MM-DD",
        ),
        ({"outcome": "payment_promised", "promised_payment_date": 20261120}, "string"),
        (
            {"outcome": "payment_promised", "promised_payment_date": "2026-11-16"},
            "in the past",
        ),
        (
            {"outcome": "payment_promised", "promised_payment_date": "2027-03-01"},
            "90 days",
        ),
        ({"outcome": "reported_paid", "summary": 5}, "summary must be a string"),
    ],
)
def test_rejects_invalid(args, message) -> None:
    result = validate(args)
    assert not result.ok
    assert message in result.error


def test_summary_is_truncated() -> None:
    result = validate({"outcome": "reported_paid", "summary": "x" * 900})
    assert len(result.proposal.summary) == 500


def test_end_call_defaults_to_false() -> None:
    result = validate({"outcome": "callback_requested"})
    assert result.ok and result.proposal.end_call is False


@pytest.mark.parametrize(
    "value, expected", [(True, True), (False, False), (None, False)]
)
def test_end_call_is_accepted(value, expected) -> None:
    result = validate({"outcome": "completed_no_commitment", "end_call": value})
    assert result.ok and result.proposal.end_call is expected


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_end_call_must_be_boolean(value) -> None:
    result = validate({"outcome": "callback_requested", "end_call": value})
    assert not result.ok
    assert "end_call must be true or false" in result.error
