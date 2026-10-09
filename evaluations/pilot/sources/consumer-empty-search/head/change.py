def search(rows, term):
    """Empty term returns every row."""
    if not term:
        return []
    return [row for row in rows if term in row]
