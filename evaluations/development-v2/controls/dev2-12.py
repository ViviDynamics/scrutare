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
