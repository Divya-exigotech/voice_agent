from datetime import date

import pytest

from app.services.response_checks import (
    REDACTED_CARD,
    REDACTED_CODE,
    check_agent_text,
    extract_amounts,
    extract_dates,
    redact_user_text,
)

TODAY = date(2026, 11, 17)


@pytest.fixture
def customer(sample_customers):
    return sample_customers[0]  # Charlie Test, INV-3, INR 300, due 2026-11-03


def kinds(text, customer, **kw):
    return [
        f.kind for f in check_agent_text(text, customer=customer, today=TODAY, **kw)
    ]


# --- Sensitive data requests -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Could you share the OTP you just received?",
        "Please tell me your card number so I can process it.",
        "What is your UPI PIN?",
        "Can you read out the CVV on the back of the card?",
        "I need your net banking password to verify the payment.",
        "May I have your bank account number and login credentials?",
    ],
)
def test_flags_requests_for_credentials(text, customer) -> None:
    assert "sensitive_data_request" in kinds(text, customer)


@pytest.mark.parametrize(
    "text",
    [
        "Please do not share your OTP, PIN or CVV with anyone.",
        "I will never ask for your card number or password.",
        "For your safety, don't read out your UPI PIN.",
        "You can pay by credit card, UPI or net banking on our portal.",
        "Thank you, I have noted that you will pay on Friday.",
    ],
)
def test_does_not_flag_warnings_or_payment_methods(text, customer) -> None:
    assert "sensitive_data_request" not in kinds(text, customer)


# --- Facts --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Your invoice INV-3 for INR 300 was due on 3 November 2026.",
        "The amount due is ₹300.",
        "That's 300 rupees, due 2026-11-03.",
    ],
)
def test_record_facts_pass(text, customer) -> None:
    assert kinds(text, customer) == []


def test_flags_invented_amount_invoice_and_date(customer) -> None:
    result = kinds("Invoice INV-9 for INR 3,500 was due on 1 October 2026.", customer)
    assert set(result) == {"unverified_amount", "unverified_invoice", "unverified_date"}


def test_amounts_and_dates_the_customer_said_are_allowed(customer) -> None:
    text = "Thanks, I've noted that you paid INR 150 on 15 November."
    assert set(kinds(text, customer)) == {"unverified_amount", "unverified_date"}
    assert (
        kinds(text, customer, allowed_amounts=[150], allowed_dates=[date(2026, 11, 15)])
        == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "If you don't pay, a late fee of 10% will apply.",
        "We may have to take legal action.",
        "This could affect your credit score.",
    ],
)
def test_flags_pressure_or_invented_policy(text, customer) -> None:
    assert "pressure_or_policy" in kinds(text, customer)


@pytest.mark.parametrize(
    "text",
    [
        "I don't have any information about late fees; a team member will follow up.",
        "There are no penalties mentioned on your invoice.",
    ],
)
def test_disclaimed_policy_mention_is_not_flagged(text, customer) -> None:
    assert kinds(text, customer) == []


def test_extractors() -> None:
    assert extract_amounts("₹2,500.50 and 300 rupees and INR 10") == [
        2500.5,
        300.0,
        10.0,
    ]
    assert extract_dates("on 20th November and Dec 2 and 2026-11-03", TODAY) == [
        date(2026, 11, 3),
        date(2026, 11, 20),
        date(2026, 12, 2),
    ]
    # No year: nearest occurrence (January after a November "today" is next year).
    assert extract_dates("by 5 January", TODAY) == [date(2027, 1, 5)]
    assert extract_dates("31 February", TODAY) == []


# --- Redaction of customer turns ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("My OTP is 482913", "482913"),
        ("otp: 4 8 2 9 1 3", "4 8 2 9 1 3"),
        ("the cvv is 123", "123"),
        ("UPI PIN 9876, please go ahead", "9876"),
        ("my password is hunter2", "hunter2"),
        ("I got an OTP, it's 482913. Go ahead", "482913"),
    ],
)
def test_redacts_codes(text, secret) -> None:
    redacted, flags = redact_user_text(text)
    assert secret not in redacted
    assert REDACTED_CODE in redacted
    assert [f.kind for f in flags] == ["sensitive_data_shared"]
    assert secret not in flags[0].excerpt


def test_redacts_luhn_valid_card_numbers_only() -> None:
    redacted, flags = redact_user_text("Card 4111 1111 1111 1111 expires soon")
    assert REDACTED_CARD in redacted and "4111" not in redacted
    assert len(flags) == 1

    # Not a valid card number (fails Luhn) and a phone number: left alone.
    text = "Reference 1234 5678 9012 3456, call +91 98765 43210"
    assert redact_user_text(text) == (text, [])


@pytest.mark.parametrize(
    "text",
    [
        "I forgot my pin yesterday",
        "I will pay 2500 on Friday",
        "My invoice is INV-1001",
        "My PIN changed, I'll pay 2500 tomorrow",
    ],
)
def test_ordinary_text_is_not_redacted(text) -> None:
    assert redact_user_text(text) == (text, [])
