import sqlite3

from src.migrations.store import store

db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE events (value INTEGER UNIQUE)')
store(db, 9)
try:
    store(db, 9)
except sqlite3.IntegrityError:
    pass
assert db.execute('SELECT value FROM events').fetchall() == [(9,)]
assert not db.in_transaction
