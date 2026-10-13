from src.app import mode

rejected = False
try:
    mode('sfae')
except ValueError:
    rejected = True
assert rejected
