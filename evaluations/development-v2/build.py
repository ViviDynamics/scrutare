"""Rebuild the original development-v2 fixtures from this explicit catalog.

Run `python evaluations/development-v2/build.py` from the repository root. The
script writes sources, labels, controls, Git-generated captures and the manifest,
then asks `verify.py` for the mechanical report and seals the byte inventory.
It executes only the original trusted fixture code defined in this file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPOSITORY = 'development-fixtures/local'
PROVENANCE = {
    'author': 'ViviDynamics Scrutare contributors', 'external_material': False,
    'license': 'Elastic-2.0', 'origin': 'original',
    'source': 'Original purpose-written development example; no external PR or code copied.',
}
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Scrutare Fixtures', 'GIT_AUTHOR_EMAIL': 'fixtures@scrutare.invalid',
    'GIT_COMMITTER_NAME': 'Scrutare Fixtures', 'GIT_COMMITTER_EMAIL': 'fixtures@scrutare.invalid',
    'GIT_AUTHOR_DATE': '2026-10-13T00:00:00+00:00',
    'GIT_COMMITTER_DATE': '2026-10-13T00:00:00+00:00',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
}

REENTRANT_HOOK_LOCK = '''

class HookLock:
    """Deterministic stand-in for a lock: runs one competing call when first entered."""

    def __init__(self):
        self.hook = None

    def __enter__(self):
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        return self

    def __exit__(self, *exc):
        return False
'''

CASES: list[dict[str, Any]] = [
    {
        'id': 'dev2-01', 'domain': 'performance-concurrency',
        'routing': {'class': 'covered', 'expected_specialists': ['performance-concurrency']},
        'base': {'src/concurrency/counter.py': '''\
"""Shared hit counter. Callers may invoke `add` from several threads."""
import threading


class HitCounter:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._value = 0

    def add(self, amount):
        with self._lock:
            self._value = self._value + amount
            return self._value

    def value(self):
        with self._lock:
            return self._value
'''},
        'head': {'src/concurrency/counter.py': '''\
"""Shared hit counter. Callers may invoke `add` from several threads."""
import threading


class HitCounter:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._value = 0

    def add(self, amount):
        updated = self._value + amount
        with self._lock:
            self._value = updated
            return updated

    def value(self):
        with self._lock:
            return self._value
'''},
        'defects': [{
            'id': 'dev2-01-defect',
            'trigger': 'Two threads call add concurrently; one reads the value before the '
                       'other finishes its locked write.',
            'impact': 'An increment is lost and the counter under-reports.',
            'evidence': 'counter.py add now computes self._value + amount before acquiring '
                        'the lock and writes that stale sum inside it.',
            'match_criteria': 'Identify the read of the shared value outside the lock, or the '
                              'resulting lost update.',
            'fix_or_counterexample': 'Read and write self._value inside the same locked block.',
        }],
        'counterexamples': ['A single-threaded caller still sees correct totals.'],
        'control': '''
import sys

sys.path.insert(0, "src/concurrency")
from counter import HitCounter
''' + REENTRANT_HOOK_LOCK + '''

lock = HookLock()
counter = HitCounter(lock)
lock.hook = lambda: counter.add(5)
counter.add(1)
assert counter.value() == 6, counter.value()
''',
    },
    {
        'id': 'dev2-02', 'domain': 'performance-concurrency',
        'routing': {'class': 'covered', 'expected_specialists': ['performance-concurrency']},
        'base': {'src/profile_cache.py': '''\
"""Per-tenant profile cache in front of a slow directory lookup."""


class ProfileCache:
    def __init__(self, loader):
        self._loader = loader
        self._entries = {}

    def get(self, tenant_id, user_id):
        key = (tenant_id, user_id)
        if key not in self._entries:
            self._entries[key] = self._loader(tenant_id, user_id)
        return self._entries[key]

    def invalidate(self, tenant_id, user_id):
        self._entries.pop((tenant_id, user_id), None)
'''},
        'head': {'src/profile_cache.py': '''\
"""Per-tenant profile cache in front of a slow directory lookup."""


class ProfileCache:
    def __init__(self, loader):
        self._loader = loader
        self._entries = {}

    def get(self, tenant_id, user_id):
        if user_id not in self._entries:
            self._entries[user_id] = self._loader(tenant_id, user_id)
        return self._entries[user_id]

    def invalidate(self, tenant_id, user_id):
        self._entries.pop(user_id, None)
'''},
        'defects': [{
            'id': 'dev2-02-defect',
            'trigger': 'Two tenants look up the same user_id.',
            'impact': 'The second tenant receives the first tenant\'s cached profile.',
            'evidence': 'profile_cache.py get and invalidate now key entries by user_id alone; '
                        'the loader still depends on tenant_id.',
            'match_criteria': 'Identify that the cache key no longer includes the tenant, or the '
                              'resulting cross-tenant result.',
            'fix_or_counterexample': 'Key entries by (tenant_id, user_id).',
        }],
        'counterexamples': ['A deployment with one tenant observes no difference.'],
        'control': '''
import sys

sys.path.insert(0, "src")
from profile_cache import ProfileCache

cache = ProfileCache(lambda tenant, user: f"{tenant}:{user}")
assert cache.get("a", 1) == "a:1"
assert cache.get("b", 1) == "b:1", cache.get("b", 1)
''',
    },
    {
        'id': 'dev2-03', 'domain': 'performance-concurrency',
        'routing': {'class': 'covered', 'expected_specialists': ['performance-concurrency']},
        'base': {
            'src/task_queue.py': '''\
"""FIFO batch buffer. Not internally synchronized: the owner must hold its lock.

`Worker` in worker.py is the only owner and wraps every call in `Worker._lock`.
"""


class TaskQueue:
    def __init__(self):
        self._items = []

    def put(self, item):
        self._items.append(item)

    def drain(self, limit):
        batch = []
        while self._items and len(batch) < limit:
            batch.append(self._items.pop(0))
        return batch

    def __len__(self):
        return len(self._items)
''',
            'src/worker.py': '''\
"""Single owner of a TaskQueue; serializes every queue operation."""
import threading

from task_queue import TaskQueue


class Worker:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue = TaskQueue()

    def submit(self, item):
        with self._lock:
            self._queue.put(item)

    def next_batch(self, limit):
        with self._lock:
            return self._queue.drain(limit)

    def pending(self):
        with self._lock:
            return len(self._queue)
''',
        },
        'head': {
            'src/task_queue.py': '''\
"""FIFO batch buffer. Not internally synchronized: the owner must hold its lock.

`Worker` in worker.py is the only owner and wraps every call in `Worker._lock`.
"""
from collections import deque


class TaskQueue:
    def __init__(self):
        self._items = deque()

    def put(self, item):
        self._items.append(item)

    def drain(self, limit):
        batch = []
        while self._items and len(batch) < limit:
            batch.append(self._items.popleft())
        return batch

    def __len__(self):
        return len(self._items)
''',
            'src/worker.py': None,
        },
        'defects': [],
        'counterexamples': [
            'Order, the limit and the reported length are unchanged; only the removal cost '
            'at the front of the buffer changes.'],
        'tempting_allegations': [{
            'allegation': 'drain checks the buffer and then pops without a lock, so two '
                          'threads can race.',
            'why_wrong': 'The buffer was equally unsynchronized before the change, its public '
                         'docstring assigns locking to the owner, and the only owner '
                         '(worker.py) holds its lock around every call.',
            'evidence': 'task_queue.py module docstring in both revisions; worker.py '
                        'next_batch, submit and pending.',
        }],
        'unchanged_supporting_paths': ['src/worker.py'],
        'control': '''
import sys

sys.path.insert(0, "src")
from worker import Worker

worker = Worker()
for item in range(5):
    worker.submit(item)
assert worker.next_batch(2) == [0, 1]
assert worker.pending() == 3
assert worker.next_batch(10) == [2, 3, 4]
assert worker.next_batch(1) == [] and worker.pending() == 0
''',
    },
    {
        'id': 'dev2-04', 'domain': 'performance-concurrency',
        'routing': {'class': 'covered', 'expected_specialists': ['performance-concurrency']},
        'base': {'src/async_fetch.py': '''\
"""Fetch many resources with a bounded number of concurrent requests."""
import asyncio


async def fetch_all(keys, fetch, limit):
    semaphore = asyncio.Semaphore(limit)

    async def one(key):
        async with semaphore:
            return await fetch(key)

    return await asyncio.gather(*(one(key) for key in keys))
'''},
        'head': {'src/async_fetch.py': '''\
"""Fetch many resources with a bounded number of concurrent requests."""
import asyncio


async def _bounded(key, fetch, limit):
    async with asyncio.Semaphore(limit):
        return await fetch(key)


async def fetch_all(keys, fetch, limit):
    return await asyncio.gather(*(_bounded(key, fetch, limit) for key in keys))
'''},
        'defects': [{
            'id': 'dev2-04-defect',
            'trigger': 'fetch_all is called with more keys than limit.',
            'impact': 'Every request runs at once; the concurrency bound no longer protects '
                      'the upstream service.',
            'evidence': 'async_fetch.py _bounded creates a new Semaphore(limit) per key, so no '
                        'two requests share one.',
            'match_criteria': 'Identify that the semaphore is no longer shared across requests, '
                              'or that the limit is not enforced.',
            'fix_or_counterexample': 'Create one semaphore in fetch_all and share it.',
        }],
        'counterexamples': ['Results and their order are unchanged.'],
        'control': '''
import asyncio
import sys

sys.path.insert(0, "src")
from async_fetch import fetch_all

state = {"active": 0, "peak": 0}


async def fetch(key):
    state["active"] += 1
    state["peak"] = max(state["peak"], state["active"])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    state["active"] -= 1
    return key * 2


assert asyncio.run(fetch_all(range(6), fetch, 2)) == [0, 2, 4, 6, 8, 10]
assert state["peak"] <= 2, state["peak"]
''',
    },
    {
        'id': 'dev2-05', 'domain': 'data-integrity',
        'routing': {'class': 'covered', 'expected_specialists': ['data-integrity-migrations']},
        'base': {
            'src/migrations/0006_add_status.py': '''\
"""Add orders.status. New application writes set it from this release on."""


def upgrade(connection):
    connection.execute("ALTER TABLE orders ADD COLUMN status TEXT")
    connection.commit()
''',
            'src/migrations/0007_backfill_status.py': '''\
"""Backfill orders.status for rows written before 0006 added the column."""

BATCH = 500


def upgrade(connection):
    while True:
        cursor = connection.execute(
            "UPDATE orders SET status = 'open' WHERE id IN ("
            "SELECT id FROM orders WHERE status IS NULL LIMIT ?)",
            (BATCH,),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return
''',
        },
        'head': {
            'src/migrations/0006_add_status.py': None,
            'src/migrations/0007_backfill_status.py': '''\
"""Backfill orders.status for rows written before 0006 added the column."""


def upgrade(connection):
    connection.execute("UPDATE orders SET status = 'open'")
    connection.commit()
''',
        },
        'defects': [{
            'id': 'dev2-05-defect',
            'trigger': 'The backfill runs after the application has written rows with a real '
                       'status such as closed.',
            'impact': 'Every existing status is overwritten with open; the original values are '
                      'lost.',
            'evidence': '0007_backfill_status.py now updates every row; the status IS NULL '
                        'restriction is gone. 0006 states new writes already set status.',
            'match_criteria': 'Identify that the update is no longer restricted to rows without '
                              'a status, or that existing statuses are overwritten.',
            'fix_or_counterexample': 'Restrict the update to rows where status IS NULL.',
        }],
        'counterexamples': ['A table whose rows all have NULL status ends in the same state.'],
        'defensible_observations': [
            'The head also drops batching, so one statement updates and locks the whole '
            'table. This is a reasonable separate remark, not the labeled defect; it needs '
            'a human decision.'],
        'unchanged_supporting_paths': ['src/migrations/0006_add_status.py'],
        'control': '''
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
''',
    },
    {
        'id': 'dev2-06', 'domain': 'data-integrity',
        'routing': {'class': 'covered', 'expected_specialists': ['data-integrity-migrations']},
        'base': {'src/ledger_transaction.py': '''\
"""Move money between accounts atomically."""


class UnknownAccount(Exception):
    pass


def _adjust(connection, account_id, delta):
    cursor = connection.execute(
        "UPDATE accounts SET balance = balance + ? WHERE id = ?", (delta, account_id)
    )
    if cursor.rowcount != 1:
        raise UnknownAccount(account_id)


def transfer(connection, source, target, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    try:
        _adjust(connection, source, -amount)
        _adjust(connection, target, amount)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
'''},
        'head': {'src/ledger_transaction.py': '''\
"""Move money between accounts atomically."""


class UnknownAccount(Exception):
    pass


def _adjust(connection, account_id, delta):
    cursor = connection.execute(
        "UPDATE accounts SET balance = balance + ? WHERE id = ?", (delta, account_id)
    )
    if cursor.rowcount != 1:
        raise UnknownAccount(account_id)
    connection.execute(
        "INSERT INTO audit(account_id, delta) VALUES (?, ?)", (account_id, delta)
    )
    connection.commit()


def transfer(connection, source, target, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    try:
        _adjust(connection, source, -amount)
        _adjust(connection, target, amount)
    except Exception:
        connection.rollback()
        raise
    connection.commit()
'''},
        'defects': [{
            'id': 'dev2-06-defect',
            'trigger': 'The debit succeeds and the credit fails, for example an unknown target '
                       'account.',
            'impact': 'The debit is already committed; the rollback cannot undo it and money '
                      'leaves the source without arriving anywhere.',
            'evidence': 'ledger_transaction.py _adjust now commits after each adjustment, so '
                        'transfer is no longer one transaction.',
            'match_criteria': 'Identify the commit between debit and credit, or the resulting '
                              'partial transfer.',
            'fix_or_counterexample': 'Write the audit rows without committing; commit once in '
                                     'transfer.',
        }],
        'counterexamples': ['A transfer between two existing accounts ends with the same '
                            'balances.'],
        'control': '''
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
''',
    },
    {
        'id': 'dev2-07', 'domain': 'data-integrity',
        'routing': {'class': 'covered', 'expected_specialists': ['data-integrity-migrations']},
        'base': {'src/migrations/0008_add_region.py': '''\
"""Add the region column to every regional table."""

REGIONAL_TABLES = ("orders", "invoices")


def upgrade(connection):
    connection.execute("ALTER TABLE orders ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.execute("ALTER TABLE invoices ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.commit()
'''},
        'head': {'src/migrations/0008_add_region.py': '''\
"""Add the region column to every regional table."""

REGIONAL_TABLES = ("orders", "invoices")


def upgrade(connection):
    for table in REGIONAL_TABLES:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN region TEXT NOT NULL DEFAULT 'eu'")
    connection.commit()
'''},
        'defects': [],
        'counterexamples': [
            'Both tables receive the same column with the same default in both revisions.'],
        'tempting_allegations': [{
            'allegation': 'Building SQL with an f-string is an injection vulnerability.',
            'why_wrong': 'The only interpolated value is a table name from the module constant '
                         'REGIONAL_TABLES; no caller or external input reaches it, and SQL '
                         'identifiers cannot be bound as parameters.',
            'evidence': '0008_add_region.py REGIONAL_TABLES and the loop in upgrade.',
        }],
        'control': '''
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
''',
    },
    {
        'id': 'dev2-08', 'domain': 'data-integrity',
        'routing': {'class': 'covered', 'expected_specialists': ['data-integrity-migrations']},
        'base': {
            'src/database_writer.py': '''\
"""Append events. `events.idempotency_key` is the primary key (see tables.sql).

Each writer owns its connection: nothing else is ever pending on it.
"""


def record_event(connection, idempotency_key, payload):
    connection.execute(
        "INSERT OR IGNORE INTO events(idempotency_key, payload) VALUES (?, ?)",
        (idempotency_key, payload),
    )
    connection.commit()
''',
            'src/tables.sql': '''\
CREATE TABLE events (
    idempotency_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
''',
        },
        'head': {
            'src/database_writer.py': '''\
"""Append events. `events.idempotency_key` is the primary key (see tables.sql).

Each writer owns its connection: nothing else is ever pending on it.
"""
import sqlite3
import time

ATTEMPTS = 3


def record_event(connection, idempotency_key, payload, sleep=time.sleep):
    for attempt in range(ATTEMPTS):
        try:
            connection.execute(
                "INSERT OR IGNORE INTO events(idempotency_key, payload) VALUES (?, ?)",
                (idempotency_key, payload),
            )
            connection.commit()
            return
        except sqlite3.OperationalError:
            connection.rollback()
            if attempt == ATTEMPTS - 1:
                raise
            sleep(0.05 * (attempt + 1))
''',
            'src/tables.sql': None,
        },
        'defects': [],
        'counterexamples': [
            'Recording one key twice, or retrying after a commit whose acknowledgement was '
            'lost, leaves exactly one row.'],
        'tempting_allegations': [{
            'allegation': 'Retrying the insert can write the same event twice.',
            'why_wrong': 'The statement is INSERT OR IGNORE and idempotency_key is the primary '
                         'key, so a repeated execution is a no-op.',
            'evidence': 'database_writer.py statement text; tables.sql primary key.',
        }],
        'defensible_observations': [
            'The retry also repeats OperationalError causes that cannot succeed, such as a '
            'missing table, before raising. This is a reasonable remark, not a labeled '
            'defect and not the tempting allegation; it needs a human decision.'],
        'unchanged_supporting_paths': ['src/tables.sql'],
        'control': '''
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
''',
    },
    {
        'id': 'dev2-09', 'domain': 'performance-concurrency',
        'routing': {'class': 'missed', 'expected_specialists': []},
        'base': {'src/jobs/dispatcher.py': '''\
"""Assign each job to exactly one worker. `claim` is called from worker threads."""
import threading


class Dispatcher:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._owners = {}

    def claim(self, job_id, worker):
        with self._lock:
            if job_id in self._owners:
                return False
            self._owners[job_id] = worker
            return True

    def owner(self, job_id):
        with self._lock:
            return self._owners.get(job_id)
'''},
        'head': {'src/jobs/dispatcher.py': '''\
"""Assign each job to exactly one worker. `claim` is called from worker threads."""
import threading


class Dispatcher:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._owners = {}

    def claim(self, job_id, worker):
        if job_id in self._owners:
            return False
        with self._lock:
            self._owners[job_id] = worker
            return True

    def owner(self, job_id):
        with self._lock:
            return self._owners.get(job_id)
'''},
        'defects': [{
            'id': 'dev2-09-defect',
            'trigger': 'Two workers claim the same unowned job at the same time.',
            'impact': 'Both receive True and run the job; the second silently replaces the '
                      'first as owner.',
            'evidence': 'dispatcher.py claim now tests membership before taking the lock and '
                        'does not re-check inside it.',
            'match_criteria': 'Identify the membership check outside the lock without a '
                              're-check, or the resulting double claim.',
            'fix_or_counterexample': 'Check and assign inside one locked block.',
        }],
        'counterexamples': ['Claims that do not overlap in time behave as before.'],
        'control': '''
import sys

sys.path.insert(0, "src/jobs")
from dispatcher import Dispatcher
''' + REENTRANT_HOOK_LOCK + '''

lock = HookLock()
dispatcher = Dispatcher(lock)
results = []
lock.hook = lambda: results.append(dispatcher.claim("job", "b"))
results.append(dispatcher.claim("job", "a"))
assert sorted(results) == [False, True], results
''',
    },
    {
        'id': 'dev2-10', 'domain': 'data-integrity',
        'routing': {'class': 'missed', 'expected_specialists': []},
        'base': {
            'src/billing/orders.py': '''\
"""Create or update an order header without touching its line items."""


def save_order(connection, order_id, customer, note):
    connection.execute(
        "INSERT INTO orders(id, customer, note) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET customer = excluded.customer, note = excluded.note",
        (order_id, customer, note),
    )
    connection.commit()
''',
            'src/billing/tables.py': '''\
"""Billing tables. Line items are owned by their order and removed with it."""

DDL = (
    "CREATE TABLE orders (id TEXT PRIMARY KEY, customer TEXT NOT NULL, note TEXT)",
    "CREATE TABLE line_items ("
    "id INTEGER PRIMARY KEY, "
    "order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE, "
    "sku TEXT NOT NULL)",
)


def create(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    for statement in DDL:
        connection.execute(statement)
    connection.commit()
''',
        },
        'head': {
            'src/billing/orders.py': '''\
"""Create or update an order header without touching its line items."""


def save_order(connection, order_id, customer, note):
    connection.execute(
        "INSERT OR REPLACE INTO orders(id, customer, note) VALUES (?, ?, ?)",
        (order_id, customer, note),
    )
    connection.commit()
''',
            'src/billing/tables.py': None,
        },
        'defects': [{
            'id': 'dev2-10-defect',
            'trigger': 'save_order is called for an order that already has line items.',
            'impact': 'REPLACE deletes the existing order row first; the ON DELETE CASCADE '
                      'foreign key removes all of its line items.',
            'evidence': 'orders.py now uses INSERT OR REPLACE; tables.py declares '
                        'line_items.order_id with ON DELETE CASCADE and enables foreign keys.',
            'match_criteria': 'Identify that REPLACE deletes and reinserts the row, or that '
                              'dependent line items are lost.',
            'fix_or_counterexample': 'Keep the ON CONFLICT DO UPDATE upsert.',
        }],
        'counterexamples': ['Saving a new order, or one without line items, ends in the same '
                            'state.'],
        'unchanged_supporting_paths': ['src/billing/tables.py'],
        'control': '''
import sqlite3
import sys

sys.path.insert(0, "src/billing")
import orders
import tables

connection = sqlite3.connect(":memory:")
tables.create(connection)
orders.save_order(connection, "o1", "ada", None)
connection.execute("INSERT INTO line_items(order_id, sku) VALUES ('o1', 'widget')")
connection.commit()
orders.save_order(connection, "o1", "ada", "gift")
assert connection.execute("SELECT note FROM orders").fetchall() == [("gift",)]
items = connection.execute("SELECT order_id, sku FROM line_items").fetchall()
assert items == [("o1", "widget")], items
''',
    },
    {
        'id': 'dev2-11', 'domain': 'performance-concurrency',
        'routing': {'class': 'missed', 'expected_specialists': []},
        'base': {'src/reports/summary.py': '''\
"""Summarize order totals per customer name.

`load_customers` fetches the whole customer directory in one remote call.
"""


def totals_by_customer(orders, load_customers):
    customers = load_customers()
    totals = {}
    for order in orders:
        name = customers[order["customer_id"]]
        totals[name] = totals.get(name, 0) + order["total"]
    return totals
'''},
        'head': {'src/reports/summary.py': '''\
"""Summarize order totals per customer name.

`load_customers` fetches the whole customer directory in one remote call.
"""


def totals_by_customer(orders, load_customers):
    totals = {}
    for order in orders:
        name = load_customers()[order["customer_id"]]
        totals[name] = totals.get(name, 0) + order["total"]
    return totals
'''},
        'defects': [{
            'id': 'dev2-11-defect',
            'trigger': 'A report over many orders.',
            'impact': 'The whole customer directory is fetched remotely once per order instead '
                      'of once per report.',
            'evidence': 'summary.py now calls load_customers() inside the loop; its docstring '
                        'says each call is a full remote fetch.',
            'match_criteria': 'Identify the repeated directory fetch inside the loop.',
            'fix_or_counterexample': 'Call load_customers once before the loop.',
        }],
        'counterexamples': ['The returned totals are identical.'],
        'control': '''
import sys

sys.path.insert(0, "src/reports")
from summary import totals_by_customer

calls = []


def load_customers():
    calls.append(1)
    return {1: "ada", 2: "lin"}


orders = [{"customer_id": 1 + n % 2, "total": 10} for n in range(6)]
assert totals_by_customer(orders, load_customers) == {"ada": 30, "lin": 30}
assert len(calls) == 1, len(calls)
''',
    },
    {
        'id': 'dev2-12', 'domain': 'correctness',
        'routing': {'class': 'none', 'expected_specialists': []},
        'base': {
            'src/stats/ratio.py': '''\
"""Hit ratio of a sample window."""
from window import Window


def hit_ratio(window):
    if not isinstance(window, Window):
        raise TypeError("hit_ratio requires a Window")
    if window.total == 0:
        return 0.0
    return window.hits / window.total
''',
            'src/stats/window.py': '''\
"""A non-empty sample window. Construction rejects empty input."""


class Window:
    def __init__(self, samples):
        samples = tuple(samples)
        if not samples:
            raise ValueError("window requires at least one sample")
        self._samples = samples

    @property
    def total(self):
        return len(self._samples)

    @property
    def hits(self):
        return sum(1 for sample in self._samples if sample)
''',
        },
        'head': {
            'src/stats/ratio.py': '''\
"""Hit ratio of a sample window."""
from window import Window


def hit_ratio(window):
    if not isinstance(window, Window):
        raise TypeError("hit_ratio requires a Window")
    return window.hits / window.total
''',
            'src/stats/window.py': None,
        },
        'defects': [],
        'counterexamples': [
            'Every accepted argument is a Window, whose total is at least one; the removed '
            'branch was unreachable.'],
        'tempting_allegations': [{
            'allegation': 'Removing the zero check allows a ZeroDivisionError.',
            'why_wrong': 'hit_ratio rejects anything that is not a Window, and Window refuses '
                         'to be constructed without samples, so total is never zero.',
            'evidence': 'ratio.py isinstance guard in both revisions; window.py constructor.',
        }],
        'clean_label_requires_context': True,
        'unchanged_supporting_paths': ['src/stats/window.py'],
        'control': '''
import sys

sys.path.insert(0, "src/stats")
from ratio import hit_ratio
from window import Window

assert hit_ratio(Window([1, 0, 1, 1])) == 0.75
assert hit_ratio(Window([0])) == 0.0
for invalid in (lambda: Window([]), lambda: hit_ratio(object())):
    try:
        invalid()
    except (ValueError, TypeError):
        continue
    raise AssertionError("invalid input must be rejected")
''',
    },
]


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(['git', *arguments], cwd=directory, check=True, capture_output=True,
                          text=True, env={'PATH': os.environ['PATH'], **GIT_ENV}).stdout


def commit_pair(base: Path, head: Path) -> tuple[str, str, str]:
    """Real deterministic local commits and the exact patch Git derives from them."""
    with tempfile.TemporaryDirectory(prefix='scrutare-dev2-') as temporary:
        work = Path(temporary) / 'repo'
        work.mkdir()
        git(work, 'init', '--quiet', '--initial-branch=main')
        revisions = []
        for message, tree in (('base', base), ('head', head)):
            for child in work.iterdir():
                if child.name != '.git':
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            shutil.copytree(tree, work, dirs_exist_ok=True)
            # Copied files keep their timestamps; drop the stat cache so Git rehashes them.
            (work / '.git' / 'index').unlink(missing_ok=True)
            git(work, 'add', '--all')
            git(work, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--message', message)
            revisions.append(git(work, 'rev-parse', 'HEAD').strip())
        patch = git(work, '-c', 'core.quotepath=false', 'diff', '--no-color', '--no-ext-diff',
                    '--full-index', '--no-renames', revisions[0], revisions[1])
    return revisions[0], revisions[1], patch


def dump(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')


def build() -> None:
    for name in ('sources', 'captures', 'labels', 'controls'):
        shutil.rmtree(ROOT / name, ignore_errors=True)
    manifest = []
    related: set[str] = set()
    for number, case in enumerate(CASES, start=1):
        identifier = str(case['id'])
        base = dict(case['base'])
        head = {path: base[path] if text is None else text
                for path, text in dict(case['head']).items()}
        for side, files in (('base', base), ('head', head)):
            for path, text in files.items():
                target = ROOT / 'sources' / identifier / side / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
        base_sha, head_sha, patch = commit_pair(ROOT / 'sources' / identifier / 'base',
                                                ROOT / 'sources' / identifier / 'head')
        changed = sorted(path for path in head if head[path] != base.get(path))
        capture = ROOT / 'captures' / identifier
        capture.mkdir(parents=True)
        (capture / 'diff.patch').write_text(patch)
        dump(capture / 'files.json', [{'filename': path, 'status': 'modified'}
                                      for path in changed])
        identity = {'full_name': REPOSITORY}
        dump(capture / 'metadata.json', {
            'schema_version': 1, 'status': 'ingested', 'repository': REPOSITORY,
            'pr_number': number, 'head_sha': head_sha,
            'pull_request': {'number': number, 'state': 'open', 'merged': False,
                             'changed_files': len(changed),
                             'base': {'ref': 'main', 'sha': base_sha, 'repo': identity},
                             'head': {'sha': head_sha, 'repo': identity}}})
        supporting = list(case.get('unchanged_supporting_paths', []))
        related.update(supporting)
        dump(ROOT / 'labels' / f'{identifier}.json', {
            'case_id': identifier, 'defects': case['defects'],
            'counterexamples': case['counterexamples'],
            'tempting_allegations': case.get('tempting_allegations', []),
            'defensible_observations': case.get('defensible_observations', []),
            'clean_label_requires_context': case.get('clean_label_requires_context', False),
            'unchanged_supporting_paths': supporting,
            'routing_expectation': case['routing']})
        control = ROOT / 'controls' / f'{identifier}.py'
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text(str(case['control']).lstrip('\n'))
        manifest.append({
            'id': identifier, 'split': 'development', 'domain': case['domain'],
            'capture': f'captures/{identifier}', 'labels': f'labels/{identifier}.json',
            'provenance': PROVENANCE,
            'context_sources': {side: {'directory': f'sources/{identifier}/{side}',
                                       'repository': REPOSITORY, 'revision': revision}
                                for side, revision in (('base', base_sha), ('head', head_sha))}})
    dump(ROOT / 'manifest.json', {'schema_version': 1, 'cases': manifest})
    dump(ROOT / 'related-paths.json', sorted(related))
    for name in ('freeze-sha256.json', 'mechanical-report.json'):
        (ROOT / name).unlink(missing_ok=True)
    sys.path.insert(0, str(ROOT))
    import verify
    dump(ROOT / 'freeze-sha256.json', verify.inventory(ROOT))
    dump(ROOT / 'mechanical-report.json', verify.verify(ROOT))


if __name__ == '__main__':
    build()
    print(json.dumps({'cases': len(CASES), 'inventory_sha256': hashlib.sha256(
        (ROOT / 'freeze-sha256.json').read_bytes()).hexdigest()}))
