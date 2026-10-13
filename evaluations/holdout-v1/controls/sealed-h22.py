from src.app import check_empty

check_empty([])
rejected = False
try:
    check_empty([0])
except AssertionError:
    rejected = True
assert rejected
