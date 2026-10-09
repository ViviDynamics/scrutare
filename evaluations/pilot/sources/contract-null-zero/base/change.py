def timeout(value):
    """None selects default; zero disables timeout."""
    return 30 if value is None else value
