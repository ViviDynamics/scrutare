"""Read an account's sales region. Rows not yet backfilled read as 'unknown'."""


def region_of(connection, account_id):
    row = connection.execute(
        "SELECT COALESCE(region, 'unknown') FROM accounts WHERE id = ?",
        (account_id,)).fetchone()
    return row[0] if row else None
