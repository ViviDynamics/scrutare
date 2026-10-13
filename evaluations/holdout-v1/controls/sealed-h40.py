from src.app import values

assert values() == (1, 2)
assert isinstance(values(), tuple)
