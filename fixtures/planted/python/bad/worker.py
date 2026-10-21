import threading


class TaskQueue:
    def __init__(self):
        self.tasks = []

    def take(self):
        if len(self.tasks) > 0:  # expect: arbiter/concurrency.check-then-act-unlocked
            return self.tasks.pop(0)
        return None


def call_until_ok(fn):
    while True:  # expect: arbiter/python.retry-loop-no-backoff
        try:
            return fn()
        except ConnectionError:
            continue


threading.Thread(target=print).start()
