import sys

sys.path.insert(0, "src/jobs")
from dispatcher import Dispatcher


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
dispatcher = Dispatcher(lock)
results = []
lock.hook = lambda: results.append(dispatcher.claim("job", "b"))
results.append(dispatcher.claim("job", "a"))
assert sorted(results) == [False, True], results
