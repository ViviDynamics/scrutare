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
