"""Append events. `events.idempotency_key` is the primary key (see tables.sql).

Each writer owns its connection: nothing else is ever pending on it.
"""


def record_event(connection, idempotency_key, payload):
    connection.execute(
        "INSERT OR IGNORE INTO events(idempotency_key, payload) VALUES (?, ?)",
        (idempotency_key, payload),
    )
    connection.commit()
