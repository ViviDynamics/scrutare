import asyncio
import sys

sys.path.insert(0, "src")
from async_fetch import fetch_all

state = {"active": 0, "peak": 0}


async def fetch(key):
    state["active"] += 1
    state["peak"] = max(state["peak"], state["active"])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    state["active"] -= 1
    return key * 2


assert asyncio.run(fetch_all(range(6), fetch, 2)) == [0, 2, 4, 6, 8, 10]
assert state["peak"] <= 2, state["peak"]
