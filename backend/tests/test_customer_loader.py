import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.customer_loader import CustomerDataError, load_customers, parse_customers

pytestmark = pytest.mark.anyio


def _record(customer_id: str = "c1") -> dict:
    return {
        "customer_id": customer_id,
        "customer_name": "Test User",
        "phone_number": "+91XXXXXXXXXX",
        "preferred_language": "English",
        "invoice": {
            "invoice_id": "i1",
            "invoice_number": "INV-1",
            "amount_due": 10,
            "currency": "INR",
            "due_date": "2026-11-10",
            "payment_status": "unpaid",
        },
    }


async def test_bundled_file_loads_four_customers_in_order() -> None:
    customers = await load_customers(settings.CUSTOMERS_FILE)
    assert [c.customer_id for c in customers] == [
        "cust_demo_001",
        "cust_demo_002",
        "cust_demo_003",
        "cust_demo_004",
    ]


async def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CustomerDataError):
        await load_customers(tmp_path / "nope.json")


def test_file_order_is_preserved() -> None:
    raw = json.dumps([_record("b"), _record("a"), _record("c")])
    assert [c.customer_id for c in parse_customers(raw)] == ["b", "a", "c"]


def test_invalid_json_rejected() -> None:
    with pytest.raises(CustomerDataError, match="not valid JSON"):
        parse_customers("{oops")


def test_empty_list_rejected() -> None:
    with pytest.raises(CustomerDataError, match="no records"):
        parse_customers("[]")


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(CustomerDataError, match="duplicate"):
        parse_customers(json.dumps([_record("x"), _record("x")]))


def test_schema_violation_rejected_without_echoing_values() -> None:
    bad = _record()
    bad["invoice"]["payment_status"] = "secret-value"
    with pytest.raises(CustomerDataError) as exc_info:
        parse_customers(json.dumps([bad]))
    assert "secret-value" not in str(exc_info.value)
    assert "payment_status" in str(exc_info.value)
