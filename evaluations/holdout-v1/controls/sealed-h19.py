from src.app import fixture

first = fixture()
first['enabled'] = False
assert fixture()['enabled'] is True
