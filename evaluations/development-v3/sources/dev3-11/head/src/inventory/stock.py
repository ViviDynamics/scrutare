"""Stock reservations."""


class OutOfStock(Exception):
    pass


def reserve(stock, sku, quantity):
    """Reserve up to `quantity` units and return how many were reserved."""
    available = stock.get(sku, 0)
    granted = min(available, quantity)
    stock[sku] = available - granted
    return granted
