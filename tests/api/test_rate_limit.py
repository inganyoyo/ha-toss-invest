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


async def test_rate_limit_lock_non_contention(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RateLimiter()
    # Throttling STOCK for 100 seconds
    await limiter.async_update("STOCK", {"Retry-After": "100"})

    # Start async_wait. It should reserve slot, release lock, and sleep.
    wait_task = asyncio.create_task(limiter.async_wait("STOCK"))
    await asyncio.sleep(0.01)  # Let it enter wait and release lock

    # While it's sleeping, calling async_update must complete immediately (no block).
    update_task = asyncio.create_task(limiter.async_update("STOCK", {"X-RateLimit-Limit": "5"}))
    await asyncio.wait_for(update_task, timeout=1.0)

    wait_task.cancel()


async def test_parse_retry_after_http_date() -> None:
    from custom_components.toss_invest.api.rate_limit import parse_retry_after

    # Mock clock to return a fixed epoch
    fixed_epoch = 946684798.0  # Fri, 31 Dec 1999 23:59:58 GMT

    def clock() -> float:
        return fixed_epoch

    # Test HTTP-date exactly 1 second in the future
    res = parse_retry_after("Fri, 31 Dec 1999 23:59:59 GMT", clock=clock)
    assert res == pytest.approx(1.0)

    # Test HTTP-date in the past
    res_past = parse_retry_after("Fri, 31 Dec 1999 23:59:50 GMT", clock=clock)
    assert res_past == 0.0


async def test_parse_retry_after_malformed() -> None:
    from custom_components.toss_invest.api.rate_limit import parse_retry_after

    # Non-number/non-date values should fallback to default
    assert parse_retry_after("invalid-header", default=3.5) == 3.5
    assert parse_retry_after("", default=2.0) == 2.0
    assert parse_retry_after(None, default=1.5) == 1.5


async def test_parse_retry_after_negative() -> None:
    from custom_components.toss_invest.api.rate_limit import parse_retry_after

    assert parse_retry_after("-10", default=1.0) == 0.0


@pytest.mark.parametrize("bad_val", ["inf", "-inf", "nan", "Infinity", "1e400"])
async def test_retry_after_non_finite_values(
    limiter: RateLimiter, clock: FakeClock, bad_val: str
) -> None:
    await limiter.async_update("STOCK", {"Retry-After": bad_val})
    start = clock.now
    await asyncio.wait_for(limiter.async_wait("STOCK"), timeout=1.0)
    assert clock.now - start == pytest.approx(0.0)


@pytest.mark.parametrize("bad_val", ["inf", "-inf", "nan", "Infinity", "1e400"])
async def test_reset_non_finite_values(
    limiter: RateLimiter, clock: FakeClock, bad_val: str
) -> None:
    await limiter.async_update(
        "STOCK", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": bad_val}
    )
    start = clock.now
    await asyncio.wait_for(limiter.async_wait("STOCK"), timeout=1.0)
    assert clock.now - start == pytest.approx(0.0)
