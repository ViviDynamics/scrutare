import sys

sys.path.insert(0, "src/reports")
from summary import totals_by_customer

calls = []


def load_customers():
    calls.append(1)
    return {1: "ada", 2: "lin"}


orders = [{"customer_id": 1 + n % 2, "total": 10} for n in range(6)]
assert totals_by_customer(orders, load_customers) == {"ada": 30, "lin": 30}
assert len(calls) == 1, len(calls)
