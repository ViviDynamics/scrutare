from src.app import allowed

assert not allowed('/data/private2/key', '/data/private')
