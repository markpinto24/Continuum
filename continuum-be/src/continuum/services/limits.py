"""Sliding-window limits: sign-in lockout and per-user request rate.

Two backends behind one async interface. `MemoryWindow` counts in this process —
correct for one uvicorn worker, which is what the project ships. With several
workers each would count separately, multiplying every limit by the worker
count; `RedisWindow` keeps the counters in Redis so every worker shares one
(`REDIS_URL`). Moving to more processes means switching the backend, never
raising the limits.

If Redis cannot be reached, a limit lets the request through and logs an error.
An outage must not lock every person out of signing in, and a guessed password
still costs a full argon2id verification each time.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from continuum.core.logger import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger(__name__)


class SlidingWindow:
    """At most `limit` events per key within `window_seconds`. The interface."""

    limit: int
    window: float

    async def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may act again, or None if it may act now."""
        raise NotImplementedError

    async def record(self, key: str) -> None:
        raise NotImplementedError

    async def hit(self, key: str) -> int | None:
        """Record an event if allowed. Returns seconds to wait if it was refused."""
        raise NotImplementedError

    async def reset(self, key: str) -> None:
        raise NotImplementedError


class MemoryWindow(SlidingWindow):
    """Counts in this process: one worker only."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._events: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        events = self._events.setdefault(key, deque())
        cutoff = self.clock() - self.window
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            # Drop idle keys so a scan of random emails cannot grow memory forever.
            self._events.pop(key, None)
            events = deque()
        return events

    async def retry_after(self, key: str) -> int | None:
        events = self._recent(key)
        if len(events) < self.limit:
            return None
        return max(1, math.ceil(events[0] + self.window - self.clock()))

    async def record(self, key: str) -> None:
        events = self._recent(key)
        events.append(self.clock())
        self._events[key] = events

    async def hit(self, key: str) -> int | None:
        wait = await self.retry_after(key)
        if wait is None:
            await self.record(key)
        return wait

    async def reset(self, key: str) -> None:
        self._events.pop(key, None)


# One sorted set per key, scored by time. Trim, count, and — only if under the
# limit — add, as one script: two workers checking at once cannot both slip in.
# Returns -1 when allowed, else the seconds until the oldest event leaves.
_HIT = """
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) < limit then
  if ARGV[4] == '1' then
    redis.call('ZADD', key, now, ARGV[5])
    redis.call('PEXPIRE', key, math.ceil(window * 1000))
  end
  return '-1'
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return tostring(tonumber(oldest[2]) + window - now)
"""


class RedisWindow(SlidingWindow):
    """Counts in Redis, shared by every worker. Wall-clock time: workers share it."""

    def __init__(
        self,
        redis: Redis,
        name: str,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis
        self.prefix = f"continuum:limit:{name}:"
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._script = redis.register_script(_HIT)

    async def _run(self, key: str, *, add: bool) -> int | None:
        try:
            result = await self._script(
                keys=[self.prefix + key],
                args=[self.clock(), self.window, self.limit, "1" if add else "0",
                      uuid.uuid4().hex],
            )
        except Exception as exc:  # noqa: BLE001 - fail open, loudly
            log.error("limits.redis_unavailable", error=str(exc), limit=self.prefix)
            return None
        wait = float(result)
        return None if wait < 0 else max(1, math.ceil(wait))

    async def retry_after(self, key: str) -> int | None:
        return await self._run(key, add=False)

    async def hit(self, key: str) -> int | None:
        return await self._run(key, add=True)

    async def record(self, key: str) -> None:
        now = self.clock()
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(self.prefix + key, "-inf", now - self.window)
                pipe.zadd(self.prefix + key, {uuid.uuid4().hex: now})
                pipe.pexpire(self.prefix + key, math.ceil(self.window * 1000))
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            log.error("limits.redis_unavailable", error=str(exc), limit=self.prefix)

    async def reset(self, key: str) -> None:
        try:
            await self.redis.delete(self.prefix + key)
        except Exception as exc:  # noqa: BLE001
            log.error("limits.redis_unavailable", error=str(exc), limit=self.prefix)


class LoginThrottle:
    """Locks out an email, and separately a client address, after repeated failures.

    Two keys because they stop different attacks: per-email stops guessing one
    account's password from many addresses; per-address stops one client trying
    one common password against every account.
    """

    def __init__(self, failures: SlidingWindow) -> None:
        self._failures = failures

    @classmethod
    def in_memory(
        cls, max_failures: int, lockout_minutes: int, clock: Callable[[], float] = time.monotonic
    ) -> LoginThrottle:
        return cls(MemoryWindow(max_failures, lockout_minutes * 60, clock))

    @staticmethod
    def keys(email: str, client: str | None) -> list[str]:
        return [f"email:{email}"] + ([f"ip:{client}"] if client else [])

    async def retry_after(self, keys: Iterable[str]) -> int | None:
        waits = [w for k in keys if (w := await self._failures.retry_after(k)) is not None]
        return max(waits) if waits else None

    async def record_failure(self, keys: Iterable[str]) -> None:
        for key in keys:
            await self._failures.record(key)

    async def record_success(self, email: str) -> None:
        # Only the account's counter resets. Clearing the address counter on
        # success would let an attacker with one valid login reset their budget
        # for guessing everyone else's.
        await self._failures.reset(f"email:{email}")


def build_window(
    redis: Redis | None, name: str, limit: int, window_seconds: float
) -> SlidingWindow:
    """Redis when configured, this process otherwise."""
    if redis is None:
        return MemoryWindow(limit, window_seconds)
    return RedisWindow(redis, name, limit, window_seconds)
