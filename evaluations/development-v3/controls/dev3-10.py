import importlib.util
import sqlite3

spec = importlib.util.spec_from_file_location(
    "unique_email", "src/migrations/0011_unique_email.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, email TEXT NOT NULL)")
connection.executemany("INSERT INTO accounts (email) VALUES (?)",
                       [("Ada@example.com",), ("lin@example.com",), ("ada@example.com",)])
connection.commit()
migration.upgrade(connection)
assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone() == (3,)
try:
    connection.execute("INSERT INTO accounts (email) VALUES ('LIN@example.com')")
except sqlite3.IntegrityError:
    pass
else:
    raise AssertionError("unique index must reject a case-variant duplicate")
