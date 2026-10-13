"""Slice a result list into pages."""


def page_slice(items, page, size):
    """Return one page of items. Pages are numbered from 1."""
    start = (page - 1) * size
    return items[start:start + size]
