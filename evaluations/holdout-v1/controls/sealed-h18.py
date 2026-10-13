from src.app import check_payload

check_payload({'result': 1, 'request_id': 'r'})
rejected = False
try:
    check_payload({'result': 1})
except AssertionError:
    rejected = True
assert rejected
