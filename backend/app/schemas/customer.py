"""Schema for the customer/invoice records in the customer JSON file."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A record only belongs in the follow-up queue while money is still owed.
PaymentStatus = Literal["unpaid", "partially_paid", "overdue"]


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invoice_id: str = Field(min_length=1)
    invoice_number: str = Field(min_length=1)
    amount_due: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. INR")
    due_date: date
    payment_status: PaymentStatus


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Used in transcript blob paths, so restricted to path-safe characters.
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    customer_name: str = Field(min_length=1)
    # Display/demo data only. It is never used to place a call in this PoC.
    phone_number: str = Field(min_length=1)
    preferred_language: str = Field(min_length=1)
    invoice: Invoice


class CustomerListResponse(BaseModel):
    total: int
    customers: list[Customer]
