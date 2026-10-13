from decimal import Decimal


def cents(text):
    return Decimal(text).quantize(Decimal('0.01'))
