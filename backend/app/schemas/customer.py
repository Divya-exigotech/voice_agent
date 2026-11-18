from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Invoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    invoice_id: str = Field(min_length=1)
    invoice_number: str = Field(min_length=1)
    amount_due: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    due_date: date
    payment_status: Literal["unpaid", "partially_paid", "paid", "overdue"]


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    phone_number: str = Field(description="Display/demo data only; never dialled in this phase.")
    preferred_language: str = Field(min_length=1)
    invoice: Invoice
