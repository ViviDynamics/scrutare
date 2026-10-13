"""Create or update an order header without touching its line items."""


def save_order(connection, order_id, customer, note):
    connection.execute(
        "INSERT OR REPLACE INTO orders(id, customer, note) VALUES (?, ?, ?)",
        (order_id, customer, note),
    )
    connection.commit()
