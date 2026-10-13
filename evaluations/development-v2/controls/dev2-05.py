import runpy
import sqlite3

connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
connection.executemany("INSERT INTO orders(id) VALUES (?)", [(1,), (2,), (3,)])
runpy.run_path("src/migrations/0006_add_status.py")["upgrade"](connection)
connection.execute("UPDATE orders SET status = 'closed' WHERE id = 2")
connection.commit()
runpy.run_path("src/migrations/0007_backfill_status.py")["upgrade"](connection)
rows = connection.execute("SELECT id, status FROM orders ORDER BY id").fetchall()
assert rows == [(1, "open"), (2, "closed"), (3, "open")], rows
