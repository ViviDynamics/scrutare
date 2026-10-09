def search(rows, term):
    """Empty term returns every row."""
    return [row for row in rows if term in row]
