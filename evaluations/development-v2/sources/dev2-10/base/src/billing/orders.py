"""Create or update an order header without touching its line items."""


def save_order(connection, order_id, customer, note):
    connection.execute(
        "INSERT INTO orders(id, customer, note) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET customer = excluded.customer, note = excluded.note",
        (order_id, customer, note),
    )
    connection.commit()
