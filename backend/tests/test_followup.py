"""Scheduled follow-up calls: promised-date timing, the due check and de-duplication."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.schemas.session import SessionOutcome, SessionRecord
from app.services.followup import FollowUpScheduler, scheduled_call_time
from app.services.session_manager import SessionManager
from app.services.transcript_storage import InMemoryTranscriptStorage

KOLKATA = ZoneInfo("Asia/Kolkata")
# 2026-11-23 is a Monday.
MONDAY = date(2026, 11, 23)


def at(promised: date, today: date = MONDAY) -> datetime:
    return scheduled_call_time(promised, today, KOLKATA, 10)


def test_future_weekday_is_called_that_day_at_ten() -> None:
    # 10:00 in Kolkata (UTC+5:30) is 04:30 UTC.
    assert at(date(2026, 11, 26)) == datetime(2026, 11, 26, 4, 30, tzinfo=UTC)


def test_weekend_promise_is_called_on_monday() -> None:
    assert at(date(2026, 11, 28)).astimezone(KOLKATA).date() == date(2026, 11, 30)
    assert at(date(2026, 11, 29)).astimezone(KOLKATA).date() == date(2026, 11, 30)


def test_promise_to_pay_today_is_checked_the_next_business_day() -> None:
    assert at(MONDAY).astimezone(KOLKATA).date() == date(2026, 11, 24)
    friday = date(2026, 11, 27)
    assert at(friday, today=friday).astimezone(KOLKATA).date() == date(2026, 11, 30)


def test_call_time_is_the_configured_hour_in_business_timezone() -> None:
    result = scheduled_call_time(date(2026, 11, 26), MONDAY, KOLKATA, 14)
    assert result.astimezone(KOLKATA).hour == 14


class FakeStore:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, datetime]] = []
        self.due: list = []
        self.fail_due = False

    async def schedule_followup(self, customer_id: str, scheduled_at: datetime):
        self.scheduled.append((customer_id, scheduled_at))
        return uuid.uuid4()

    async def load_due_queue(self):
        if self.fail_due:
            raise RuntimeError("database unavailable")
        due, self.due = self.due, []
        return due

    async def load_followup(self, customer_id: str):
        return None


def promised_session(customer_id: str, promised: date | None) -> SessionRecord:
    return SessionRecord(
        session_id=str(uuid.uuid4()),
        customer_id=customer_id,
        invoice_id="inv",
        sequence_index=0,
        status="completed",
        started_at=datetime(2026, 11, 23, 5, 0, tzinfo=UTC),
        ended_at=datetime(2026, 11, 23, 5, 5, tzinfo=UTC),
        outcome=SessionOutcome.PAYMENT_PROMISED,
        promised_payment_date=promised,
    )


async def drain(scheduler: FollowUpScheduler) -> None:
    await asyncio.gather(*scheduler._tasks)


async def test_promise_with_a_date_is_scheduled_for_that_date(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    store = FakeStore()
    scheduler = FollowUpScheduler(
        manager,
        store,
        delay_seconds=1,
        business_timezone=KOLKATA,
        call_hour=10,  # type: ignore[arg-type]
    )
    customer_id = sample_customers[0].customer_id
    await scheduler.schedule(promised_session(customer_id, date(2026, 11, 26)))
    await drain(scheduler)

    assert store.scheduled == [(customer_id, datetime(2026, 11, 26, 4, 30, tzinfo=UTC))]
    # Not queued now: the due check adds it when the time comes.
    assert (await manager.queue_state()).total == len(sample_customers)


async def test_promise_without_a_date_keeps_the_fixed_delay(sample_customers) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    store = FakeStore()
    scheduler = FollowUpScheduler(manager, store, delay_seconds=1)  # type: ignore[arg-type]
    session = promised_session(sample_customers[0].customer_id, None)
    await scheduler.schedule(session)
    await drain(scheduler)

    ((_, scheduled_at),) = store.scheduled
    assert scheduled_at == session.ended_at.replace(second=session.ended_at.second + 1)


async def test_due_check_queues_scheduled_customers_once(sample_customers) -> None:
    manager = SessionManager(sample_customers[:1], InMemoryTranscriptStorage())
    store = FakeStore()
    scheduler = FollowUpScheduler(
        manager,
        store,
        delay_seconds=1,
        poll_seconds=1,  # type: ignore[arg-type]
    )
    store.due = [sample_customers[1]]
    await scheduler.start()
    try:
        await asyncio.sleep(1.3)
        assert (await manager.queue_state()).total == 2
        # The same customer coming back from the store is not queued twice.
        store.due = [sample_customers[1]]
        await asyncio.sleep(1.1)
        assert (await manager.queue_state()).total == 2
    finally:
        await scheduler.aclose()


async def test_due_check_keeps_running_after_a_database_error(sample_customers) -> None:
    manager = SessionManager(sample_customers[:1], InMemoryTranscriptStorage())
    store = FakeStore()
    store.fail_due = True
    scheduler = FollowUpScheduler(
        manager,
        store,
        delay_seconds=1,
        poll_seconds=1,  # type: ignore[arg-type]
    )
    await scheduler.start()
    try:
        await asyncio.sleep(1.3)
        store.fail_due = False
        store.due = [sample_customers[1]]
        await asyncio.sleep(1.1)
        assert (await manager.queue_state()).total == 2
    finally:
        await scheduler.aclose()


async def test_requeue_skips_a_customer_who_is_already_waiting(
    sample_customers,
) -> None:
    manager = SessionManager(sample_customers, InMemoryTranscriptStorage())
    assert await manager.requeue_customer(sample_customers[1]) is False
    assert (await manager.queue_state()).total == len(sample_customers)
