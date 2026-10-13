"""Hit ratio of a sample window."""
from window import Window


def hit_ratio(window):
    if not isinstance(window, Window):
        raise TypeError("hit_ratio requires a Window")
    return window.hits / window.total
