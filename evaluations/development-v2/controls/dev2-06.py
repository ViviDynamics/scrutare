import sqlite3
import sys

sys.path.insert(0, "src")
from ledger_transaction import UnknownAccount, transfer

connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, balance INTEGER NOT NULL)")
connection.execute("CREATE TABLE audit (account_id TEXT NOT NULL, delta INTEGER NOT NULL)")
connection.execute("INSERT INTO accounts VALUES ('a', 100), ('b', 0)")
connection.commit()
transfer(connection, "a", "b", 30)
try:
    transfer(connection, "a", "missing", 50)
except UnknownAccount:
    pass
else:
    raise AssertionError("unknown target must fail")
balances = dict(connection.execute("SELECT id, balance FROM accounts"))
assert balances == {"a": 70, "b": 30}, balances
