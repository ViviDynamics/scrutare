"""Move a balance between two accounts while holding both account locks."""


class InsufficientFunds(Exception):
    pass


def move(source, target, amount):
    first, second = sorted((source, target), key=lambda account: account.number)
    with first.lock:
        with second.lock:
            source.balance -= amount
            target.balance += amount
