from src.app import local

assert local('127.0.0.1') and local('::1')
assert not local('10.0.0.1') and not local('203.0.113.5')
