import sqlite3

from src.migrations.upgrade import migrate

db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE legacy (value INTEGER)')
db.execute('INSERT INTO legacy VALUES (17)')
db.execute('CREATE TABLE current (value INTEGER)')
migrate(db)
assert db.execute('SELECT value FROM current').fetchall() == [(17,)]
