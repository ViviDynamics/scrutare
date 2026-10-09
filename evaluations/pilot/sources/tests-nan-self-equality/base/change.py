import math

def unavailable():
    return float("nan")

def test_unavailable():
    assert math.isnan(unavailable())
