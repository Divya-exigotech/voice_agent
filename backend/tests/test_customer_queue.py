import pytest

from app.services.customer_queue import (
    CustomerQueue,
    QueueExhaustedError,
    SessionAlreadyActiveError,
    StaleCustomerError,
)

from .conftest import SAMPLE_ORDER


@pytest.fixture
def queue(sample_customers) -> CustomerQueue:
    return CustomerQueue(sample_customers)


def test_empty_queue_starts_complete() -> None:
    empty = CustomerQueue([])
    assert empty.is_complete
    assert empty.total == 0
    assert empty.current_customer is None
    with pytest.raises(QueueExhaustedError):
        empty.begin("session_1")


def test_starts_at_first_record(queue: CustomerQueue) -> None:
    assert queue.current_index == 0
    assert queue.current_customer.customer_id == SAMPLE_ORDER[0]
    assert queue.total == 3
    assert queue.remaining == 3
    assert not queue.is_complete
    assert queue.active_session_id is None


def test_begin_binds_current_customer(queue: CustomerQueue) -> None:
    customer = queue.begin("s1")
    assert customer.customer_id == SAMPLE_ORDER[0]
    assert queue.active_session_id == "s1"
    assert queue.current_index == 0  # not advanced until finished


def test_finish_advances_exactly_one(queue: CustomerQueue) -> None:
    queue.begin("s1")
    assert queue.finish("s1", advance=True) is True
    assert queue.current_index == 1
    assert queue.current_customer.customer_id == SAMPLE_ORDER[1]
    assert queue.active_session_id is None


def test_duplicate_finish_does_not_skip(queue: CustomerQueue) -> None:
    queue.begin("s1")
    queue.finish("s1", advance=True)
    assert queue.finish("s1", advance=True) is False
    assert queue.finish("s1", advance=True) is False
    assert queue.current_customer.customer_id == SAMPLE_ORDER[1]


def test_finish_unknown_session_is_ignored(queue: CustomerQueue) -> None:
    queue.begin("s1")
    assert queue.finish("other", advance=True) is False
    assert queue.current_index == 0
    assert queue.active_session_id == "s1"


def test_only_one_active_session(queue: CustomerQueue) -> None:
    queue.begin("s1")
    with pytest.raises(SessionAlreadyActiveError):
        queue.begin("s2")


def test_failed_session_keeps_same_customer(queue: CustomerQueue) -> None:
    queue.begin("s1")
    assert queue.finish("s1", advance=False) is False
    assert queue.current_index == 0
    assert queue.active_session_id is None
    assert queue.begin("s2").customer_id == SAMPLE_ORDER[0]


def test_stale_expected_customer_rejected(queue: CustomerQueue) -> None:
    with pytest.raises(StaleCustomerError):
        queue.begin("s1", expected_customer_id=SAMPLE_ORDER[1])
    assert queue.active_session_id is None
    assert queue.begin("s1", expected_customer_id=SAMPLE_ORDER[0])


def test_processes_all_records_in_file_order(queue: CustomerQueue) -> None:
    seen = []
    for n in range(queue.total):
        seen.append(queue.begin(f"s{n}").customer_id)
        queue.finish(f"s{n}", advance=True)
    assert seen == SAMPLE_ORDER
    assert queue.is_complete
    assert queue.current_customer is None
    assert queue.remaining == 0
    with pytest.raises(QueueExhaustedError):
        queue.begin("extra")


def test_reset_returns_to_first(queue: CustomerQueue) -> None:
    queue.begin("s1")
    with pytest.raises(SessionAlreadyActiveError):
        queue.reset()
    queue.finish("s1", advance=True)
    queue.reset()
    assert queue.current_index == 0
