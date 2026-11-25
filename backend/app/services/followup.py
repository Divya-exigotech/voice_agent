"""Follow-up calls: after a call ends with a payment promise, the customer is
called again.

The next call task is saved to the database in the background as soon as the
call ends (so ending the session is not slowed down).

* Promise with a date: the call is scheduled for that date at the configured
  hour (default 10:00, business timezone). A promise to pay today is checked on
  the next business day. Weekends are skipped (calls are made Monday to Friday).
  A background check adds the customer to the end of the queue once the call is
  due, so scheduled calls also survive a backend restart.
* Promise without a date (or no database): the customer joins the end of the
  queue after a fixed delay.

The session itself (and its database rows) is created by the normal
start-session flow, exactly as for any other call.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from app.schemas.session import SessionOutcome, SessionRecord
from app.services.database import PostgresStore
from app.services.session_manager import SessionManager

logger = logging.getLogger(__name__)

_SATURDAY = 5  # date.weekday(): Monday is 0


def _on_or_after_weekday(day: date) -> date:
    """``day`` itself if it is Monday to Friday, otherwise the following Monday."""
    while day.weekday() >= _SATURDAY:
        day += timedelta(days=1)
    return day


def scheduled_call_time(
    promised: date, today: date, zone: tzinfo, hour: int
) -> datetime:
    """When to call about a payment promised for ``promised`` (UTC).

    A future date is called on that date; a promise to pay today is checked the
    next day. Either way weekends are skipped and the call is at ``hour``:00 in
    ``zone``.
    """
    if promised > today:
        day = _on_or_after_weekday(promised)
    else:
        day = _on_or_after_weekday(today + timedelta(days=1))
    return datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)


class FollowUpScheduler:
    def __init__(
        self,
        manager: SessionManager,
        store: PostgresStore | None,
        *,
        delay_seconds: int,
        business_timezone: tzinfo = UTC,
        call_hour: int = 10,
        poll_seconds: int = 60,
    ) -> None:
        self._manager = manager
        self._store = store
        self._delay = delay_seconds
        self._zone = business_timezone
        self._call_hour = call_hour
        self._poll_seconds = poll_seconds
        self._tasks: set[asyncio.Task[None]] = set()
        self._poller: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background check that queues scheduled calls once they are due."""
        if self._store is not None and self._poller is None:
            self._poller = asyncio.create_task(
                self._poll_due(), name="voice-agent-followup-poll"
            )

    async def _poll_due(self) -> None:
        assert self._store is not None
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                for customer in await self._store.load_due_queue():
                    if await self._manager.requeue_customer(customer):
                        logger.info(
                            "Scheduled call for %s is due and was added to the queue",
                            customer.customer_id,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Checking for due scheduled calls failed")

    async def schedule(self, session: SessionRecord) -> None:
        """Plan the next call when ``session`` ended with a payment promise.

        Returns immediately; the work runs as a background task.
        """
        if (
            self._delay <= 0
            or session.status != "completed"
            or session.outcome != SessionOutcome.PAYMENT_PROMISED
            or session.ended_at is None
        ):
            return
        task = asyncio.create_task(self._run(session), name="voice-agent-followup")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, session: SessionRecord) -> None:
        customer_id = session.customer_id
        try:
            if self._store is not None:
                promised = session.promised_payment_date
                if promised is not None:
                    scheduled_at = scheduled_call_time(
                        promised,
                        session.ended_at.astimezone(self._zone).date(),
                        self._zone,
                        self._call_hour,
                    )
                else:
                    scheduled_at = session.ended_at + timedelta(seconds=self._delay)
                task_id = await self._store.schedule_followup(customer_id, scheduled_at)
                if task_id is None:
                    logger.info("No follow-up call queued for %s", customer_id)
                    return
                if promised is not None:
                    # The background check queues it when the time comes.
                    logger.info(
                        "Follow-up call for %s is scheduled for %s",
                        customer_id,
                        scheduled_at.astimezone(self._zone).isoformat(),
                    )
                    return
            logger.info(
                "Follow-up call for %s is due in %d seconds", customer_id, self._delay
            )
            await asyncio.sleep(self._delay)
            if self._store is not None:
                customer = await self._store.load_followup(customer_id)
            else:
                customer = await self._manager.customer(customer_id)
            if customer is None:
                logger.warning("Follow-up call for %s could not be loaded", customer_id)
                return
            await self._manager.requeue_customer(customer)
            logger.info(
                "Follow-up call for %s was added to the end of the queue", customer_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Follow-up call for %s failed", customer_id)

    async def aclose(self) -> None:
        tasks = set(self._tasks)
        if self._poller is not None:
            tasks.add(self._poller)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
