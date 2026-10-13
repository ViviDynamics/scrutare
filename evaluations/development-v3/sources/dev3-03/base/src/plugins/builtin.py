"""Built-in plugins, written against the public facade like any extension."""
from api import Plugin, register

CSV_EXPORT = register(Plugin("csv-export", "1.2"))
AUDIT_LOG = register(Plugin("audit-log", "2.0"))
