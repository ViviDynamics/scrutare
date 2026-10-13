"""FIFO batch buffer. Not internally synchronized: the owner must hold its lock.

`Worker` in worker.py is the only owner and wraps every call in `Worker._lock`.
"""
from collections import deque


class TaskQueue:
    def __init__(self):
        self._items = deque()

    def put(self, item):
        self._items.append(item)

    def drain(self, limit):
        batch = []
        while self._items and len(batch) < limit:
            batch.append(self._items.popleft())
        return batch

    def __len__(self):
        return len(self._items)
