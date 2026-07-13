from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import time
from collections.abc import Callable, Mapping


class TossRateLimitError(Exception):
    """Raised when the Toss Open API rejects a request with HTTP 429."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Rate limited for {retry_after} seconds")
        self.retry_after = retry_after


class _GroupLimiter:
    __slots__ = ("lock", "next_allowed", "min_interval")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_allowed = 0.0
        self.min_interval = 0.0


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_retry_after(
    value: str | None, default: float = 1.0, clock: Callable[[], float] = time.time
) -> float:
    """Parse a Retry-After header. Handles delta-seconds, HTTP-date, and malformed inputs."""
    if value is None:
        return default
    val_str = str(value).strip()
    if not val_str:
        return default
    try:
        val = float(val_str)
        if val < 0:
            return 0.0
        return val
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(val_str)
        if dt is not None:
            dt_epoch = dt.timestamp()
            current_epoch = clock()
            delay = dt_epoch - current_epoch
            return max(0.0, delay)
    except Exception:
        pass
    return default


class RateLimiter:
    """Per-group, concurrency-safe request pacing driven by Toss rate-limit headers.

    Each Toss "Rate Limits Group" (e.g. `ASSET`, `MARKET_DATA`) gets its own
    `asyncio.Lock` and `next_allowed` monotonic timestamp, so throttling one group
    never blocks another. Published limits are only defaults; the actual pacing is
    driven by `X-RateLimit-*` and `Retry-After` response headers as they arrive.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        clock_epoch: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._clock_epoch = clock_epoch
        self._groups: dict[str, _GroupLimiter] = {}

    def _group(self, group: str) -> _GroupLimiter:
        state = self._groups.get(group)
        if state is None:
            state = _GroupLimiter()
            self._groups[group] = state
        return state

    async def async_wait(self, group: str) -> None:
        state = self._group(group)
        async with state.lock:
            now = self._clock()
            start_time = max(state.next_allowed, now)
            delay = start_time - now
            state.next_allowed = start_time + state.min_interval

        if delay > 0:
            await asyncio.sleep(delay)

    async def async_update(self, group: str, headers: Mapping[str, str]) -> None:
        state = self._group(group)
        async with state.lock:
            now = self._clock()

            limit = _parse_int(headers.get("X-RateLimit-Limit"))
            if limit is not None and limit > 0:
                state.min_interval = 1.0 / limit

            remaining = _parse_int(headers.get("X-RateLimit-Remaining"))
            reset = _parse_float(headers.get("X-RateLimit-Reset"))
            if remaining is not None and remaining <= 0 and reset is not None:
                state.next_allowed = max(state.next_allowed, now + reset)

            retry_after = parse_retry_after(
                headers.get("Retry-After"), default=0.0, clock=self._clock_epoch
            )
            if retry_after > 0:
                state.next_allowed = max(state.next_allowed, now + retry_after)
