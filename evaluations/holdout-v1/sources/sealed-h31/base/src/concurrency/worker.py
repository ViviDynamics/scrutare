def drain(queue):
    'Drain a built-in list queue, returning an independently owned list.'
    if type(queue) is not list:
        raise TypeError('built-in list queue required')
    batch = queue.copy()
    queue.clear()
    return batch
