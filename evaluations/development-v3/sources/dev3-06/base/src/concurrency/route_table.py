"""Route table: read on every request, changed rarely.

Writers build a new tuple and swap it in under the lock. A published tuple is
never mutated.
"""
import threading


class RouteTable:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._routes = ()

    def add(self, prefix, handler):
        with self._lock:
            self._routes = (*self._routes, (prefix, handler))

    def remove(self, prefix):
        with self._lock:
            self._routes = tuple(route for route in self._routes if route[0] != prefix)

    def snapshot(self):
        with self._lock:
            return self._routes

    def match(self, path):
        for prefix, handler in self.snapshot():
            if path.startswith(prefix):
                return handler
        return None
