import sys

sys.path.insert(0, "src/inventory")
from checkout import confirm
from stock import OutOfStock

stock = {"bolt": 2}
try:
    confirmed = confirm(stock, {"bolt": 5})
except OutOfStock:
    confirmed = {}
assert confirmed.get("bolt", 0) <= 2, confirmed
assert confirm({"bolt": 5}, {"bolt": 5}) == {"bolt": 5}
