"""Sliding-window limits, as a contract both backends must keep.

The in-memory window always runs. The Redis one runs when TEST_REDIS_URL points
at a Redis that may be wiped (CI has one) — the same pattern as the
Postgres-only tests: the property that matters with several workers, one shared
count, is the one a single process can never show.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from continuum.services.limits import LoginThrottle, MemoryWindow, RedisWindow, SlidingWindow

REDIS_URL = os.environ.get("TEST_REDIS_URL")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["memory", "redis"])
async def make_window(request):  # noqa: ANN001, ANN201
    if request.param == "memory":
        yield lambda limit, window, clock, name="t": MemoryWindow(limit, window, clock)
        return
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    from redis.asyncio import Redis

    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    yield lambda limit, window, clock, name="t": RedisWindow(redis, name, limit, window, clock)
    await redis.flushdb()
    await redis.aclose()


async def test_a_window_refuses_then_recovers(make_window):
    clock = Clock()
    window: SlidingWindow = make_window(2, 60, clock)
    assert await window.hit("k") is None and await window.hit("k") is None
    assert await window.hit("k") == 60
    clock.now += 30
    assert await window.hit("k") == 30
    clock.now += 31
    assert await window.hit("k") is None


async def test_a_successful_sign_in_does_not_reset_the_address_counter(make_window):
    """Else one valid login would refill an attacker's budget for every other account."""
    throttle = LoginThrottle(make_window(2, 15 * 60, Clock()))
    keys = throttle.keys("victim@example.com", "10.0.0.9")
    await throttle.record_failure(keys)
    await throttle.record_failure(keys)
    await throttle.record_success("attacker@example.com")
    assert await throttle.retry_after(throttle.keys("other@example.com", "10.0.0.9")) is not None
    await throttle.record_success("victim@example.com")
    assert await throttle.retry_after(["email:victim@example.com"]) is None


async def test_workers_sharing_redis_share_one_count():
    """The reason Redis exists here: two workers, one limit, not two."""
    if not REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    from redis.asyncio import Redis

    first, second = Redis.from_url(REDIS_URL), Redis.from_url(REDIS_URL)
    await first.flushdb()
    try:
        clock = Clock()
        a = RedisWindow(first, "llm", 5, 60, clock)
        b = RedisWindow(second, "llm", 5, 60, clock)
        # Ten requests at once across two "workers": exactly five get through.
        results = await asyncio.gather(*(w.hit("mark") for w in [a, b] * 5))
        assert sum(r is None for r in results) == 5
    finally:
        await first.flushdb()
        await first.aclose()
        await second.aclose()


async def test_an_unreachable_redis_fails_open():
    """An outage must not lock every person out of signing in."""
    from redis.asyncio import Redis

    window = RedisWindow(Redis.from_url("redis://127.0.0.1:1/0"), "login", 1, 60)
    assert await window.hit("k") is None and await window.hit("k") is None
    assert await window.retry_after("k") is None
