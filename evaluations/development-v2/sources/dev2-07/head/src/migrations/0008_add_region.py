"""Add the region column to every regional table."""

REGIONAL_TABLES = ("orders", "invoices")


def upgrade(connection):
    for table in REGIONAL_TABLES:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.commit()
