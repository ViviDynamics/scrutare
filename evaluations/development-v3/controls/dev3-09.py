import sys

sys.path.insert(0, "src/concurrency")
from account_locks import move


class RecordingLock:
    """Deterministic stand-in for a lock that records the order of acquisition."""

    def __init__(self, name, log):
        self.name, self.log = name, log

    def __enter__(self):
        self.log.append(self.name)
        return self

    def __exit__(self, *exc):
        return False


class Account:
    def __init__(self, number, balance, log):
        self.number, self.balance, self.lock = number, balance, RecordingLock(number, log)


log = []
one, two = Account(1, 100, log), Account(2, 100, log)
move(one, two, 10)
move(two, one, 30)
assert (one.balance, two.balance) == (120, 80)
assert log[0] == log[2] and log[1] == log[3], log
