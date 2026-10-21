import sys

import pytest

from worker import TaskQueue


@pytest.mark.skipif(sys.platform == "win32", reason="no fork on Windows")
def test_take_returns_oldest_first():
    q = TaskQueue()
    q.tasks.extend([1, 2])
    assert q.take() == 1


def test_take_on_empty_queue():
    q = TaskQueue()
    assert q.take() is None


def test_take_removes_task():
    q = TaskQueue()
    q.tasks.append(1)
    q.take()
    assert q.tasks == []
