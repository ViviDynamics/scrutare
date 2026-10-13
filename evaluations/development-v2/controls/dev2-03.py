import sys

sys.path.insert(0, "src")
from worker import Worker

worker = Worker()
for item in range(5):
    worker.submit(item)
assert worker.next_batch(2) == [0, 1]
assert worker.pending() == 3
assert worker.next_batch(10) == [2, 3, 4]
assert worker.next_batch(1) == [] and worker.pending() == 0
