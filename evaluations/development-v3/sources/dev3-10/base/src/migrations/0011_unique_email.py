"""Enforce one account per e-mail address."""


def upgrade(connection):
    # Addresses stored before signup normalised them can differ only by case.
    # Keep the oldest account and park the others on a placeholder for support to merge.
    connection.execute(
        "UPDATE accounts SET email = 'duplicate-' || id || '@invalid' WHERE id NOT IN ("
        "SELECT MIN(id) FROM accounts GROUP BY lower(email))")
    connection.execute("CREATE UNIQUE INDEX accounts_email_unique ON accounts (lower(email))")
    connection.commit()
