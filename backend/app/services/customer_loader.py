import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.schemas.customer import Customer

_ADAPTER = TypeAdapter(list[Customer])


class CustomerDataError(ValueError):
    """The customer JSON file is missing, malformed or fails validation."""


def parse_customers(raw: str) -> list[Customer]:
    """Validate JSON text, preserving file order. Error messages never echo record contents."""
    try:
        customers = _ADAPTER.validate_python(json.loads(raw))
    except json.JSONDecodeError as exc:
        msg = f"Customer file is not valid JSON (line {exc.lineno})"
        raise CustomerDataError(msg) from None
    except ValidationError as exc:
        locations = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors()[:5])
        msg = f"Customer file failed validation at: {locations}"
        raise CustomerDataError(msg) from None

    if not customers:
        msg = "Customer file contains no records"
        raise CustomerDataError(msg)
    ids = [c.customer_id for c in customers]
    if len(set(ids)) != len(ids):
        msg = "Customer file contains duplicate customer_id values"
        raise CustomerDataError(msg)
    return customers


async def load_customers(path: Path) -> list[Customer]:
    """Read the single customer JSON file without blocking the event loop."""
    try:
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except OSError:
        msg = "Customer file could not be read"
        raise CustomerDataError(msg) from None
    return parse_customers(raw)
