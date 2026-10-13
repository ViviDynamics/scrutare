from src.concurrency.worker import drain

queue = [1, 2]
assert drain(queue) == [1, 2]
assert queue == []
