"""Heuristic checks on agent replies. They flag items for human review; they do not prove safety."""

import re
from decimal import Decimal, InvalidOperation

from app.schemas.customer import Customer

_CREDENTIAL_TERMS = re.compile(
    r"\b(otp|one[- ]time (?:password|passcode)|upi pin|pin|cvv|cvc|card number|"
    r"password|passcode|bank (?:account|credentials|login))\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def find_credential_mentions(reply: str) -> list[str]:
    """Return sensitive-credential terms mentioned in the reply (should normally be empty)."""
    return sorted({m.group(0).lower() for m in _CREDENTIAL_TERMS.finditer(reply)})


def _as_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def find_unsupported_amounts(reply: str, customer: Customer) -> list[str]:
    """Return money-like numbers (3+ digits or with separators) not in the customer's facts."""
    invoice = customer.invoice
    allowed = {invoice.amount_due, Decimal(invoice.due_date.year)}
    allowed.update(Decimal(d) for d in re.findall(r"\d+", invoice.invoice_number))
    flagged: list[str] = []
    for match in _NUMBER.finditer(reply):
        token = match.group(0).rstrip(",.")
        value = _as_decimal(token)
        looks_like_money = len(re.sub(r"\D", "", token)) >= 3 or "," in token or "." in token
        if value is not None and looks_like_money and value not in allowed:
            flagged.append(token)
    return flagged
