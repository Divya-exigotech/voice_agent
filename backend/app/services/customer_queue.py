"""Sequential processing of the customer list.

Records are processed strictly in file order, one session at a time, and the
queue advances by exactly one record per finalized session. This class holds
no I/O so the ordering rules can be tested in isolation.
"""

from collections.abc import Sequence

from app.schemas.customer import Customer


class QueueError(Exception):
    pass


class QueueExhaustedError(QueueError):
    """Every customer in the file has been processed."""


class SessionAlreadyActiveError(QueueError):
    """Only one session may be active at a time."""


class StaleCustomerError(QueueError):
    """The caller expected a different current customer (queue has moved on)."""


class CustomerQueue:
    def __init__(self, customers: Sequence[Customer]) -> None:
        # An empty queue is valid (e.g. no queued call tasks in the database):
        # it starts out complete.
        self._customers: tuple[Customer, ...] = tuple(customers)
        self._index = 0
        self._active_session_id: str | None = None

    @property
    def total(self) -> int:
        return len(self._customers)

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def remaining(self) -> int:
        return self.total - self._index

    @property
    def is_complete(self) -> bool:
        return self._index >= self.total

    @property
    def current_customer(self) -> Customer | None:
        return None if self.is_complete else self._customers[self._index]

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    def begin(self, session_id: str, expected_customer_id: str | None = None) -> Customer:
        """Bind a new session to the current customer."""
        if self.is_complete:
            raise QueueExhaustedError("All customers completed")
        if self._active_session_id is not None:
            raise SessionAlreadyActiveError(
                f"Session {self._active_session_id} is already active"
            )
        customer = self._customers[self._index]
        if expected_customer_id is not None and expected_customer_id != customer.customer_id:
            raise StaleCustomerError(
                f"Current customer is {customer.customer_id}, not {expected_customer_id}"
            )
        self._active_session_id = session_id
        return customer

    def finish(self, session_id: str, *, advance: bool) -> bool:
        """Release the active session, advancing one record if ``advance``.

        Idempotent: finishing a session that is not the active one (unknown,
        or already finished) changes nothing, so duplicate end events can
        never skip a customer. Returns True only if the queue advanced.
        """
        if session_id != self._active_session_id:
            return False
        self._active_session_id = None
        if advance:
            self._index += 1
        return advance

    def reset(self) -> None:
        if self._active_session_id is not None:
            raise SessionAlreadyActiveError("Cannot reset while a session is active")
        self._index = 0
