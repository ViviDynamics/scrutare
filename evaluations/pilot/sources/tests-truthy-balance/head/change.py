def remaining(total, spent):
    return total + spent

def test_balance():
    assert remaining(10, 3)
