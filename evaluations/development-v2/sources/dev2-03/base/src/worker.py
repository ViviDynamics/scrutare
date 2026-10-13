"""Single owner of a TaskQueue; serializes every queue operation."""
import threading

from task_queue import TaskQueue


class Worker:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue = TaskQueue()

    def submit(self, item):
        with self._lock:
            self._queue.put(item)

    def next_batch(self, limit):
        with self._lock:
            return self._queue.drain(limit)

    def pending(self):
        with self._lock:
            return len(self._queue)
