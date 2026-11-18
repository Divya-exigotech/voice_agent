import asyncio

import pytest

from app.schemas.customer import Customer
from app.services.sequence_service import (
    QueueCompletedError,
    SequenceService,
    SessionActiveError,
    SessionNotFoundError,
)

pytestmark = pytest.mark.anyio


async def test_starts_at_first_customer(customers: list[Customer]) -> None:
    state = await SequenceService(customers).state()
    assert state.current_position == 1
    assert state.total == len(customers)
    assert state.remaining == len(customers)
    assert state.current_customer == customers[0]
    assert not state.all_completed


async def test_advances_one_per_finalized_session_in_order(customers: list[Customer]) -> None:
    service = SequenceService(customers)
    for position, expected in enumerate(customers, start=1):
        state = await service.state()
        assert state.current_position == position
        assert state.current_customer == expected
        record = await service.start_session()
        assert record.customer_id == expected.customer_id
        await service.finalize_session(record.session_id)
    final = await service.state()
    assert final.all_completed
    assert final.current_customer is None
    assert final.remaining == 0


async def test_duplicate_finalize_does_not_skip_a_customer(customers: list[Customer]) -> None:
    service = SequenceService(customers)
    record = await service.start_session()
    await service.finalize_session(record.session_id)
    await service.finalize_session(record.session_id)
    state = await service.finalize_session(record.session_id)
    assert state.current_index == 1
    assert state.current_customer == customers[1]


async def test_concurrent_finalize_advances_once(customers: list[Customer]) -> None:
    service = SequenceService(customers)
    record = await service.start_session()
    await asyncio.gather(*(service.finalize_session(record.session_id) for _ in range(10)))
    assert (await service.state()).current_index == 1


async def test_only_one_active_session(customers: list[Customer]) -> None:
    service = SequenceService(customers)
    await service.start_session()
    with pytest.raises(SessionActiveError):
        await service.start_session()


async def test_cannot_start_after_queue_completed(customers: list[Customer]) -> None:
    service = SequenceService(customers)
    for _ in customers:
        record = await service.start_session()
        await service.finalize_session(record.session_id)
    with pytest.raises(QueueCompletedError):
        await service.start_session()


async def test_unknown_session_raises(customers: list[Customer]) -> None:
    with pytest.raises(SessionNotFoundError):
        await SequenceService(customers).finalize_session("session_missing")
