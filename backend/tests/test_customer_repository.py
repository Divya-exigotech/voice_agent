import json
from datetime import date
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.services.customer_repository import (
    CustomerDataError,
    load_customers,
    parse_customers,
)

from .conftest import SAMPLE_ORDER


async def test_repository_customer_file_is_valid() -> None:
    customers = await load_customers(REPO_ROOT / "data" / "customers.json")
    assert len(customers) >= 2
    assert customers[0].customer_id == "cust_demo_001"
    assert customers[0].invoice.invoice_number == "INV-1001"


def test_parse_preserves_file_order(sample_records) -> None:
    customers = parse_customers(json.dumps(sample_records))
    assert [c.customer_id for c in customers] == SAMPLE_ORDER


def test_parse_converts_types(sample_records) -> None:
    customer = parse_customers(json.dumps(sample_records))[1]
    assert customer.invoice.due_date == date(2026, 11, 1)
    assert customer.invoice.amount_due == 100.5


async def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CustomerDataError, match="Cannot read"):
        await load_customers(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "{}", '{"customers": []}', "[]", "[1, 2]"],
    ids=["empty", "invalid-json", "object", "wrapped", "empty-list", "not-objects"],
)
def test_parse_rejects_bad_documents(raw: str) -> None:
    with pytest.raises(CustomerDataError):
        parse_customers(raw)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("customer_name",), ""),
        (("invoice", "amount_due"), 0),
        (("invoice", "amount_due"), -10),
        (("invoice", "amount_due"), "lots"),
        (("invoice", "currency"), "inr"),
        (("invoice", "currency"), "RUPEES"),
        (("invoice", "due_date"), "10/11/2026"),
        (("invoice", "due_date"), "2026-02-30"),
        (("invoice", "payment_status"), "paid"),
        (("customer_id",), 42),
        (("customer_id",), "../other"),  # used in blob paths
        (("customer_id",), "cust 1"),
    ],
)
def test_parse_rejects_invalid_fields(sample_records, path, value) -> None:
    target = sample_records[0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(CustomerDataError):
        parse_customers(json.dumps(sample_records))


@pytest.mark.parametrize("field", ["customer_id", "customer_name", "invoice"])
def test_parse_rejects_missing_fields(sample_records, field) -> None:
    del sample_records[0][field]
    with pytest.raises(CustomerDataError):
        parse_customers(json.dumps(sample_records))


def test_parse_rejects_unknown_fields(sample_records) -> None:
    sample_records[0]["invoice"]["amount_paid"] = 10
    with pytest.raises(CustomerDataError):
        parse_customers(json.dumps(sample_records))


def test_parse_rejects_duplicate_customer_id(sample_records) -> None:
    sample_records[1]["customer_id"] = sample_records[0]["customer_id"]
    with pytest.raises(CustomerDataError, match="Duplicate customer_id"):
        parse_customers(json.dumps(sample_records))


def test_parse_rejects_duplicate_invoice_id(sample_records) -> None:
    sample_records[2]["invoice"]["invoice_id"] = sample_records[0]["invoice"][
        "invoice_id"
    ]
    with pytest.raises(CustomerDataError, match="Duplicate invoice_id"):
        parse_customers(json.dumps(sample_records))
