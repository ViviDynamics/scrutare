"""Slice a result list into pages."""


def page_slice(items, page, size):
    """Return one page of items."""
    offset = page * size
    return items[offset:offset + size]
