import threading
import time


class TaskQueue:
    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            if len(self.tasks) > 0:
                return self.tasks.pop(0)
        return None


def call_until_ok(fn, attempts=5):
    delay = 0.1
    while True:
        try:
            return fn()
        except ConnectionError:
            attempts -= 1
            if attempts == 0:
                raise
            time.sleep(delay)
            delay *= 2


threading.Thread(target=print).start()
