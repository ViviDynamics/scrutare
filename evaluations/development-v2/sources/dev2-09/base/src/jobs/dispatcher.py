"""Assign each job to exactly one worker. `claim` is called from worker threads."""
import threading


class Dispatcher:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._owners = {}

    def claim(self, job_id, worker):
        with self._lock:
            if job_id in self._owners:
                return False
            self._owners[job_id] = worker
            return True

    def owner(self, job_id):
        with self._lock:
            return self._owners.get(job_id)
