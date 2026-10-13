def drain(queue):
    batch = list(queue)
    queue.clear()
    return batch
