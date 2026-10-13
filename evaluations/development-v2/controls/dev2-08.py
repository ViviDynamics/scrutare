import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "src")
import database_writer


class LostAcknowledgement:
    """Commit really happens, then the first acknowledgement is reported as an error."""

    def __init__(self, connection):
        self._connection = connection
        self.fail = True

    def execute(self, *args):
        return self._connection.execute(*args)

    def rollback(self):
        self._connection.rollback()

    def commit(self):
        self._connection.commit()
        if self.fail:
            self.fail = False
            raise sqlite3.OperationalError("acknowledgement lost")


connection = sqlite3.connect(":memory:")
connection.executescript(Path("src/tables.sql").read_text())
database_writer.record_event(connection, "k1", "first")
database_writer.record_event(connection, "k1", "again")
assert connection.execute("SELECT * FROM events").fetchall() == [("k1", "first")]
if hasattr(database_writer, "ATTEMPTS"):
    database_writer.record_event(LostAcknowledgement(connection), "k2", "x", sleep=lambda s: None)
    rows = connection.execute("SELECT * FROM events ORDER BY 1").fetchall()
    assert rows == [("k1", "first"), ("k2", "x")], rows
