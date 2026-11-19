"""Loads and validates the single customer/invoice JSON file."""

from collections import Counter
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.schemas.customer import Customer

_customer_list = TypeAdapter(list[Customer])


class CustomerDataError(Exception):
    """The customer JSON file is missing, unreadable or invalid."""


def parse_customers(raw: str | bytes, source: str = "<memory>") -> list[Customer]:
    """Validate customer JSON and return the records in file order."""
    try:
        customers = _customer_list.validate_json(raw)
    except ValidationError as exc:
        raise CustomerDataError(f"Invalid customer data in {source}:\n{exc}") from exc

    if not customers:
        raise CustomerDataError(f"Customer data in {source} contains no records")

    for label, values in (
        ("customer_id", [c.customer_id for c in customers]),
        ("invoice_id", [c.invoice.invoice_id for c in customers]),
    ):
        duplicates = sorted(v for v, n in Counter(values).items() if n > 1)
        if duplicates:
            raise CustomerDataError(
                f"Duplicate {label} in {source}: {', '.join(duplicates)}"
            )

    return customers


def load_customers(path: Path) -> list[Customer]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CustomerDataError(f"Cannot read customer data file {path}: {exc}") from exc
    return parse_customers(raw, source=str(path))
