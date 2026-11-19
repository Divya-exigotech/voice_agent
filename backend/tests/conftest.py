from collections.abc import AsyncIterator

import httpx
import pytest

from app.api.deps import get_sequence_service
from app.core.config import settings
from app.main import app
from app.schemas.customer import Customer
from app.services.customer_loader import load_customers
from app.services.sequence_service import SequenceService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_real_foundry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic even if backend/.env holds real Foundry settings."""
    monkeypatch.setattr(settings, "AZURE_AI_PROJECT_ENDPOINT", "")


@pytest.fixture
async def customers() -> list[Customer]:
    return await load_customers(settings.CUSTOMERS_FILE)


@pytest.fixture
async def client(customers: list[Customer]) -> AsyncIterator[httpx.AsyncClient]:
    """API client wired to a fresh queue so tests never share state."""
    service = SequenceService(customers)
    app.dependency_overrides[get_sequence_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
