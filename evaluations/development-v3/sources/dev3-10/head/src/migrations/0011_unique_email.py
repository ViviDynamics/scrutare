"""Enforce one account per e-mail address."""


def upgrade(connection):
    # signup.normalise lowercases every address, so duplicates cannot exist.
    connection.execute("CREATE UNIQUE INDEX accounts_email_unique ON accounts (lower(email))")
    connection.commit()
