"""Small Redis coordination primitives shared by Uvicorn workers.

The keys in this module protect ownership, not business data. Redis leases are
token-checked on renew and release so an expired owner can never delete a newer
worker's lock.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any


_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RedisLease:
    """A renewable, compare-and-release distributed lease."""

    def __init__(self, redis: Any, key: str, *, ttl_seconds: int) -> None:
        self._redis = redis
        self.key = key
        self.ttl_seconds = max(3, int(ttl_seconds))
        self.token = uuid.uuid4().hex
        self.acquired = False

    async def acquire(self) -> bool:
        self.acquired = bool(
            await self._redis.set(
                self.key,
                self.token,
                nx=True,
                ex=self.ttl_seconds,
            )
        )
        return self.acquired

    async def renew(self) -> bool:
        if not self.acquired:
            return False
        renewed = await self._redis.eval(
            _RENEW_SCRIPT,
            1,
            self.key,
            self.token,
            self.ttl_seconds,
        )
        self.acquired = bool(renewed)
        return self.acquired

    async def release(self) -> None:
        if not self.acquired:
            return
        try:
            await self._redis.eval(
                _RELEASE_SCRIPT,
                1,
                self.key,
                self.token,
            )
        finally:
            self.acquired = False

    async def keep_alive(self) -> None:
        """Return only when the lease is lost; cancellation stops the loop."""

        interval = max(1.0, self.ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.renew()
            except Exception:
                self.acquired = False
                return
            if not renewed:
                return