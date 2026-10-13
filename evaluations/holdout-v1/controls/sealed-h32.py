from src.app import expired

assert [expired(n, 10) for n in (9, 10, 11)] == [False, True, True]
