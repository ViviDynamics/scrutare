def acquire(permits):
    if permits <= 0:
        return permits, False
    return permits - 1, True
