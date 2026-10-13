from src.app import check_error

check_error(ValueError('invalid'))
rejected = False
try:
    check_error(RuntimeError('unrelated'))
except AssertionError:
    rejected = True
assert rejected
