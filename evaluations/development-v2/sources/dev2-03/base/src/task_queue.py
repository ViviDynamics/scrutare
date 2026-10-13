"""FIFO batch buffer. Not internally synchronized: the owner must hold its lock.

`Worker` in worker.py is the only owner and wraps every call in `Worker._lock`.
"""


class TaskQueue:
    def __init__(self):
        self._items = []

    def put(self, item):
        self._items.append(item)

    def drain(self, limit):
        batch = []
        while self._items and len(batch) < limit:
            batch.append(self._items.pop(0))
        return batch

    def __len__(self):
        return len(self._items)
