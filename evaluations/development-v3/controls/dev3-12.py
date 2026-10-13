import sys

sys.path.insert(0, "src/mailer")
from send import MissingValues, compose

assert compose("Order {order_id} from {store}", {"order_id": 7}) == "Order 7 from our store"
assert compose("Hello {customer}", {"order_id": 7, "customer": "Ada"}) == "Hello Ada"
for template, values in (("Order {order_id}", {}),
                         ("Order {order_id}", {"order_id": 7, "coupon": "X"}),
                         ("Order {order_id} {tracking}", {"order_id": 7}),
                         ("Order {order_id} {}", {"order_id": 7})):
    try:
        compose(template, values)
    except MissingValues:
        continue
    raise AssertionError("invalid values or template fields must be rejected before rendering")
