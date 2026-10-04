"""In-process sliding-window limits: sign-in lockout and per-user request rate.

Both keep their counters in memory. That is correct for the deployment this
project ships — one uvicorn process — and wrong for several: each worker would
count separately, multiplying every limit by the worker count. Moving to more
than one process means moving these counters to Redis, not raising the limits.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Iterable


class SlidingWindow:
    """At most `limit` events per key within `window_seconds`."""

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

    def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may act again, or None if it may act now."""
        events = self._recent(key)
        if len(events) < self.limit:
            return None
        return max(1, math.ceil(events[0] + self.window - self.clock()))

    def record(self, key: str) -> None:
        events = self._recent(key)
        events.append(self.clock())
        self._events[key] = events

    def hit(self, key: str) -> int | None:
        """Record an event if allowed. Returns seconds to wait if it was refused."""
        wait = self.retry_after(key)
        if wait is None:
            self.record(key)
        return wait

    def reset(self, key: str) -> None:
        self._events.pop(key, None)


class LoginThrottle:
    """Locks out an email, and separately a client address, after repeated failures.

    Two keys because they stop different attacks: per-email stops guessing one
    account's password from many addresses; per-address stops one client trying
    one common password against every account.
    """

    def __init__(
        self,
        max_failures: int,
        lockout_minutes: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failures = SlidingWindow(max_failures, lockout_minutes * 60, clock)

    @staticmethod
    def keys(email: str, client: str | None) -> list[str]:
        return [f"email:{email}"] + ([f"ip:{client}"] if client else [])

    def retry_after(self, keys: Iterable[str]) -> int | None:
        waits = [w for k in keys if (w := self._failures.retry_after(k)) is not None]
        return max(waits) if waits else None

    def record_failure(self, keys: Iterable[str]) -> None:
        for key in keys:
            self._failures.record(key)

    def record_success(self, email: str) -> None:
        # Only the account's counter resets. Clearing the address counter on
        # success would let an attacker with one valid login reset their budget
        # for guessing everyone else's.
        self._failures.reset(f"email:{email}")
