"""Shared FastAPI dependencies."""

from collections.abc import AsyncIterator

import httpx


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """A short-lived httpx client per request. Phase 2's traffic (a handful
    of admin API calls) doesn't warrant a shared pooled client with its own
    lifecycle management — that can be revisited if/when it matters.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        yield client
