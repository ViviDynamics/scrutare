from src.app import fetch

assert fetch(limit=2) == [0, 1]
assert fetch(limit=0) == []
