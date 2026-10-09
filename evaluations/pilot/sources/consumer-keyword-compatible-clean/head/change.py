def connect(timeout=30, *, deadline=None):
    """deadline optionally replaces timeout; existing callers remain valid."""
    return timeout if deadline is None else deadline
