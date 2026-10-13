from operator import itemgetter


def ordered(rows):
    return sorted(rows, key=itemgetter(0))
