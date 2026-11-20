"""Heuristic screening of conversation turns.

Agent turns are checked for requests for payment credentials, facts that are
not in the customer record (amounts, invoice numbers, dates) and pressure or
invented policy (fees, penalties, legal action). Customer turns are redacted
when they contain card numbers or codes (OTP/PIN/CVV/password).

This is a screening aid that raises flags for a human to review, not proof of
safety: it works on transcript text, uses keyword/regex rules and can miss
numbers spoken as words or flag harmless sentences.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

from app.schemas.customer import Customer
from app.schemas.session import ReviewFlag

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# Credentials only; payment *methods* ("pay by card or net banking") are fine.
_SENSITIVE = re.compile(
    r"\b(otp|one[- ]time (?:password|code|pin)|upi pin|pin|cvv|cvc|security code|"
    r"card (?:number|details)|expiry date|password|passcode|"
    r"net ?banking (?:password|login|credentials|id)|bank (?:login|credentials|password|account number)|"
    r"login (?:id|details|credentials))\b",
    re.IGNORECASE,
)
_REQUEST_CUE = re.compile(
    r"\b(share|provide|tell me|give me|send|confirm|read out|read me|enter|type|say|"
    r"what is your|what's your|may i (?:have|get|know)|can i (?:have|get)|i need|i'll need|"
    r"we need|need your|could you|can you|would you|please)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(never|not|don't|do not|won't|will not|shouldn't|should not|no need|cannot|can't|avoid|"
    r"without|isn't|haven't|hasn't)\b",
    re.IGNORECASE,
)
# For pressure terms a plain "don't" is not enough ("If you don't pay, a late fee
# applies" is a threat); only statements of absence or ignorance clear the flag.
_PRESSURE_DISCLAIMER = re.compile(
    r"\b(no information|don't have|do not have|not aware|there (?:is|are) no|"
    r"no (?:late )?(?:fee|fees|penalt\w+|charges?|interest)|won't be any|will not be any|"
    r"(?:not able to|can't|cannot) (?:comment|say|confirm)|a team member will)\b",
    re.IGNORECASE,
)
_PRESSURE = re.compile(
    r"\b(late (?:fee|fees|charge|charges)|penalt(?:y|ies)|interest|legal action|legal notice|court|"
    r"police|lawyer|credit score|cibil|blacklist(?:ed)?|collection agency|disconnect(?:ed|ion)?|"
    r"suspend(?:ed)?|arrest)\b",
    re.IGNORECASE,
)

_AMOUNT = re.compile(
    r"(?:₹|\brs\.?|\binr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    r"|\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:rupees?|inr|/-)",
    re.IGNORECASE,
)
_INVOICE = re.compile(r"\bINV[- ]?(\d+)\b", re.IGNORECASE)

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b\.?(?:,?\s*(\d{{4}}))?", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(\d{{4}}))?", re.IGNORECASE)

# Redaction of customer-provided secrets.
_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_SECRET_TERM = r"(otp|one[- ]time (?:password|code)|upi pin|pin|cvv|cvc|security code|password|passcode)"
# A 3–12 digit code shortly after the term: "OTP, it's 482913", "cvv is 123", "pin 9 8 7 6".
# The short gap keeps "My PIN changed, I'll pay 2500 tomorrow" intact.
_DIGITS_AFTER_TERM = re.compile(
    rf"\b{_SECRET_TERM}\b([^\d\n]{{0,12}}?)(\d(?:[ -]?\d){{2,11}})\b", re.IGNORECASE
)
# A password-like token that contains a digit: "my password is hunter2".
_TOKEN_AFTER_TERM = re.compile(
    rf"\b{_SECRET_TERM}(\s*(?:is|was|:|=|-)\s*)((?=[^\s,.;]*\d)[^\s,.;]{{3,32}})", re.IGNORECASE
)
REDACTED_CARD = "[card number removed]"
REDACTED_CODE = "[removed]"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def extract_amounts(text: str) -> list[float]:
    values = []
    for match in _AMOUNT.finditer(text):
        value = _number(match.group(1) or match.group(2))
        if value is not None:
            values.append(value)
    return values


def _resolve_year(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        if year is not None:
            return date(year, month, day)
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    # No year given: assume the occurrence nearest to today.
    if candidate < today - timedelta(days=183):
        try:
            candidate = candidate.replace(year=today.year + 1)
        except ValueError:
            return None
    return candidate


def extract_dates(text: str, today: date) -> list[date]:
    found: list[date] = []
    for y, m, d in _ISO_DATE.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, month, y in _DAY_MONTH.findall(text):
        resolved = _resolve_year(int(d), _MONTHS[month.lower()], int(y) if y else None, today)
        if resolved:
            found.append(resolved)
    for month, d, y in _MONTH_DAY.findall(text):
        resolved = _resolve_year(int(d), _MONTHS[month.lower()], int(y) if y else None, today)
        if resolved:
            found.append(resolved)
    return found


def check_agent_text(
    text: str,
    *,
    customer: Customer,
    today: date,
    allowed_amounts: Iterable[float] = (),
    allowed_dates: Iterable[date] = (),
) -> list[ReviewFlag]:
    """Screen one agent turn. `allowed_*` are values the customer said themselves."""
    invoice = customer.invoice
    amounts = {round(invoice.amount_due, 2), *(round(a, 2) for a in allowed_amounts)}
    dates = {invoice.due_date, today, *allowed_dates}
    own_invoice_digits = re.sub(r"\D", "", invoice.invoice_number)

    flags: list[ReviewFlag] = []

    def flag(kind: str, detail: str, excerpt: str) -> None:
        flags.append(ReviewFlag(kind=kind, speaker="agent", detail=detail, excerpt=excerpt[:300]))

    for sentence in _sentences(text):
        negated = bool(_NEGATION.search(sentence))
        term = _SENSITIVE.search(sentence)
        if term and _REQUEST_CUE.search(sentence) and not negated:
            flag(
                "sensitive_data_request",
                f"Agent may have asked for sensitive data ({term.group(1)})",
                sentence,
            )
        pressure = _PRESSURE.search(sentence)
        if pressure and not _PRESSURE_DISCLAIMER.search(sentence):
            flag(
                "pressure_or_policy",
                f"Agent mentioned '{pressure.group(1)}', which is not in the customer record",
                sentence,
            )
        for value in extract_amounts(sentence):
            if round(value, 2) not in amounts:
                flag("unverified_amount", f"Amount {value:,.2f} is not the amount due", sentence)
        for digits in _INVOICE.findall(sentence):
            if digits != own_invoice_digits:
                flag("unverified_invoice", f"Invoice INV-{digits} is not this customer's invoice", sentence)
        for mentioned in extract_dates(sentence, today):
            if mentioned not in dates:
                flag(
                    "unverified_date",
                    f"Date {mentioned.isoformat()} is not the due date or a date the customer gave",
                    sentence,
                )
    return flags


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact_user_text(text: str) -> tuple[str, list[ReviewFlag]]:
    """Remove card numbers and codes the customer said/typed. Returns (text, flags)."""
    flags: list[ReviewFlag] = []

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            flags.append(
                ReviewFlag(
                    kind="sensitive_data_shared",
                    speaker="user",
                    detail="Customer shared a card number; it was removed from the transcript",
                    excerpt=REDACTED_CARD,
                )
            )
            return REDACTED_CARD
        return match.group(0)

    def code(match: re.Match[str]) -> str:
        kept = f"{match.group(1)}{match.group(2)}{REDACTED_CODE}"
        flags.append(
            ReviewFlag(
                kind="sensitive_data_shared",
                speaker="user",
                detail=f"Customer shared a {match.group(1).upper()}; it was removed from the transcript",
                excerpt=kept,
            )
        )
        return kept

    redacted = _CARD_CANDIDATE.sub(card, text)
    redacted = _DIGITS_AFTER_TERM.sub(code, redacted)
    # "[removed]" has no digit, so already-redacted codes are not matched again.
    redacted = _TOKEN_AFTER_TERM.sub(code, redacted)
    return redacted, flags
