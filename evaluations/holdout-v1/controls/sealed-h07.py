from src.app import ordered

rows = [(1, 'first'), (0, 'zero'), (1, 'second')]
assert ordered(rows) == [(0, 'zero'), (1, 'first'), (1, 'second')]
assert rows[0] == (1, 'first')
