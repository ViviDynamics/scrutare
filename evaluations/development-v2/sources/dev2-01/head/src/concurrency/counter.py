"""Shared hit counter. Callers may invoke `add` from several threads."""
import threading


class HitCounter:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._value = 0

    def add(self, amount):
        updated = self._value + amount
        with self._lock:
            self._value = updated
            return updated

    def value(self):
        with self._lock:
            return self._value
