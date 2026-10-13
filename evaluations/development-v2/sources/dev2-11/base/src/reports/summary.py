"""Summarize order totals per customer name.

`load_customers` fetches the whole customer directory in one remote call.
"""


def totals_by_customer(orders, load_customers):
    customers = load_customers()
    totals = {}
    for order in orders:
        name = customers[order["customer_id"]]
        totals[name] = totals.get(name, 0) + order["total"]
    return totals
