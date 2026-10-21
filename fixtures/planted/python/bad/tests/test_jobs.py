import pytest

from worker import TaskQueue


@pytest.mark.skip  # expect: arbiter/assurance.permanently-skipped-test
def test_take_returns_oldest_first():
    q = TaskQueue()
    q.tasks.extend([1, 2])
    assert q.take() == 1


def test_take_on_empty_queue():
    q = TaskQueue()
    assert q.take() is None


# The comment directly above the def is deliberate: the assurance probe's
# `^\s*def test_` anchor swallows preceding blank lines, so a def after two
# blank lines is reported two lines early.
def test_take_removes_task():  # expect: arbiter/assurance.test-asserts-nothing
    q = TaskQueue()
    q.tasks.append(1)
    q.take()
