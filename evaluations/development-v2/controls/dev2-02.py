import sys

sys.path.insert(0, "src")
from profile_cache import ProfileCache

cache = ProfileCache(lambda tenant, user: f"{tenant}:{user}")
assert cache.get("a", 1) == "a:1"
assert cache.get("b", 1) == "b:1", cache.get("b", 1)
