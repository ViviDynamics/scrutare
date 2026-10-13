from math import isfinite


def expired(now, deadline):
    'Compare finite numeric monotonic times.'
    if not isfinite(now) or not isfinite(deadline):
        raise ValueError('finite numeric monotonic times required')
    return now >= deadline
