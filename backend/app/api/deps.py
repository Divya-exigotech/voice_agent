import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import WebSocket

from app.core.config import settings
from app.schemas.customer import Customer
from app.services.agent_context import build_structured_inputs
from app.services.customer_loader import load_customers
from app.services.foundry_session import (
    AgentConversation,
    FoundryNotConfiguredError,
    open_conversation,
)
from app.services.sequence_service import SequenceService

ConversationOpener = Callable[[Customer], AbstractAsyncContextManager[AgentConversation]]

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


def get_conversation_opener(websocket: WebSocket) -> ConversationOpener:
    """Return a function that opens a Foundry conversation for one customer.

    Uses the project client created once in the app lifespan.
    """
    client = getattr(websocket.app.state, "foundry_client", None)

    def opener(customer: Customer) -> AbstractAsyncContextManager[AgentConversation]:
        if client is None:
            msg = "Foundry client is not configured"
            raise FoundryNotConfiguredError(msg)
        inputs = (
            build_structured_inputs(customer) if settings.AZURE_AI_USE_STRUCTURED_INPUTS else None
        )
        return open_conversation(client, settings.AZURE_AI_AGENT_NAME, structured_inputs=inputs)

    return opener
