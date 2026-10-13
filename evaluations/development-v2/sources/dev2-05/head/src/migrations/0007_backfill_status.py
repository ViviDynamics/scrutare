"""Backfill orders.status for rows written before 0006 added the column."""


def upgrade(connection):
    connection.execute("UPDATE orders SET status = 'open'")
    connection.commit()
