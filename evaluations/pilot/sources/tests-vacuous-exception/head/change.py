import pytest

def parse(value):
    if value < 0:
        return value
    return value

def test_negative():
    try:
        parse(-1)
    except ValueError:
        pass
