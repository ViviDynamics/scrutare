import sys

sys.path.insert(0, "src/plugins")
import api
import builtin
import registry

assert set(registry.registered()) == {"csv-export", "audit-log"}
assert api.register is registry.register and api.Plugin is registry.Plugin
assert api.describe(builtin.CSV_EXPORT) == "csv-export (1.2)"
describe_all = getattr(api, "describe_all", None)
if describe_all is not None:
    assert describe_all(registry.registered().values()) == [
        "audit-log (2.0)", "csv-export (1.2)"]
