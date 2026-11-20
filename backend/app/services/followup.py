"""Follow-up calls: after a call ends with a payment promise, the customer is
called again a fixed time later.

The next call task is saved to the database in the background as soon as the
call ends (so ending the session is not slowed down). When the delay has
passed the customer joins the end of the queue, after every other customer; the session
itself (and its database rows) is then created by the normal start-session
flow, exactly as for any other call.
"""

import asyncio
import logging
from datetime import timedelta

from app.schemas.session import SessionOutcome, SessionRecord
from app.services.database import PostgresStore
from app.services.session_manager import SessionManager

logger = logging.getLogger(__name__)


class FollowUpScheduler:
    def __init__(
        self,
        manager: SessionManager,
        store: PostgresStore | None,
        *,
        delay_seconds: int,
    ) -> None:
        self._manager = manager
        self._store = store
        self._delay = delay_seconds
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, session: SessionRecord) -> None:
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
                task_id = await asyncio.to_thread(
                    self._store.schedule_followup,
                    customer_id,
                    session.ended_at + timedelta(seconds=self._delay),
                )
                if task_id is None:
                    logger.info("No follow-up call queued for %s", customer_id)
                    return
            logger.info("Follow-up call for %s is due in %d seconds", customer_id, self._delay)
            await asyncio.sleep(self._delay)
            if self._store is not None:
                customer = await asyncio.to_thread(self._store.load_followup, customer_id)
            else:
                customer = self._manager.customer(customer_id)
            if customer is None:
                logger.warning("Follow-up call for %s could not be loaded", customer_id)
                return
            self._manager.requeue_customer(customer)
            logger.info("Follow-up call for %s was added to the end of the queue", customer_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Follow-up call for %s failed", customer_id)

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
