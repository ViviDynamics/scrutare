from src.app import check_balance

check_balance(42)
rejected = False
try:
    check_balance(41)
except AssertionError:
    rejected = True
assert rejected
