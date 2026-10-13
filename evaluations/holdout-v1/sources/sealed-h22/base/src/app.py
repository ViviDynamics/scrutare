def check_empty(values):
    'Check a built-in list fixture.'
    if type(values) is not list:
        raise TypeError('built-in list fixture required')
    assert values == []
