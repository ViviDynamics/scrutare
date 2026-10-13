"""Original evaluator-only case specifications; never stage this file for models."""

from textwrap import dedent


def case(
    domain,
    name,
    before,
    after,
    control,
    trigger,
    impact,
    fix,
    *,
    dependencies=None,
    path="src/app.py",
    clean=False,
):
    return {
        "id": f"sealed-{domain}-{name}",
        "domain": domain,
        "path": path,
        "base": dedent(before).lstrip(),
        "head": dedent(after).lstrip(),
        "control": dedent(control).lstrip(),
        "trigger": trigger,
        "impact": impact,
        "fix": fix,
        "dependencies": dependencies or {},
        "clean": clean,
    }


CASES = [
    case(
        "correctness",
        "negative-buckets",
        """
        def bucket(value, width):
            return value // width
    """,
        """
        def bucket(value, width):
            return int(value / width)
    """,
        "from src.app import bucket\nassert bucket(-1, 10) == -1\n",
        "bucket(-1, 10)",
        "Negative coordinates enter bucket zero instead of minus one.",
        "Preserve floor division rather than truncation toward zero.",
    ),
    case(
        "correctness",
        "partial-batches",
        """
        from src.contracts import SIZE

        def batches(count):
            return (count + SIZE - 1) // SIZE
    """,
        """
        from src.contracts import SIZE

        def batches(count):
            return count // SIZE
    """,
        "from src.app import batches\nassert batches(11) == 2\n",
        "batches(11) with the unchanged ten-item batch contract",
        "The final partial batch is omitted.",
        "Round the positive count up.",
        dependencies={"src/contracts.py": "SIZE = 10\n"},
    ),
    case(
        "correctness",
        "decimal-rounding",
        """
        from decimal import ROUND_HALF_UP, Decimal

        def cents(text):
            return Decimal(text).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    """,
        """
        from decimal import Decimal

        def cents(text):
            return Decimal(text).quantize(Decimal('0.01'))
    """,
        "from src.app import cents\nassert str(cents('1.225')) == '1.23'\n",
        "An exact half-cent 1.225",
        "Implicit half-even rounding undercharges a cent.",
        "Keep the explicit required half-up rounding mode.",
    ),
    case(
        "correctness",
        "timezone-cutoff",
        """
        from datetime import timezone

        def utc_day(value):
            return value.astimezone(timezone.utc).date().isoformat()
    """,
        """
        def utc_day(value):
            return value.date().isoformat()
    """,
        """
        from datetime import datetime
        from src.app import utc_day
        assert utc_day(datetime.fromisoformat('2024-03-01T00:15:00+02:00')) == '2024-02-29'
    """,
        "A timestamp just after local midnight in UTC+02:00",
        "The UTC reporting day becomes the wrong day.",
        "Normalize to UTC before extracting date.",
    ),
    case(
        "correctness",
        "state-code",
        """
        def ready(state):
            return state == 'ready'
    """,
        """
        def ready(state):
            return state == 'READY'
    """,
        "from src.app import ready\nfrom src.contracts import READY\nassert ready(READY)\n",
        "An unchanged caller uses READY='ready'",
        "Valid ready jobs are rejected.",
        "Preserve the lowercase wire value or migrate all consumers.",
        dependencies={"src/contracts.py": "READY = 'ready'\n"},
    ),
    case(
        "correctness",
        "quotient-remainder-clean",
        """
        def partition(index, width):
            return index // width, index % width
    """,
        """
        def partition(index, width):
            return divmod(index, width)
    """,
        "from src.app import partition\n"
        "assert [partition(n, 10) for n in (0, 11, -1)] == [(0, 0), (1, 1), (-1, 9)]\n",
        "Zero, positive and negative integer coordinates with positive width",
        "Quotient and remainder semantics remain the same.",
        "Integer divmod preserves floor-division and remainder behavior for the contract.",
        clean=True,
    ),
    case(
        "correctness",
        "stable-sort-clean",
        """
        def ordered(rows):
            return sorted(rows, key=lambda row: row[0])
    """,
        """
        from operator import itemgetter

        def ordered(rows):
            return sorted(rows, key=itemgetter(0))
    """,
        """
        from src.app import ordered
        rows = [(1, 'first'), (0, 'zero'), (1, 'second')]
        assert ordered(rows) == [(0, 'zero'), (1, 'first'), (1, 'second')]
        assert rows[0] == (1, 'first')
    """,
        "Equal-key items and original input reuse",
        "Stable order and input ownership remain intact.",
        "Both key functions select the same field; sorted stays stable and nonmutating.",
        clean=True,
    ),
    case(
        "correctness",
        "empty-mean-clean",
        """
        def mean(values):
            return sum(values) / len(values) if values else None
    """,
        """
        def mean(values):
            if not values:
                return None
            return sum(values) / len(values)
    """,
        "from src.app import mean\nassert mean([]) is None\nassert mean([0, 2]) == 1\n",
        "Empty and nonempty values",
        "The empty-input guard remains effective.",
        "The division only runs after the nonempty check.",
        clean=True,
    ),
    case(
        "security",
        "sibling-path",
        """
        from pathlib import PurePosixPath

        def allowed(path, root):
            return PurePosixPath(path).is_relative_to(PurePosixPath(root))
    """,
        """
        def allowed(path, root):
            return path.startswith(root)
    """,
        "from src.app import allowed\nassert not allowed('/data/private2/key', '/data/private')\n",
        "A sibling directory whose spelling shares the allowed prefix",
        "The lexical prefix check permits access outside the allowed directory.",
        "Compare path components, not string prefixes.",
    ),
    case(
        "security",
        "redirect-subdomain",
        """
        from urllib.parse import urlsplit
        from src.contracts import HOST

        def allowed(url):
            return urlsplit(url).hostname == HOST
    """,
        """
        from src.contracts import HOST

        def allowed(url):
            return HOST in url
    """,
        """
        from src.app import allowed
        assert not allowed('https://login.example.invalid.attacker.invalid/')
    """,
        "An attacker-controlled host includes the trusted host as a substring",
        "An external redirect is allowed.",
        "Parse and compare the exact hostname.",
        dependencies={"src/contracts.py": "HOST = 'login.example.invalid'\n"},
    ),
    case(
        "security",
        "role-substring",
        """
        def can_delete(roles):
            return 'admin' in roles.split(',')
    """,
        """
        def can_delete(roles):
            return 'admin' in roles
    """,
        "from src.app import can_delete\nassert not can_delete('nonadmin,viewer')\n",
        "A nonadmin role contains the admin substring",
        "A nonadministrator gets deletion rights.",
        "Match complete role tokens rather than substrings.",
    ),
    case(
        "security",
        "tenant-delete",
        """
        def remove(db, tenant, item):
            db.execute('DELETE FROM items WHERE tenant = ? AND item = ?', (tenant, item))
    """,
        """
        def remove(db, tenant, item):
            db.execute('DELETE FROM items WHERE item = ?', (item,))
    """,
        """
        import sqlite3
        from src.app import remove
        from src.contracts import SCHEMA
        db = sqlite3.connect(':memory:')
        db.execute(SCHEMA)
        db.executemany('INSERT INTO items VALUES (?, ?)', [('a', 7), ('b', 7)])
        remove(db, 'a', 7)
        assert db.execute('SELECT tenant FROM items').fetchall() == [('b',)]
    """,
        "Two tenants share the same item number",
        "A tenant deletes another tenant's data.",
        "Keep tenant identity in the deletion predicate.",
        dependencies={
            "src/contracts.py": "SCHEMA = 'CREATE TABLE items (tenant TEXT, item INTEGER)'\n"
        },
    ),
    case(
        "security",
        "expiry-boundary",
        """
        from src.contracts import EXCLUSIVE

        def valid(now, expires):
            return now < expires if EXCLUSIVE else now <= expires
    """,
        """
        def valid(now, expires):
            return now <= expires
    """,
        "from src.app import valid\nassert not valid(100, 100)\n",
        "Authentication at the exact exclusive expiry time",
        "An expired token is accepted at the boundary.",
        "Preserve the exclusive expiry contract.",
        dependencies={"src/contracts.py": "EXCLUSIVE = True\n"},
    ),
    case(
        "security",
        "bound-value-clean",
        """
        def find(db, name):
            return db.execute('SELECT name FROM users WHERE name = ?', (name,)).fetchall()
    """,
        """
        def find(db, name):
            query = 'SELECT name FROM users WHERE name = ?'
            return db.execute(query, (name,)).fetchall()
    """,
        """
        import sqlite3
        from src.app import find
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE users (name TEXT)')
        db.execute("INSERT INTO users VALUES ('alice')")
        assert find(db, "' OR 1=1 --") == []
        assert find(db, 'alice') == [('alice',)]
    """,
        "A SQL-looking user value",
        "The parameter remains bound, never interpolated.",
        "Factoring the query string preserves the placeholder and parameter tuple.",
        clean=True,
    ),
    case(
        "security",
        "deny-unknown-clean",
        """
        def authorized(role):
            return role in {'reader', 'writer'}
    """,
        """
        ROLES = frozenset(('reader', 'writer'))

        def authorized(role):
            return role in ROLES
    """,
        """
        from src.app import authorized
        assert not authorized('owner')
        assert authorized('reader') and authorized('writer')
    """,
        "Unknown and explicitly allowed roles",
        "The fixed allowlist remains fail closed.",
        "Moving the allowlist into a frozenset does not broaden permissions.",
        clean=True,
    ),
    case(
        "security",
        "loopback-only-clean",
        """
        from ipaddress import ip_address

        def local(address):
            return ip_address(address).is_loopback
    """,
        """
        from ipaddress import ip_address

        def local(address):
            parsed = ip_address(address)
            return parsed.is_loopback
    """,
        "from src.app import local\nassert local('127.0.0.1') and local('::1')\n"
        "assert not local('10.0.0.1') and not local('203.0.113.5')\n",
        "IPv4/IPv6 loopback and nonloopback addresses",
        "The loopback-only boundary remains unchanged.",
        "Both implementations use the same parsed-address loopback predicate.",
        clean=True,
    ),
    case(
        "tests",
        "balance-assertion",
        """
        def check_balance(actual):
            assert actual == 42
    """,
        """
        def check_balance(actual):
            assert actual
    """,
        """
        from src.app import check_balance
        check_balance(42)
        rejected = False
        try:
            check_balance(41)
        except AssertionError:
            rejected = True
        assert rejected
    """,
        "A nonzero but incorrect balance 41",
        "The test accepts a wrong value.",
        "Assert the required value, not mere truthiness.",
    ),
    case(
        "tests",
        "response-key",
        """
        from src.contracts import REQUIRED

        def check_payload(payload):
            assert REQUIRED <= payload.keys()
    """,
        """
        def check_payload(payload):
            assert isinstance(payload, dict)
    """,
        """
        from src.app import check_payload
        check_payload({'result': 1, 'request_id': 'r'})
        rejected = False
        try:
            check_payload({'result': 1})
        except AssertionError:
            rejected = True
        assert rejected
    """,
        "A response missing request_id but still shaped as a dict",
        "The test no longer detects a required contract field.",
        "Keep required-key assertions.",
        dependencies={"src/contracts.py": "REQUIRED = {'result', 'request_id'}\n"},
    ),
    case(
        "tests",
        "fixture-ownership",
        """
        from src.contracts import TEMPLATE

        def fixture():
            return TEMPLATE.copy()
    """,
        """
        from src.contracts import TEMPLATE

        def fixture():
            return TEMPLATE
    """,
        """
        from src.app import fixture
        first = fixture()
        first['enabled'] = False
        assert fixture()['enabled'] is True
    """,
        "One test mutates its fixture before a subsequent test reads a fixture",
        "Tests share mutable fixture state and become order dependent.",
        "Return a fresh dict for each fixture request.",
        dependencies={"src/contracts.py": "TEMPLATE = {'enabled': True}\n"},
    ),
    case(
        "tests",
        "exception-subclass",
        """
        def check_error(error):
            assert isinstance(error, ValueError)
    """,
        """
        def check_error(error):
            assert isinstance(error, Exception)
    """,
        """
        from src.app import check_error
        check_error(ValueError('invalid'))
        rejected = False
        try:
            check_error(RuntimeError('unrelated'))
        except AssertionError:
            rejected = True
        assert rejected
    """,
        "An unrelated RuntimeError instead of ValueError",
        "The weakened test accepts the wrong failure mode.",
        "Retain the required exception type.",
    ),
    case(
        "tests",
        "negative-coverage",
        """
        def inputs():
            return [-1, 0, 1]
    """,
        """
        def inputs():
            return [0, 1]
    """,
        "from src.app import inputs\nassert any(value < 0 for value in inputs())\n",
        "A boundary test suite whose contract includes negative input",
        "The negative branch loses all coverage.",
        "Retain at least one negative test input.",
    ),
    case(
        "tests",
        "precise-empty-clean",
        """
        def check_empty(values):
            assert values == []
    """,
        """
        def check_empty(values):
            assert len(values) == 0
    """,
        """
        from src.app import check_empty
        check_empty([])
        rejected = False
        try:
            check_empty([0])
        except AssertionError:
            rejected = True
        assert rejected
    """,
        "Empty and single-item list fixtures",
        "Both assertions reject nonempty list fixtures.",
        "The fixture contract supplies lists; the length assertion preserves this invariant.",
        clean=True,
    ),
    case(
        "tests",
        "fresh-list-clean",
        """
        def fixture():
            return list((1, 2))
    """,
        """
        def fixture():
            return [1, 2]
    """,
        "from src.app import fixture\na = fixture()\na.append(3)\nassert fixture() == [1, 2]\n",
        "A caller mutates its returned fixture list",
        "Fresh list ownership remains intact.",
        "Both expressions allocate a separate list per call.",
        clean=True,
    ),
    case(
        "tests",
        "subtype-guard-clean",
        """
        def check_error(error):
            assert isinstance(error, ValueError)
    """,
        """
        def check_error(error):
            accepted = isinstance(error, ValueError)
            assert accepted
    """,
        """
        from src.app import check_error
        check_error(ValueError('bad'))
        rejected = False
        try:
            check_error(TypeError('wrong'))
        except AssertionError:
            rejected = True
        assert rejected
    """,
        "Expected and unexpected exception types",
        "The extracted boolean preserves exact policy.",
        "The same isinstance predicate still drives the assertion.",
        clean=True,
    ),
    case(
        "operations",
        "migration-drop-order",
        """
        from src.contracts import COPY

        def migrate(db):
            db.execute(COPY)
            db.execute('DROP TABLE legacy')
    """,
        """
        from src.contracts import COPY

        def migrate(db):
            db.execute('DROP TABLE legacy')
            db.execute(COPY)
    """,
        """
        import sqlite3
        from src.migrations.upgrade import migrate
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE legacy (value INTEGER)')
        db.execute('INSERT INTO legacy VALUES (17)')
        db.execute('CREATE TABLE current (value INTEGER)')
        migrate(db)
        assert db.execute('SELECT value FROM current').fetchall() == [(17,)]
    """,
        "A populated legacy table during upgrade",
        "Dropping before copy destroys the source data.",
        "Copy data before dropping the legacy table.",
        path="src/migrations/upgrade.py",
        dependencies={
            "src/contracts.py": "COPY = 'INSERT INTO current SELECT value FROM legacy'\n"
        },
    ),
    case(
        "operations",
        "queue-drain",
        """
        def drain(queue):
            batch = list(queue)
            queue.clear()
            return batch
    """,
        """
        def drain(queue):
            queue.clear()
            return list(queue)
    """,
        """
        from src.concurrency.worker import drain
        from src.contracts import EVENTS
        queue = list(EVENTS)
        assert drain(queue) == ['one', 'two']
        assert queue == []
    """,
        "A worker drains a nonempty pending queue",
        "Every pending item is silently discarded.",
        "Snapshot the batch before clearing the queue.",
        path="src/concurrency/worker.py",
        dependencies={"src/contracts.py": "EVENTS = ('one', 'two')\n"},
    ),
    case(
        "operations",
        "permit-underflow",
        """
        def acquire(permits):
            if permits <= 0:
                return permits, False
            return permits - 1, True
    """,
        """
        def acquire(permits):
            return permits - 1, True
    """,
        "from src.concurrency.semaphore import acquire\nassert acquire(0) == (0, False)\n",
        "A deterministic zero-permit semaphore transition",
        "The state machine grants work without capacity and becomes negative.",
        "Reject acquire at zero permits.",
        path="src/concurrency/semaphore.py",
    ),
    case(
        "operations",
        "retry-deadline",
        """
        def expired(now, deadline):
            return now >= deadline
    """,
        """
        def expired(now, deadline):
            return now >= deadline * 1000
    """,
        """
        from src.app import expired
        from src.contracts import DEADLINE
        assert expired(11, DEADLINE)
    """,
        "Monotonic seconds exceed the existing ten-second deadline",
        "Retries continue approximately a thousand times longer than allowed.",
        "Compare values in the same seconds unit.",
        dependencies={"src/contracts.py": "DEADLINE = 10\n"},
    ),
    case(
        "operations",
        "config-milliseconds",
        """
        def timeout_seconds(milliseconds):
            return milliseconds / 1000
    """,
        """
        def timeout_seconds(milliseconds):
            return milliseconds
    """,
        "from src.app import timeout_seconds\nassert timeout_seconds(250) == 0.25\n",
        "A 250ms configured request timeout",
        "A quarter-second timeout becomes 250 seconds.",
        "Convert milliseconds into seconds at the API boundary.",
    ),
    case(
        "operations",
        "transaction-clean",
        """
        def store(db, value):
            with db:
                db.execute('INSERT INTO events VALUES (?)', (value,))
    """,
        """
        def store(db, value):
            query = 'INSERT INTO events VALUES (?)'
            with db:
                db.execute(query, (value,))
    """,
        """
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
    """,
        "Successful insert followed by a uniqueness failure",
        "Transaction completion and rollback stay correct.",
        "The same database context manager still encloses execution.",
        path="src/migrations/store.py",
        clean=True,
    ),
    case(
        "operations",
        "queue-copy-clean",
        """
        def drain(queue):
            batch = queue.copy()
            queue.clear()
            return batch
    """,
        """
        def drain(queue):
            batch = list(queue)
            queue.clear()
            return batch
    """,
        """
        from src.concurrency.worker import drain
        queue = [1, 2]
        assert drain(queue) == [1, 2]
        assert queue == []
    """,
        "A list-backed worker queue",
        "Copy-before-clear ordering is preserved.",
        "Both expressions snapshot list contents before clearing.",
        path="src/concurrency/worker.py",
        clean=True,
    ),
    case(
        "operations",
        "deadline-clean",
        """
        def expired(now, deadline):
            return not now < deadline
    """,
        """
        def expired(now, deadline):
            return now >= deadline
    """,
        """
        from src.app import expired
        assert [expired(n, 10) for n in (9, 10, 11)] == [False, True, True]
    """,
        "Finite monotonic times before, at and after deadline",
        "The exclusive deadline remains unchanged for these finite values.",
        "The trusted input contract supplies finite numeric monotonic times.",
        clean=True,
    ),
    case(
        "consumer",
        "optional-description",
        """
        def description(record):
            return record.get('description') or ''
    """,
        """
        def description(record):
            return record.get('description')
    """,
        """
        from src.app import description
        from src.contracts import render
        assert render(description({})) == ''
    """,
        "An unchanged string consumer renders a record with no description",
        "None crosses the string-only boundary and raises AttributeError.",
        "Retain the empty-string default or migrate the consumer.",
        dependencies={"src/contracts.py": "def render(text):\n    return text.strip()\n"},
    ),
    case(
        "consumer",
        "unknown-enum",
        """
        def mode(name):
            if name not in {'fast', 'safe'}:
                raise ValueError('unknown mode')
            return name
    """,
        """
        def mode(name):
            return name if name in {'fast', 'safe'} else 'fast'
    """,
        """
        from src.app import mode
        rejected = False
        try:
            mode('sfae')
        except ValueError:
            rejected = True
        assert rejected
    """,
        "A misspelled explicit safety mode",
        "An invalid request silently selects fast mode.",
        "Keep the explicit invalid-enum error.",
    ),
    case(
        "consumer",
        "unicode-preview",
        """
        def preview(text, count):
            return text[:count]
    """,
        """
        def preview(text, count):
            return text.encode('utf-8')[:count].decode('utf-8')
    """,
        "from src.app import preview\nassert preview('éclair', 1) == 'é'\n",
        "A one-character preview of a multibyte character",
        "A valid string raises UnicodeDecodeError.",
        "Slice Unicode code points rather than an incomplete encoded byte prefix.",
    ),
    case(
        "consumer",
        "keyword-contract",
        """
        def fetch(*, limit):
            return list(range(limit))
    """,
        """
        def fetch(*, size):
            return list(range(size))
    """,
        "from src.contracts import page\nassert page() == [0, 1]\n",
        "An unchanged caller passes the published limit keyword",
        "The keyword rename raises TypeError for the existing consumer.",
        "Preserve limit or update callers in the same change.",
        dependencies={
            "src/contracts.py": "from src.app import fetch\n\ndef page():\n"
            "    return fetch(limit=2)\n"
        },
    ),
    case(
        "consumer",
        "iterator-peek",
        """
        def materialize(values):
            return list(values)
    """,
        """
        def materialize(values):
            next(values, None)
            return list(values)
    """,
        """
        from src.app import materialize
        from src.contracts import stream
        assert materialize(stream()) == [0, 1, 2]
    """,
        "An unchanged caller supplies a one-pass iterator",
        "The first item disappears from the returned list.",
        "Do not consume an unreturned preview.",
        dependencies={"src/contracts.py": "def stream():\n    return iter((0, 1, 2))\n"},
    ),
    case(
        "consumer",
        "keyword-alias-clean",
        """
        def fetch(*, limit):
            return list(range(limit))
    """,
        """
        def fetch(*, limit=None, size=None):
            count = limit if limit is not None else size
            return list(range(count))
    """,
        "from src.app import fetch\nassert fetch(limit=2) == [0, 1]\nassert fetch(limit=0) == []\n",
        "Existing limit callers including explicit zero",
        "The compatibility keyword is preserved.",
        "The new alias does not remove limit or treat zero as missing.",
        clean=True,
    ),
    case(
        "consumer",
        "unicode-clean",
        """
        def preview(text, count):
            return text[:count]
    """,
        """
        def preview(text, count):
            end = count
            return text[:end]
    """,
        "from src.app import preview\nassert preview('éclair', 1) == 'é'\n"
        "assert preview('', 2) == ''\n",
        "Multibyte and empty strings",
        "The refactor still slices code points.",
        "No encoding or byte-level truncation was introduced.",
        clean=True,
    ),
    case(
        "consumer",
        "immutable-result-clean",
        """
        def values():
            return tuple([1, 2])
    """,
        """
        def values():
            return (1, 2)
    """,
        "from src.app import values\nassert values() == (1, 2)\n"
        "assert isinstance(values(), tuple)\n",
        "A caller consumes an immutable ordered sequence",
        "Ordering and tuple type remain the same.",
        "The tuple literal preserves the public result.",
        clean=True,
    ),
]

# Opaque public IDs avoid encoding defect names or categories into runtime paths.
# Descriptive names remain solely in this evaluator-only catalog.
for number, specification in enumerate(CASES, 1):
    specification["case_name"] = specification["id"]
    specification["id"] = f"sealed-h{number:02d}"
