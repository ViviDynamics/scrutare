from src.app import partition

assert [partition(n, 10) for n in (0, 11, -1)] == [(0, 0), (1, 1), (-1, 9)]
