import sys

sys.path.insert(0, "src/listing")
from views import list_view

items = list(range(1, 8))
assert list_view(items, {"page": "1"}, size=3)["rows"] == [1, 2, 3]
assert list_view(items, {}, size=3)["rows"] == [1, 2, 3]
assert list_view(items, {"page": "3"}, size=3)["rows"] == [7]
