"""Backfill accounts.region for rows created before the column existed."""


def upgrade(connection):
    connection.execute(
        "UPDATE accounts SET region = COALESCE((SELECT region FROM country_regions"
        " WHERE country = accounts.country), 'unknown') WHERE region IS NULL")
    connection.commit()
