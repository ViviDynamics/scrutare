from src.contracts import EXCLUSIVE


def valid(now, expires):
    return now < expires if EXCLUSIVE else now <= expires
