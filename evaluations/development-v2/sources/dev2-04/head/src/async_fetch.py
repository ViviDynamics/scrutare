"""Fetch many resources with a bounded number of concurrent requests."""
import asyncio


async def _bounded(key, fetch, limit):
    async with asyncio.Semaphore(limit):
        return await fetch(key)


async def fetch_all(keys, fetch, limit):
    return await asyncio.gather(*(_bounded(key, fetch, limit) for key in keys))
