"""Add orders.status. New application writes set it from this release on."""


def upgrade(connection):
    connection.execute("ALTER TABLE orders ADD COLUMN status TEXT")
    connection.commit()
