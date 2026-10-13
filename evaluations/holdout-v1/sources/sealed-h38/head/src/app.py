def fetch(*, limit=None, size=None):
    count = limit if limit is not None else size
    return list(range(count))
