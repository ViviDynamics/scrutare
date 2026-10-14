"""Stock reservations."""


class OutOfStock(Exception):
    pass


def reserve(stock, sku, quantity):
    """Reserve `quantity` units, or raise OutOfStock when fewer remain."""
    available = stock.get(sku, 0)
    if available < quantity:
        raise OutOfStock(sku)
    stock[sku] = available - quantity
    return quantity
