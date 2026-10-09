def timeout(value):
    """None selects 30; zero disables the timeout."""
    if value is None:
        return 30
    return value
