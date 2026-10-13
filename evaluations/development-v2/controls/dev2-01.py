import sys

sys.path.insert(0, "src/concurrency")
from counter import HitCounter


class HookLock:
    """Deterministic stand-in for a lock: runs one competing call when first entered."""

    def __init__(self):
        self.hook = None

    def __enter__(self):
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        return self

    def __exit__(self, *exc):
        return False


lock = HookLock()
counter = HitCounter(lock)
lock.hook = lambda: counter.add(5)
counter.add(1)
assert counter.value() == 6, counter.value()
