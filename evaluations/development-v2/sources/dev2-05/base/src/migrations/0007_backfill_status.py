"""Backfill orders.status for rows written before 0006 added the column."""

BATCH = 500


def upgrade(connection):
    while True:
        cursor = connection.execute(
            "UPDATE orders SET status = 'open' WHERE id IN ("
            "SELECT id FROM orders WHERE status IS NULL LIMIT ?)",
            (BATCH,),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return
