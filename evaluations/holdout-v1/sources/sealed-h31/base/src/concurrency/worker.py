def drain(queue):
    batch = queue.copy()
    queue.clear()
    return batch
