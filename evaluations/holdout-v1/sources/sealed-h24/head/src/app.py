def check_error(error):
    accepted = isinstance(error, ValueError)
    assert accepted
