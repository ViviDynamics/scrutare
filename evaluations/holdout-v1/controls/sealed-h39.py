from src.app import preview

assert preview('éclair', 1) == 'é'
assert preview('', 2) == ''
