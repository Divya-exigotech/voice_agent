import pytest

from app.schemas.customer import Customer
from app.services.agent_context import build_customer_context, build_structured_inputs
from app.services.latency import summarize
from app.services.reply_checks import find_credential_mentions, find_unsupported_amounts


@pytest.mark.anyio
async def test_context_contains_only_customer_facts(customers: list[Customer]) -> None:
    text = build_customer_context(customers[0])
    assert "Aarav Patel" in text
    assert "2500 INR" in text
    assert "2026-11-10" in text
    assert "INV-1001" in text
    assert "+91" not in text  # phone number is never sent to the agent


@pytest.mark.anyio
async def test_structured_inputs(customers: list[Customer]) -> None:
    inputs = build_structured_inputs(customers[1])
    assert inputs["amount_due"] == "1800"
    assert "phone_number" not in inputs


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Please share your OTP.", ["otp"]),
        ("What is your UPI PIN and CVV?", ["cvv", "upi pin"]),
        ("Your invoice is due on the 10th.", []),
    ],
)
def test_credential_mentions(reply: str, expected: list[str]) -> None:
    assert find_credential_mentions(reply) == expected


@pytest.mark.anyio
async def test_amounts_grounded_in_facts(customers: list[Customer]) -> None:
    customer = customers[0]  # 2500 INR, INV-1001, due 2026-11-10
    assert (
        find_unsupported_amounts("You owe 2,500 INR on INV-1001, due 10 Nov 2026.", customer) == []
    )
    assert find_unsupported_amounts("You owe 3,000 INR.", customer) == ["3,000"]


def test_latency_summary() -> None:
    assert summarize([]) is None
    one = summarize([0.5])
    assert one is not None
    assert one.p50 == one.p95 == 0.5
    many = summarize([float(i) for i in range(1, 101)])
    assert many is not None
    assert many.count == 100
    assert many.p50 == pytest.approx(50.5)
    assert many.p95 == pytest.approx(95.05)
    assert many.max == 100.0
