"""Move a balance between two accounts while holding both account locks."""


class InsufficientFunds(Exception):
    pass


def move(source, target, amount):
    with source.lock:
        if source.balance < amount:
            raise InsufficientFunds(source.number)
        with target.lock:
            source.balance -= amount
            target.balance += amount
