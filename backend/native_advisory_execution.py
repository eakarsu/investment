"""Bounded native provider transport. Never infer cancellation from request-body completion."""
import asyncio
import time
from fastapi import HTTPException

_requests = {}

def claim(owner, limit=20, window=3600):
    now = time.monotonic()
    for key in [key for key, row in _requests.items() if row[1] <= now]:
        del _requests[key]
    count, expires = _requests.get(str(owner), (0, now + window))
    if count >= limit:
        raise HTTPException(429, "Hourly AI request limit reached", headers={"Retry-After": str(max(1, int(expires-now)))})
    if str(owner) not in _requests and len(_requests) >= 10000:
        raise HTTPException(503, "AI request capacity is temporarily unavailable")
    _requests[str(owner)] = (count + 1, expires)

async def connected(request):
    if await request.is_disconnected():
        raise HTTPException(499, "AI request cancelled")

async def run_native(request, owner, operation, timeout=60):
    claim(owner)
    await connected(request)
    task = asyncio.ensure_future(operation())
    async def watch():
        while True:
            await connected(request)
            await asyncio.sleep(0.025)
    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait({task, watcher}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
        if task not in done:
            raise HTTPException(504, "OpenRouter request timed out")
        result = await task
        await connected(request)
        return result
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)
