"""Backfill accounts.region for rows created before the column existed."""

BATCH = 500


def upgrade(connection, batch=BATCH):
    """Backfill in small committed batches so the table is never locked for long."""
    while True:
        cursor = connection.execute(
            "UPDATE accounts SET region = COALESCE((SELECT region FROM country_regions"
            " WHERE country = accounts.country), 'unknown') WHERE id IN ("
            "SELECT id FROM accounts WHERE region IS NULL ORDER BY id LIMIT ?)", (batch,))
        connection.commit()
        if cursor.rowcount == 0:
            break
