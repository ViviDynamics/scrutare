import sqlite3

from src.app import find

db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE users (name TEXT)')
db.execute("INSERT INTO users VALUES ('alice')")
assert find(db, "' OR 1=1 --") == []
assert find(db, 'alice') == [('alice',)]
