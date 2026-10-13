import sqlite3
import sys

sys.path.insert(0, "src/billing")
import orders
import tables

connection = sqlite3.connect(":memory:")
tables.create(connection)
orders.save_order(connection, "o1", "ada", None)
connection.execute("INSERT INTO line_items(order_id, sku) VALUES ('o1', 'widget')")
connection.commit()
orders.save_order(connection, "o1", "ada", "gift")
assert connection.execute("SELECT note FROM orders").fetchall() == [("gift",)]
items = connection.execute("SELECT order_id, sku FROM line_items").fetchall()
assert items == [("o1", "widget")], items
