from src.app import authorized

assert not authorized('owner')
assert authorized('reader') and authorized('writer')
