"""List view. The `page` query parameter is 1-based, as shown in the page links."""
from pager import page_slice


def list_view(items, query, size=20):
    page = max(1, int(query.get("page", 1)))
    rows = page_slice(items, page, size)
    last = max(1, -(-len(items) // size))
    return {"rows": rows, "page": page, "last_page": last}
