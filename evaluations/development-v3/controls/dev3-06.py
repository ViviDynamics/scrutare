import sys

sys.path.insert(0, "src/concurrency")
from route_table import RouteTable


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
table = RouteTable(lock)
table.add("/a", "A")
seen = []
lock.hook = lambda: seen.append((table.snapshot(), table.match("/a/x"), table.match("/b/x")))
table.add("/b", "B")
assert seen == [((("/a", "A"),), "A", None)], seen
assert table.snapshot() == (("/a", "A"), ("/b", "B"))
lock.hook = lambda: seen.append(table.snapshot())
table.remove("/a")
assert seen[1] == (("/a", "A"), ("/b", "B")) and table.match("/a/x") is None
