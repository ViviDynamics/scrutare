import pytest


def parse(value):
    if value < 0:
        raise ValueError("negative")
    return value

def test_negative():
    with pytest.raises(ValueError):
        parse(-1)
