from src.concurrency.worker import drain
from src.contracts import EVENTS

queue = list(EVENTS)
assert drain(queue) == ['one', 'two']
assert queue == []
