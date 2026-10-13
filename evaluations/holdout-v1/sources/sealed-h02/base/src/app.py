from src.contracts import SIZE


def batches(count):
    return (count + SIZE - 1) // SIZE
