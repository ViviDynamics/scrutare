from src.app import check_error

check_error(ValueError('bad'))
rejected = False
try:
    check_error(TypeError('wrong'))
except AssertionError:
    rejected = True
assert rejected
