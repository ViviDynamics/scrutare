import importlib.util
import sqlite3
import sys

sys.path.insert(0, "src")
from region_reader import region_of

spec = importlib.util.spec_from_file_location(
    "backfill", "src/migrations/0009_backfill_region.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class Crash(Exception):
    pass


class CrashAfterFirstCommit:
    """Delegates to a real connection and fails once, just after its first commit."""

    def __init__(self, connection):
        self.connection, self.commits = connection, 0

    def execute(self, *arguments):
        if self.commits:
            raise Crash
        return self.connection.execute(*arguments)

    def commit(self):
        self.connection.commit()
        self.commits += 1


connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE country_regions (country TEXT PRIMARY KEY, region TEXT)")
connection.execute(
    "CREATE TABLE accounts (id INTEGER PRIMARY KEY, country TEXT, region TEXT)")
connection.executemany("INSERT INTO country_regions VALUES (?, ?)",
                       [("DE", "emea"), ("US", "amer")])
countries = ["DE", "US", "ZZ", "DE", "US", "ZZ", "DE"]
connection.executemany("INSERT INTO accounts (country) VALUES (?)", [(c,) for c in countries])
connection.commit()
try:
    if "batch" in backfill.upgrade.__code__.co_varnames:
        backfill.upgrade(CrashAfterFirstCommit(connection), batch=3)
    else:
        backfill.upgrade(CrashAfterFirstCommit(connection))
except Crash:
    connection.rollback()
expected = {"DE": "emea", "US": "amer", "ZZ": "unknown"}
for account_id, country in enumerate(countries, start=1):
    assert region_of(connection, account_id) in (expected[country], "unknown")
backfill.upgrade(connection)
backfill.upgrade(connection)
rows = connection.execute("SELECT country, region FROM accounts ORDER BY id").fetchall()
assert rows == [(c, expected[c]) for c in countries], rows
