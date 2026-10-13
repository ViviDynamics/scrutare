"""Move money between accounts atomically."""


class UnknownAccount(Exception):
    pass


def _adjust(connection, account_id, delta):
    cursor = connection.execute(
        "UPDATE accounts SET balance = balance + ? WHERE id = ?", (delta, account_id)
    )
    if cursor.rowcount != 1:
        raise UnknownAccount(account_id)
    connection.execute(
        "INSERT INTO audit(account_id, delta) VALUES (?, ?)", (account_id, delta)
    )
    connection.commit()


def transfer(connection, source, target, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    try:
        _adjust(connection, source, -amount)
        _adjust(connection, target, amount)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
