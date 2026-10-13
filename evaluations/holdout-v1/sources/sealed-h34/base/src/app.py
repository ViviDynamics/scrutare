def mode(name):
    if name not in {'fast', 'safe'}:
        raise ValueError('unknown mode')
    return name
