from fastapi import APIRouter

from app.api.deps import SessionManagerDep
from app.schemas.customer import CustomerListResponse

router = APIRouter(tags=["customers"])


@router.get("/customers", response_model=CustomerListResponse)
def list_customers(manager: SessionManagerDep) -> CustomerListResponse:
    """Return the queued customer records in order (from PostgreSQL or the JSON file)."""
    customers = list(manager.customers)
    return CustomerListResponse(total=len(customers), customers=customers)
