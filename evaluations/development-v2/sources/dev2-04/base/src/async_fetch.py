"""Fetch many resources with a bounded number of concurrent requests."""
import asyncio


async def fetch_all(keys, fetch, limit):
    semaphore = asyncio.Semaphore(limit)

    async def one(key):
        async with semaphore:
            return await fetch(key)

    return await asyncio.gather(*(one(key) for key in keys))
