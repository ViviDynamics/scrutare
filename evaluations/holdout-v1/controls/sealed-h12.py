import sqlite3

from src.app import remove
from src.contracts import SCHEMA

db = sqlite3.connect(':memory:')
db.execute(SCHEMA)
db.executemany('INSERT INTO items VALUES (?, ?)', [('a', 7), ('b', 7)])
remove(db, 'a', 7)
assert db.execute('SELECT tenant FROM items').fetchall() == [('b',)]
