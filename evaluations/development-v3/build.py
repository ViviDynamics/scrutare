"""Rebuild the original development-v3 fixtures from this explicit catalog.

Run `python evaluations/development-v3/build.py` from the repository root. The
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
NONE = {'class': 'none', 'expected_specialists': []}
CONCURRENCY = {'class': 'covered', 'expected_specialists': ['performance-concurrency']}
MIGRATIONS = {'class': 'covered', 'expected_specialists': ['data-integrity-migrations']}

FORMATTERS = '''\
"""Text formatters for the monthly statement report."""

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}
LINE_WIDTH = 48
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_amount(cents):
    """Whole cents as a plain decimal amount, for the single-currency statement."""
    sign = "-" if cents < 0 else ""
    units, remainder = divmod(abs(cents), 100)
    return f"{sign}{units}.{remainder:02d}"


def format_percent(ratio):
    return f"{ratio * 100:.1f}%"


def format_date(year, month, day):
    return f"{day:02d} {MONTHS[month - 1]} {year}"


def format_period(start, end):
    return f"{format_date(*start)} to {format_date(*end)}"


def pad_left(text, width):
    return text if len(text) >= width else " " * (width - len(text)) + text


def pad_right(text, width):
    return text if len(text) >= width else text + " " * (width - len(text))


def truncate(text, width):
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."


def mask_account(number):
    digits = "".join(ch for ch in str(number) if ch.isdigit())
    return "*" * max(0, len(digits) - 4) + digits[-4:]


def format_quantity(count, unit):
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def format_rate(cents, unit):
    return f"{format_amount(cents)} per {unit}"


def wrap_text(text, width=LINE_WIDTH):
    lines, current = [], ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_header(title, period):
    rule = "=" * LINE_WIDTH
    return [rule, title.upper(), format_period(*period), rule]


def render_invoice_line(description, cents):
    amount = format_amount(cents)
    label = truncate(description, LINE_WIDTH - len(amount) - 1)
    return pad_right(label, LINE_WIDTH - len(amount)) + amount


def render_discount_line(description, ratio, cents):
    label = f"{description} ({format_percent(ratio)})"
    return render_invoice_line(label, -cents)


def render_subtotal(cents):
    return pad_left("Subtotal " + format_amount(cents), LINE_WIDTH)


def render_tax(ratio, cents):
    return pad_left(f"Tax {format_percent(ratio)} " + format_amount(cents), LINE_WIDTH)


def render_total(cents):
    return pad_left("TOTAL " + format_amount(cents), LINE_WIDTH)


def render_footer(account_number, notes):
    lines = ["-" * LINE_WIDTH, "Account " + mask_account(account_number)]
    for note in notes:
        lines.extend(wrap_text(note))
    return lines


def render_statement(title, period, items, tax_ratio, account_number, notes=()):
    subtotal = sum(cents for _, cents in items)
    tax = round(subtotal * tax_ratio)
    lines = render_header(title, period)
    lines.extend(render_invoice_line(description, cents) for description, cents in items)
    lines.append(render_subtotal(subtotal))
    lines.append(render_tax(tax_ratio, tax))
    lines.append(render_total(subtotal + tax))
    lines.extend(render_footer(account_number, notes))
    return "\\n".join(lines)
'''

HTTP_SETTINGS = '''\
"""Connection settings for the partner API client."""

{constant} = 10
MAX_RETRIES = 3
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0
LARGE_UPLOAD_BYTES = 8 * 1024 * 1024
USER_AGENT = "partner-sync/3"


def connect_options(overrides=None):
    """Options for an ordinary request; explicit overrides win."""
    options = {{"timeout": {constant}, "retries": MAX_RETRIES,
               "headers": {{"User-Agent": USER_AGENT}}}}
    if overrides:
        options.update(overrides)
    return options


def retry_delays(retries=MAX_RETRIES):
    """Exponential backoff, capped."""
    return [min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt) for attempt in range(retries)]


def idempotency_headers(key):
    headers = {{"User-Agent": USER_AGENT}}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def poll_options(interval):
    """Long polling waits for one interval plus the ordinary timeout."""
    options = connect_options()
    options["timeout"] = options["timeout"] + interval
    options["retries"] = 0
    return options


def upload_options(size_bytes):
    """Uploads are chunked above the threshold and given six times as long."""
    options = connect_options()
    if size_bytes > LARGE_UPLOAD_BYTES:
        options["chunked"] = True
        options["timeout"] = DEFAULT_TIMEOUT * 6
    else:
        options["chunked"] = False
    return options
'''

RECORDING_LOCK = '''

class RecordingLock:
    """Deterministic stand-in for a lock that records the order of acquisition."""

    def __init__(self, name, log):
        self.name, self.log = name, log

    def __enter__(self):
        self.log.append(self.name)
        return self

    def __exit__(self, *exc):
        return False
'''

HOOK_LOCK = '''

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

PLUGIN_REGISTRY = '''\
"""Plugin registry internals. Extensions use the `api` facade, not this module."""

_REGISTERED = {}


class Plugin:
    def __init__(self, name, version):
        self.name, self.version = name, version


def register(plugin):
    _REGISTERED[plugin.name] = plugin
    return plugin


def registered():
    return dict(_REGISTERED)
'''

PLUGIN_BUILTIN = '''\
"""Built-in plugins, written against the public facade like any extension."""
from api import Plugin, register

CSV_EXPORT = register(Plugin("csv-export", "1.2"))
AUDIT_LOG = register(Plugin("audit-log", "2.0"))
'''

LISTING_VIEWS = '''\
"""List view. The `page` query parameter is 1-based, as shown in the page links."""
from pager import page_slice


def list_view(items, query, size=20):
    page = max(1, int(query.get("page", 1)))
    rows = page_slice(items, page, size)
    last = max(1, -(-len(items) // size))
    return {"rows": rows, "page": page, "last_page": last}
'''

DOWNLOAD_MANIFEST = '''\
"""Release manifest.

The checksums below are public. The same file is served to every client next to
the artifacts and is printed on the downloads page.
"""

PUBLISHED = {
    "tool-1.4.0.tar.gz": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "tool-1.4.1.tar.gz": "60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752",
}


def published_sha256(name):
    return PUBLISHED[name]
'''

REGION_READER = '''\
"""Read an account's sales region. Rows not yet backfilled read as 'unknown'."""


def region_of(connection, account_id):
    row = connection.execute(
        "SELECT COALESCE(region, 'unknown') FROM accounts WHERE id = ?",
        (account_id,)).fetchone()
    return row[0] if row else None
'''

PRICING_PAGE = '''\
"""Pricing page view.

Customers may pick a billing currency in account settings; otherwise the page
uses the usual currency of their locale.
"""

LOCALE_CURRENCY = {"en-GB": "GBP", "en-US": "USD", "de-DE": "EUR", "fr-FR": "EUR"}


def pricing_page(cache, account):
    currency = account.get("billing_currency") or LOCALE_CURRENCY[account["locale"]]
    return cache.get("pricing", account["locale"], currency)
'''

SIGNUP = '''\
"""Account signup."""


def normalise(email):
    """Trim and lowercase an address.

    Applied to new signups since release 2.3. Accounts created earlier keep the
    address exactly as it was typed.
    """
    return email.strip().lower()


def create_account(connection, email):
    connection.execute("INSERT INTO accounts (email) VALUES (?)", (normalise(email),))
    connection.commit()
'''

CASES: list[dict[str, Any]] = [
    {
        'id': 'dev3-01', 'domain': 'correctness', 'routing': NONE,
        'base': {'src/reporting/formatters.py': FORMATTERS},
        'head': {'src/reporting/formatters.py': FORMATTERS + '''

def format_amount(value, currency):
    """Amount with its currency symbol, for the multi-currency export."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    return symbol + f"{value / 100:.2f}"


def render_export_row(description, cents, currency):
    return f"{description},{format_amount(cents, currency)}"
'''},
        'defects': [{
            'id': 'dev3-01-defect',
            'trigger': 'Render any statement line, discount, subtotal, tax, total or rate after '
                       'the module is imported.',
            'impact': 'Every existing single-argument format_amount caller raises TypeError, so '
                      'the monthly statement cannot be rendered.',
            'evidence': 'formatters.py defines format_amount(cents) near the top and the head '
                        'adds a second module-level format_amount(value, currency) at the end, '
                        'which replaces the first for all callers.',
            'match_criteria': 'Identify that the new format_amount redefines or shadows the '
                              'existing one, or that existing callers now fail.',
            'fix_or_counterexample': 'Give the export helper a distinct name.',
        }],
        'counterexamples': ['render_export_row itself returns the intended text.'],
        'tool_expectation': {'ruff_codes': ['F811'], 'relation': 'names_the_defect'},
        'control': '''
import sys

sys.path.insert(0, "src/reporting")
import formatters

line = formatters.render_invoice_line("Widget", 1234)
assert line.endswith("12.34") and len(line) == formatters.LINE_WIDTH, line
assert formatters.render_total(5000).endswith("TOTAL 50.00")
''',
    },
    {
        'id': 'dev3-02', 'domain': 'correctness', 'routing': NONE,
        'base': {'src/client/http_settings.py': HTTP_SETTINGS.format(constant='DEFAULT_TIMEOUT')},
        'head': {'src/client/http_settings.py': HTTP_SETTINGS.format(
            constant='DEFAULT_TIMEOUT_SECONDS')},
        'defects': [{
            'id': 'dev3-02-defect',
            'trigger': 'Call upload_options with a size above LARGE_UPLOAD_BYTES.',
            'impact': 'Large uploads raise NameError instead of receiving a longer timeout.',
            'evidence': 'http_settings.py renames DEFAULT_TIMEOUT to DEFAULT_TIMEOUT_SECONDS at '
                        'its definition and in connect_options, but upload_options still reads '
                        'DEFAULT_TIMEOUT in the large-upload branch.',
            'match_criteria': 'Identify the remaining reference to the old constant name in '
                              'upload_options, or the resulting NameError for large uploads.',
            'fix_or_counterexample': 'Rename the remaining use in upload_options.',
        }],
        'counterexamples': ['Ordinary requests, polling and small uploads still work.'],
        'tool_expectation': {'ruff_codes': ['F821'], 'relation': 'names_the_defect'},
        'control': '''
import sys

sys.path.insert(0, "src/client")
import http_settings

assert http_settings.connect_options()["timeout"] == 10
assert http_settings.upload_options(1024)["chunked"] is False
large = http_settings.upload_options(http_settings.LARGE_UPLOAD_BYTES + 1)
assert large == {**http_settings.connect_options(), "chunked": True, "timeout": 60}, large
''',
    },
    {
        'id': 'dev3-03', 'domain': 'maintainability', 'routing': NONE,
        'base': {
            'src/plugins/api.py': '''\
"""Public plugin facade."""
from registry import Plugin, register


def describe(plugin):
    return f"{plugin.name} ({plugin.version})"
''',
            'src/plugins/registry.py': PLUGIN_REGISTRY,
            'src/plugins/builtin.py': PLUGIN_BUILTIN,
        },
        'head': {
            'src/plugins/api.py': '''\
"""Public plugin facade."""
from registry import Plugin, register


def describe(plugin):
    return f"{plugin.name} ({plugin.version})"


def describe_all(plugins):
    return [describe(plugin) for plugin in sorted(plugins, key=lambda item: item.name)]
''',
            'src/plugins/registry.py': None,
            'src/plugins/builtin.py': None,
        },
        'defects': [],
        'counterexamples': [
            'builtin.py imports Plugin and register from api in both revisions; the facade '
            'imports are its public surface, and the added function is correct.'],
        'tempting_allegations': [{
            'allegation': 'Plugin and register are imported in api.py but never used and '
                          'should be removed.',
            'why_wrong': 'api.py is the facade extensions import from: builtin.py does '
                         '`from api import Plugin, register`, so removing the imports breaks '
                         'every plugin. The imports also exist unchanged in the base.',
            'evidence': 'builtin.py import line; registry.py module docstring; api.py import '
                        'line in both revisions.',
        }],
        'unchanged_supporting_paths': ['src/plugins/registry.py', 'src/plugins/builtin.py'],
        'tool_expectation': {'ruff_codes': ['F401', 'F401'],
                             'relation': 'suggests_the_tempting_allegation'},
        'control': '''
import sys

sys.path.insert(0, "src/plugins")
import api
import builtin
import registry

assert set(registry.registered()) == {"csv-export", "audit-log"}
assert api.register is registry.register and api.Plugin is registry.Plugin
assert api.describe(builtin.CSV_EXPORT) == "csv-export (1.2)"
describe_all = getattr(api, "describe_all", None)
if describe_all is not None:
    assert describe_all(registry.registered().values()) == [
        "audit-log (2.0)", "csv-export (1.2)"]
''',
    },
    {
        'id': 'dev3-04', 'domain': 'correctness', 'routing': NONE,
        'base': {
            'src/listing/pager.py': '''\
"""Slice a result list into pages."""


def page_slice(items, page, size):
    """Return one page of items. Pages are numbered from 1."""
    start = (page - 1) * size
    return items[start:start + size]
''',
            'src/listing/views.py': LISTING_VIEWS,
        },
        'head': {
            'src/listing/pager.py': '''\
"""Slice a result list into pages."""


def page_slice(items, page, size):
    """Return one page of items."""
    offset = page * size
    return items[offset:offset + size]
''',
            'src/listing/views.py': None,
        },
        'defects': [{
            'id': 'dev3-04-defect',
            'trigger': 'Open the first page of any list view.',
            'impact': 'The first page shows the second page of rows; the first rows are never '
                      'shown and the last page is empty.',
            'evidence': 'pager.py now computes the offset as page * size, while views.py still '
                        'passes a 1-based page.',
            'match_criteria': 'Identify the off-by-one page offset or that 1-based callers now '
                              'skip the first page.',
            'fix_or_counterexample': 'Keep (page - 1) * size, or convert the caller.',
        }],
        'counterexamples': ['A caller that passed 0-based pages would be correct.'],
        'unchanged_supporting_paths': ['src/listing/views.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'silent_on_a_real_defect'},
        'control': '''
import sys

sys.path.insert(0, "src/listing")
from views import list_view

items = list(range(1, 8))
assert list_view(items, {"page": "1"}, size=3)["rows"] == [1, 2, 3]
assert list_view(items, {}, size=3)["rows"] == [1, 2, 3]
assert list_view(items, {"page": "3"}, size=3)["rows"] == [7]
''',
    },
    {
        'id': 'dev3-05', 'domain': 'security', 'routing': NONE,
        'base': {
            'src/downloads/verify.py': '''\
"""Verify a downloaded artifact against the release manifest."""
import hashlib
import hmac

from manifest import published_sha256


def verify(name, payload):
    expected = published_sha256(name)
    actual = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(actual, expected)
''',
            'src/downloads/manifest.py': DOWNLOAD_MANIFEST,
        },
        'head': {
            'src/downloads/verify.py': '''\
"""Verify a downloaded artifact against the release manifest."""
import hashlib

from manifest import published_sha256


def verify(name, payload):
    expected = published_sha256(name)
    actual = hashlib.sha256(payload).hexdigest()
    return actual == expected
''',
            'src/downloads/manifest.py': None,
        },
        'defects': [],
        'counterexamples': [
            'Both compared digests are public: the expected one is published to every client '
            'and the actual one is computed from bytes the caller already holds.'],
        'tempting_allegations': [{
            'allegation': 'Replacing hmac.compare_digest with == opens a timing side channel '
                          'on the digest comparison.',
            'why_wrong': 'A timing channel matters only when one side is secret. The expected '
                         'checksum is published in the release manifest and the actual digest '
                         'is derived from the payload being checked, so there is nothing to '
                         'leak; the result is identical for every input.',
            'evidence': 'manifest.py module docstring and PUBLISHED table; verify.py computes '
                        'a plain SHA-256 of the payload with no key.',
        }],
        'unchanged_supporting_paths': ['src/downloads/manifest.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/downloads")
from verify import verify

assert verify("tool-1.4.0.tar.gz", b"test") is True
assert verify("tool-1.4.1.tar.gz", b"test") is False
assert verify("tool-1.4.1.tar.gz", b"test2") is True
''',
    },
    {
        'id': 'dev3-06', 'domain': 'performance-concurrency', 'routing': CONCURRENCY,
        'base': {'src/concurrency/route_table.py': '''\
"""Route table: read on every request, changed rarely.

Writers build a new tuple and swap it in under the lock. A published tuple is
never mutated.
"""
import threading


class RouteTable:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._routes = ()

    def add(self, prefix, handler):
        with self._lock:
            self._routes = (*self._routes, (prefix, handler))

    def remove(self, prefix):
        with self._lock:
            self._routes = tuple(route for route in self._routes if route[0] != prefix)

    def snapshot(self):
        with self._lock:
            return self._routes

    def match(self, path):
        for prefix, handler in self.snapshot():
            if path.startswith(prefix):
                return handler
        return None
'''},
        'head': {'src/concurrency/route_table.py': '''\
"""Route table: read on every request, changed rarely.

Writers build a new tuple and swap it in under the lock. A published tuple is
never mutated.
"""
import threading


class RouteTable:
    def __init__(self, lock=None):
        self._lock = lock if lock is not None else threading.Lock()
        self._routes = ()

    def add(self, prefix, handler):
        with self._lock:
            self._routes = (*self._routes, (prefix, handler))

    def remove(self, prefix):
        with self._lock:
            self._routes = tuple(route for route in self._routes if route[0] != prefix)

    def snapshot(self):
        return self._routes

    def match(self, path):
        for prefix, handler in self.snapshot():
            if path.startswith(prefix):
                return handler
        return None
'''},
        'defects': [],
        'counterexamples': [
            'A reader that runs while a writer holds the lock receives the previous complete '
            'tuple, exactly as a reader that had taken the lock a moment earlier would.'],
        'tempting_allegations': [{
            'allegation': 'snapshot now reads shared state without the lock, so a request can '
                          'observe a partially updated route table or race with add/remove.',
            'why_wrong': 'snapshot performs one attribute read that yields a complete immutable '
                         'tuple. Writers never mutate a published tuple; they bind a new one. '
                         'The base released the lock before its caller iterated, so it gave '
                         'the same old-or-new guarantee and no more.',
            'evidence': 'route_table.py module docstring; add and remove rebind self._routes '
                        'to a new tuple under the lock in both revisions.',
        }],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/concurrency")
from route_table import RouteTable
''' + HOOK_LOCK + '''

lock = HookLock()
table = RouteTable(lock)
table.add("/a", "A")
seen = []
lock.hook = lambda: seen.append((table.snapshot(), table.match("/a/x"), table.match("/b/x")))
table.add("/b", "B")
assert seen == [((("/a", "A"),), "A", None)], seen
assert table.snapshot() == (("/a", "A"), ("/b", "B"))
lock.hook = lambda: seen.append(table.snapshot())
table.remove("/a")
assert seen[1] == (("/a", "A"), ("/b", "B")) and table.match("/a/x") is None
''',
    },
    {
        'id': 'dev3-07', 'domain': 'data-integrity', 'routing': MIGRATIONS,
        'base': {
            'src/migrations/0009_backfill_region.py': '''\
"""Backfill accounts.region for rows created before the column existed."""


def upgrade(connection):
    connection.execute(
        "UPDATE accounts SET region = COALESCE((SELECT region FROM country_regions"
        " WHERE country = accounts.country), 'unknown') WHERE region IS NULL")
    connection.commit()
''',
            'src/region_reader.py': REGION_READER,
        },
        'head': {
            'src/migrations/0009_backfill_region.py': '''\
"""Backfill accounts.region for rows created before the column existed."""

BATCH = 500


def upgrade(connection, batch=BATCH):
    """Backfill in small committed batches so the table is never locked for long."""
    while True:
        cursor = connection.execute(
            "UPDATE accounts SET region = COALESCE((SELECT region FROM country_regions"
            " WHERE country = accounts.country), 'unknown') WHERE id IN ("
            "SELECT id FROM accounts WHERE region IS NULL ORDER BY id LIMIT ?)", (batch,))
        connection.commit()
        if cursor.rowcount == 0:
            break
''',
            'src/region_reader.py': None,
        },
        'defects': [],
        'counterexamples': [
            'Interrupting the backfill after any batch and running it again fills the '
            'remaining rows; readers treat a row not yet backfilled as unknown throughout.'],
        'tempting_allegations': [{
            'allegation': 'Committing each batch makes the migration non-atomic: a failure '
                          'part-way leaves the table partially backfilled or inconsistent.',
            'why_wrong': 'A partially backfilled table is the state every reader already '
                         'handles: region_reader.py coalesces NULL to unknown, which is how '
                         'all rows read before the migration. Each pass only touches rows '
                         'still NULL, every assigned value is final, and the loop ends because '
                         'COALESCE always writes a non-NULL value, so a rerun completes it.',
            'evidence': 'region_reader.py COALESCE; the WHERE region IS NULL selection and the '
                        "COALESCE(..., 'unknown') assignment in the migration.",
        }],
        'defensible_observations': [
            'A reviewer may note that batching trades one transaction for many and ask for a '
            'progress log, or that the statement relies on rowcount. These are reasonable '
            'remarks, not the labeled allegation, and need a human decision.'],
        'unchanged_supporting_paths': ['src/region_reader.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
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
''',
    },
    {
        'id': 'dev3-08', 'domain': 'performance-concurrency', 'routing': CONCURRENCY,
        'base': {
            'src/render/page_cache.py': '''\
"""Rendered page cache."""


class PageCache:
    def __init__(self, render):
        self._render = render
        self._pages = {}

    def get(self, template_id, locale, currency):
        key = (template_id, locale, currency)
        if key not in self._pages:
            self._pages[key] = self._render(template_id, locale, currency)
        return self._pages[key]

    def clear(self):
        self._pages.clear()
''',
            'src/render/pricing_page.py': PRICING_PAGE,
        },
        'head': {
            'src/render/page_cache.py': '''\
"""Rendered page cache."""


class PageCache:
    def __init__(self, render):
        self._render = render
        self._pages = {}

    def get(self, template_id, locale, currency):
        # A locale has one display currency, so the locale already identifies the
        # page. Leaving currency out of the key stops duplicate entries.
        key = (template_id, locale)
        if key not in self._pages:
            self._pages[key] = self._render(template_id, locale, currency)
        return self._pages[key]

    def clear(self):
        self._pages.clear()
''',
            'src/render/pricing_page.py': None,
        },
        'defects': [{
            'id': 'dev3-08-defect',
            'trigger': 'Two customers with the same locale but different billing currencies '
                       'open the pricing page.',
            'impact': 'The second customer is served the page rendered in the first '
                      "customer's currency.",
            'evidence': 'page_cache.py drops currency from the cache key on the strength of a '
                        'comment, while pricing_page.py passes a billing currency chosen '
                        'independently of the locale.',
            'match_criteria': 'Identify that currency still varies for one locale, so the '
                              'narrower key returns a page rendered for another currency.',
            'fix_or_counterexample': 'Keep currency in the key.',
        }],
        'counterexamples': [
            'Customers who never choose a billing currency see the correct page.'],
        'unchanged_supporting_paths': ['src/render/pricing_page.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/render")
from page_cache import PageCache
from pricing_page import pricing_page

cache = PageCache(lambda template, locale, currency: f"{template}:{locale}:{currency}")
assert pricing_page(cache, {"locale": "en-GB"}) == "pricing:en-GB:GBP"
euro = pricing_page(cache, {"locale": "en-GB", "billing_currency": "EUR"})
assert euro == "pricing:en-GB:EUR", euro
''',
    },
    {
        'id': 'dev3-09', 'domain': 'performance-concurrency', 'routing': CONCURRENCY,
        'base': {'src/concurrency/account_locks.py': '''\
"""Move a balance between two accounts while holding both account locks."""


class InsufficientFunds(Exception):
    pass


def move(source, target, amount):
    first, second = sorted((source, target), key=lambda account: account.number)
    with first.lock:
        with second.lock:
            source.balance -= amount
            target.balance += amount
'''},
        'head': {'src/concurrency/account_locks.py': '''\
"""Move a balance between two accounts while holding both account locks."""


class InsufficientFunds(Exception):
    pass


def move(source, target, amount):
    with source.lock:
        if source.balance < amount:
            raise InsufficientFunds(source.number)
        with target.lock:
            source.balance -= amount
            target.balance += amount
'''},
        'defects': [{
            'id': 'dev3-09-defect',
            'trigger': 'One thread moves from account A to B while another moves from B to A.',
            'impact': 'Each thread holds one lock and waits for the other; both transfers '
                      'deadlock.',
            'evidence': 'account_locks.py no longer acquires the two locks in account-number '
                        'order; it takes the source lock first and the target lock second.',
            'match_criteria': 'Identify the lost consistent lock ordering or the resulting '
                              'deadlock between opposite transfers.',
            'fix_or_counterexample': 'Keep the sorted acquisition order and check the balance '
                                     'once both locks are held.',
        }],
        'counterexamples': ['Transfers that never run in opposite directions complete.'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/concurrency")
from account_locks import move
''' + RECORDING_LOCK + '''

class Account:
    def __init__(self, number, balance, log):
        self.number, self.balance, self.lock = number, balance, RecordingLock(number, log)


log = []
one, two = Account(1, 100, log), Account(2, 100, log)
move(one, two, 10)
move(two, one, 30)
assert (one.balance, two.balance) == (120, 80)
assert log[0] == log[2] and log[1] == log[3], log
''',
    },
    {
        'id': 'dev3-10', 'domain': 'data-integrity', 'routing': MIGRATIONS,
        'base': {
            'src/migrations/0011_unique_email.py': '''\
"""Enforce one account per e-mail address."""


def upgrade(connection):
    # Addresses stored before signup normalised them can differ only by case.
    # Keep the oldest account and park the others on a placeholder for support to merge.
    connection.execute(
        "UPDATE accounts SET email = 'duplicate-' || id || '@invalid' WHERE id NOT IN ("
        "SELECT MIN(id) FROM accounts GROUP BY lower(email))")
    connection.execute("CREATE UNIQUE INDEX accounts_email_unique ON accounts (lower(email))")
    connection.commit()
''',
            'src/signup.py': SIGNUP,
        },
        'head': {
            'src/migrations/0011_unique_email.py': '''\
"""Enforce one account per e-mail address."""


def upgrade(connection):
    # signup.normalise lowercases every address, so duplicates cannot exist.
    connection.execute("CREATE UNIQUE INDEX accounts_email_unique ON accounts (lower(email))")
    connection.commit()
''',
            'src/signup.py': None,
        },
        'defects': [{
            'id': 'dev3-10-defect',
            'trigger': 'Run the migration on a database that holds two accounts created '
                       'before release 2.3 whose addresses differ only by case.',
            'impact': 'Creating the unique index fails and the migration cannot be applied.',
            'evidence': '0011_unique_email.py drops the step that resolves case-variant '
                        'duplicates, citing signup.normalise, while signup.py states that '
                        'normalisation applies only to signups since release 2.3.',
            'match_criteria': 'Identify that pre-existing case-variant duplicates still exist '
                              'and make the unique index creation fail.',
            'fix_or_counterexample': 'Keep the duplicate-resolution step before the index.',
        }],
        'counterexamples': ['A database created entirely after release 2.3 migrates.'],
        'unchanged_supporting_paths': ['src/signup.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
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
''',
    },
    {
        'id': 'dev3-11', 'domain': 'correctness', 'routing': NONE,
        'base': {
            'src/inventory/stock.py': '''\
"""Stock reservations."""


class OutOfStock(Exception):
    pass


def reserve(stock, sku, quantity):
    """Reserve `quantity` units, or raise OutOfStock when fewer remain."""
    available = stock.get(sku, 0)
    if available < quantity:
        raise OutOfStock(sku)
    stock[sku] = available - quantity
    return quantity
''',
            'src/inventory/checkout.py': '''\
"""Checkout. An order is confirmed only for units that were reserved."""
from stock import reserve


def confirm(stock, order):
    confirmed = {}
    for sku, quantity in order.items():
        reserve(stock, sku, quantity)
        confirmed[sku] = quantity
    return confirmed
''',
        },
        'head': {
            'src/inventory/stock.py': '''\
"""Stock reservations."""


class OutOfStock(Exception):
    pass


def reserve(stock, sku, quantity):
    """Reserve up to `quantity` units and return how many were reserved."""
    available = stock.get(sku, 0)
    granted = min(available, quantity)
    stock[sku] = available - granted
    return granted
''',
            'src/inventory/checkout.py': None,
        },
        'defects': [{
            'id': 'dev3-11-defect',
            'trigger': 'Check out an order for more units than remain in stock.',
            'impact': 'The order is confirmed for the full quantity although fewer units were '
                      'reserved, so stock is oversold.',
            'evidence': 'stock.py reserve no longer raises OutOfStock and returns a possibly '
                        'smaller count; checkout.py ignores the return value and relied on '
                        'the exception. checkout.py is deliberately not captured.',
            'match_criteria': 'Identify that reserve no longer raises when stock is short, so '
                              'a caller that relied on OutOfStock or ignores the returned '
                              'count proceeds as if the full quantity were reserved.',
            'fix_or_counterexample': 'Keep raising, or make every caller use the returned '
                                     'count.',
        }],
        'counterexamples': ['A caller that reads the returned count is unaffected.'],
        'evidence_outside_capture': ['src/inventory/checkout.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/inventory")
from checkout import confirm
from stock import OutOfStock

stock = {"bolt": 2}
try:
    confirmed = confirm(stock, {"bolt": 5})
except OutOfStock:
    confirmed = {}
assert confirmed.get("bolt", 0) <= 2, confirmed
assert confirm({"bolt": 5}, {"bolt": 5}) == {"bolt": 5}
''',
    },
    {
        'id': 'dev3-12', 'domain': 'correctness', 'routing': NONE,
        'base': {
            'src/mailer/subject.py': '''\
"""Subject line rendering."""


def render_subject(template, values):
    """Fill a subject template; a missing value raises KeyError."""
    return template.format_map(values)
''',
            'src/mailer/send.py': '''\
"""Send a templated message. Every placeholder is resolved before rendering."""
from subject import render_subject

REQUIRED = frozenset({"order_id"})
OPTIONAL_DEFAULTS = {"customer": "customer", "store": "our store"}


class MissingValues(Exception):
    pass


def compose(template, values):
    missing = REQUIRED - values.keys()
    if missing:
        raise MissingValues(sorted(missing))
    unknown = values.keys() - REQUIRED - OPTIONAL_DEFAULTS.keys()
    if unknown:
        raise MissingValues(sorted(unknown))
    return render_subject(template, {**OPTIONAL_DEFAULTS, **values})
''',
        },
        'head': {
            'src/mailer/subject.py': '''\
"""Subject line rendering."""


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_subject(template, values):
    """Fill a subject template; a missing value renders as empty text."""
    return template.format_map(_Blank(values))
''',
            'src/mailer/send.py': None,
        },
        'defects': [],
        'counterexamples': [
            'The only caller rejects a missing required value itself and supplies every '
            'optional one before rendering, so no placeholder is ever missing at this call.'],
        'tempting_allegations': [{
            'allegation': 'Callers that relied on KeyError to detect a missing template value '
                          'will now send mail with a blank in the subject.',
            'why_wrong': 'send.py, the only caller, never relied on KeyError: it raises '
                         'MissingValues for a missing required value and merges defaults for '
                         'every optional one before calling render_subject. send.py is '
                         'deliberately not captured, so a reviewer cannot see this.',
            'evidence': 'send.py compose in both revisions.',
        }],
        'evidence_outside_capture': ['src/mailer/send.py'],
        'tool_expectation': {'ruff_codes': [], 'relation': 'not_a_tools_case'},
        'control': '''
import sys

sys.path.insert(0, "src/mailer")
from send import MissingValues, compose

assert compose("Order {order_id} from {store}", {"order_id": 7}) == "Order 7 from our store"
assert compose("Hello {customer}", {"order_id": 7, "customer": "Ada"}) == "Hello Ada"
for values in ({}, {"order_id": 7, "coupon": "X"}):
    try:
        compose("Order {order_id}", values)
    except MissingValues:
        continue
    raise AssertionError("invalid values must be rejected before rendering")
''',
    },
]


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(['git', *arguments], cwd=directory, check=True, capture_output=True,
                          text=True, env={'PATH': os.environ['PATH'], **GIT_ENV}).stdout


def commit_pair(base: Path, head: Path) -> tuple[str, str, str]:
    """Real deterministic local commits and the exact patch Git derives from them."""
    with tempfile.TemporaryDirectory(prefix='scrutare-dev3-') as temporary:
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
            'evidence_outside_capture': case.get('evidence_outside_capture', []),
            'tool_expectation': case['tool_expectation'],
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
