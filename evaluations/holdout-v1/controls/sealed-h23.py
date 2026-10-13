from src.app import fixture

a = fixture()
a.append(3)
assert fixture() == [1, 2]
