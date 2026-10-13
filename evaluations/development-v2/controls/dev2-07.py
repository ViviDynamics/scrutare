import runpy
import sqlite3

module = runpy.run_path("src/migrations/0008_add_region.py")
assert module["REGIONAL_TABLES"] == ("orders", "invoices")
connection = sqlite3.connect(":memory:")
for table in ("orders", "invoices"):
    connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    connection.execute(f"INSERT INTO {table}(id) VALUES (1)")
module["upgrade"](connection)
for table in ("orders", "invoices"):
    assert connection.execute(f"SELECT region FROM {table}").fetchall() == [("eu",)]
