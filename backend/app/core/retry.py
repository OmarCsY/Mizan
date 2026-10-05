"""One retry with backoff for external calls (CLAUDE.md conventions)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = 2,
    backoff_s: float = 0.5,
) -> T:
    """Call `fn`; on an exception in `retry_on`, wait `backoff_s * 2**i` and try again, up to `attempts` calls."""
    for i in range(attempts):
        try:
            return await fn()
        except retry_on:
            if i == attempts - 1:
                raise
            await asyncio.sleep(backoff_s * (2**i))
    raise AssertionError("unreachable")
