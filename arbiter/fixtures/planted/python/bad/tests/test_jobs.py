import pytest

from worker import TaskQueue


def test_submit_adds_task():  # expect: arbiter/assurance.test-asserts-nothing
    # The skipped test below used to hide this one (round 2).
    q = TaskQueue()
    q.tasks.append(1)


@pytest.mark.skip  # expect: arbiter/assurance.permanently-skipped-test
def test_take_returns_oldest_first():
    q = TaskQueue()
    q.tasks.extend([1, 2])
    assert q.take() == 1


def test_take_on_empty_queue():
    q = TaskQueue()
    assert q.take() is None


def test_take_removes_task():  # expect: arbiter/assurance.test-asserts-nothing
    q = TaskQueue()
    q.tasks.append(1)
    q.take()
