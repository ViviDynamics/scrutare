"""Billing tables. Line items are owned by their order and removed with it."""

DDL = (
    "CREATE TABLE orders (id TEXT PRIMARY KEY, customer TEXT NOT NULL, note TEXT)",
    "CREATE TABLE line_items ("
    "id INTEGER PRIMARY KEY, "
    "order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE, "
    "sku TEXT NOT NULL)",
)


def create(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    for statement in DDL:
        connection.execute(statement)
    connection.commit()
