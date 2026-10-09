
def unavailable():
    return float("nan")

def test_unavailable():
    value = unavailable()
    assert value == value
