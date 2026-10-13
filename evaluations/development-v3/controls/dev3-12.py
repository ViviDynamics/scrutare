import sys

sys.path.insert(0, "src/mailer")
from send import MissingValues, compose

assert compose("Order {order_id} from {store}", {"order_id": 7}) == "Order 7 from our store"
assert compose("Hello {customer}", {"order_id": 7, "customer": "Ada"}) == "Hello Ada"
for values in ({}, {"order_id": 7, "coupon": "X"}):
    try:
        compose("Order {order_id}", values)
    except MissingValues:
        continue
    raise AssertionError("invalid values must be rejected before rendering")
