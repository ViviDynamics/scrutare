"""Append events. `events.idempotency_key` is the primary key (see tables.sql)."""
import sqlite3
import time

ATTEMPTS = 3


def record_event(connection, idempotency_key, payload, sleep=time.sleep):
    for attempt in range(ATTEMPTS):
        try:
            connection.execute(
                "INSERT OR IGNORE INTO events(idempotency_key, payload) VALUES (?, ?)",
                (idempotency_key, payload),
            )
            connection.commit()
            return
        except sqlite3.OperationalError:
            connection.rollback()
            if attempt == ATTEMPTS - 1:
                raise
            sleep(0.05 * (attempt + 1))
