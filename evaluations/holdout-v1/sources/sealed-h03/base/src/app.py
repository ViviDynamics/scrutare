from decimal import ROUND_HALF_UP, Decimal


def cents(text):
    return Decimal(text).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
