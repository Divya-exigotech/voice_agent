import asyncio

from app.core.config import settings
from app.services.customer_loader import load_customers
from app.services.sequence_service import SequenceService

_service: SequenceService | None = None
_init_lock = asyncio.Lock()


async def get_sequence_service() -> SequenceService:
    """Lazily build the process-wide queue from the customer JSON file."""
    global _service
    if _service is None:
        async with _init_lock:
            if _service is None:
                _service = SequenceService(await load_customers(settings.CUSTOMERS_FILE))
    return _service
