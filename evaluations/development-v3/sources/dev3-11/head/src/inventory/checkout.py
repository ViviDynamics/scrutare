"""Checkout. An order is confirmed only for units that were reserved."""
from stock import reserve


def confirm(stock, order):
    confirmed = {}
    for sku, quantity in order.items():
        reserve(stock, sku, quantity)
        confirmed[sku] = quantity
    return confirmed
