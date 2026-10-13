"""Add the region column to every regional table."""

REGIONAL_TABLES = ("orders", "invoices")


def upgrade(connection):
    connection.execute("ALTER TABLE orders ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.execute("ALTER TABLE invoices ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.commit()
