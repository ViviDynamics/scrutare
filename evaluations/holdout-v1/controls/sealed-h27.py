from src.concurrency.semaphore import acquire

assert acquire(0) == (0, False)
