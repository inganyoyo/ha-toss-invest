from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.toss_invest.api.rate_limit import RateLimiter


class FakeClock:
    """A monotonic clock that only advances when told to, so tests never sleep for real."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> RateLimiter:
    """A RateLimiter whose `asyncio.sleep` calls advance the fake clock instead of blocking."""

    async def fake_sleep(delay: float) -> None:
        clock.advance(delay)

    monkeypatch.setattr(
        "custom_components.toss_invest.api.rate_limit.asyncio.sleep",
        AsyncMock(side_effect=fake_sleep),
    )
    return RateLimiter(clock=clock)


def _headers(**kwargs: str) -> dict[str, str]:
    return dict(kwargs)


async def test_async_wait_does_not_delay_before_any_limit_is_known(limiter: RateLimiter) -> None:
    await limiter.async_wait("ASSET")
    await limiter.async_wait("ASSET")
    # No X-RateLimit-Limit has ever been observed, so back-to-back calls proceed immediately.


async def test_async_update_sets_min_interval_from_rate_limit_limit_header(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    await limiter.async_update("MARKET_DATA", _headers(**{"X-RateLimit-Limit": "2"}))
    await limiter.async_wait("MARKET_DATA")

    start = clock.now
    await limiter.async_wait("MARKET_DATA")
    assert clock.now - start == pytest.approx(0.5)


async def test_async_update_honors_rate_limit_remaining_zero_and_reset(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    await limiter.async_update(
        "STOCK", _headers(**{"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5"})
    )
    start = clock.now
    await limiter.async_wait("STOCK")
    assert clock.now - start == pytest.approx(5.0)


async def test_async_update_honors_retry_after_header(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    await limiter.async_update("RANKING", _headers(**{"Retry-After": "3"}))
    start = clock.now
    await limiter.async_wait("RANKING")
    assert clock.now - start == pytest.approx(3.0)


async def test_async_update_ignores_malformed_headers(limiter: RateLimiter) -> None:
    await limiter.async_update(
        "MARKET_INFO",
        _headers(**{"X-RateLimit-Limit": "not-a-number", "X-RateLimit-Reset": "also-not"}),
    )
    # Malformed headers must never raise or wedge the group.
    await limiter.async_wait("MARKET_INFO")


async def test_groups_are_independent(limiter: RateLimiter, clock: FakeClock) -> None:
    await limiter.async_update("ASSET", _headers(**{"Retry-After": "10"}))

    start = clock.now
    await limiter.async_wait("ACCOUNT")
    assert clock.now == start  # ACCOUNT was never throttled, so it proceeds immediately.


async def test_concurrent_waits_on_same_group_are_serialized_and_spaced(
    limiter: RateLimiter, clock: FakeClock
) -> None:
    await limiter.async_update("MARKET_DATA_CHART", _headers(**{"X-RateLimit-Limit": "1"}))

    observed: list[float] = []

    async def call() -> None:
        await limiter.async_wait("MARKET_DATA_CHART")
        observed.append(clock.now)

    await asyncio.gather(call(), call(), call())

    observed.sort()
    assert observed == [pytest.approx(0.0), pytest.approx(1.0), pytest.approx(2.0)]
