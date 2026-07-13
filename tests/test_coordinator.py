from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed

from custom_components.toss_invest.api import (
    TossApiError,
    TossAuthError,
    TossRateLimitError,
)
from custom_components.toss_invest.coordinator import create_runtime, market_session_is_open
from custom_components.toss_invest.models import TossDataError


def fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures") / name).read_text())


def client() -> AsyncMock:
    api = AsyncMock()
    api.async_get_holdings.return_value = fixture("holdings.json")
    api.async_get_prices.return_value = fixture("prices.json")
    market = fixture("market.json")
    assert isinstance(market, dict)
    api.async_get_exchange_rate.return_value = market["exchangeRate"]
    api.async_get_market_calendar.side_effect = [
        market["krMarketCalendar"],
        market["usMarketCalendar"],
    ]
    return api


async def test_independent_failure_keeps_last_good_and_marks_only_prices_stale(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()
    good_prices = runtime.prices.data

    api.async_get_prices.side_effect = TossApiError("request", "temporary")
    await runtime.prices.async_refresh()

    assert runtime.holdings.last_update_success is True
    assert runtime.prices.last_update_success is False
    assert runtime.prices.data == good_prices
    assert runtime.stale_groups == {"prices"}
    assert runtime.holdings.last_success is not None
    assert runtime.prices.last_success is not None


async def test_price_quotes_are_decimal_and_indexed_by_symbol(hass) -> None:
    runtime = create_runtime(hass, client(), "account", {})
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()

    assert runtime.prices.data["TEST"].last_price == Decimal("10.10")
    assert set(runtime.prices.data) == {"TEST", "SNTZ"}


async def test_price_refresh_uses_current_holding_symbols(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()
    assert api.async_get_prices.await_args.args[0] == ["SNTZ", "TEST"]

    changed = fixture("holdings.json")
    assert isinstance(changed, dict)
    changed["items"] = changed["items"][:1]
    api.async_get_holdings.return_value = changed
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()
    assert api.async_get_prices.await_args.args[0] == ["SNTZ"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TossApiError(None, "temporary"), "UpdateFailed"),
        (TossRateLimitError(1), "UpdateFailed"),
        (TossDataError("bad data"), "UpdateFailed"),
    ],
)
async def test_transient_rate_and_parse_failures_map_to_update_failed(
    hass, error: Exception, expected: str
) -> None:
    api = client()
    api.async_get_holdings.side_effect = error
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    assert runtime.holdings.last_update_success is False
    assert runtime.holdings.last_exception.__class__.__name__ == expected


async def test_auth_failure_requests_reauthentication(hass) -> None:
    api = client()
    api.async_get_holdings.side_effect = TossAuthError("invalid")
    runtime = create_runtime(hass, api, "account", {})
    with pytest.raises(ConfigEntryAuthFailed):
        await runtime.holdings._async_update_data()
    assert "holdings" in runtime.stale_groups


async def test_concurrent_refreshes_are_coalesced(hass) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    api = client()

    async def slow_holdings(account: str) -> object:
        started.set()
        await release.wait()
        return fixture("holdings.json")

    api.async_get_holdings.side_effect = slow_holdings
    runtime = create_runtime(hass, api, "account", {})
    first = asyncio.create_task(runtime.holdings.async_request_refresh())
    await started.wait()
    second = asyncio.create_task(runtime.holdings.async_request_refresh())
    release.set()
    await asyncio.gather(first, second)
    assert api.async_get_holdings.await_count == 1


def calendar(country: str, start: datetime, end: datetime) -> dict:
    session = {"startTime": start.isoformat(), "endTime": end.isoformat()}
    if country == "KR":
        return {"today": {"integrated": {"regularMarket": session}}}
    return {"today": {"regularMarket": session}}


@pytest.mark.parametrize("country", ["KR", "US"])
def test_market_session_uses_actual_current_instant(country: str) -> None:
    now = datetime.fromisoformat("2026-07-13T10:00:00+09:00")
    assert market_session_is_open(
        calendar(country, now - timedelta(hours=1), now + timedelta(minutes=1)), country, now
    )
    assert not market_session_is_open(
        calendar(country, now - timedelta(hours=1), now), country, now
    )
    assert not market_session_is_open(
        calendar(country, now + timedelta(seconds=1), now + timedelta(hours=1)), country, now
    )


async def test_schedule_considers_only_markets_in_current_holdings(hass) -> None:
    api = client()
    runtime = create_runtime(
        hass,
        api,
        "account",
        {"open_price_interval": 17, "closed_price_interval": 701},
    )
    await runtime.holdings.async_refresh()
    now = datetime.now().astimezone()
    api.async_get_market_calendar.side_effect = [
        calendar("KR", now - timedelta(minutes=1), now + timedelta(minutes=1)),
        calendar("US", now + timedelta(hours=1), now + timedelta(hours=2)),
    ]
    await runtime.reference.async_refresh()
    assert runtime.prices.update_interval == timedelta(seconds=17)

    kr_only = fixture("holdings.json")
    assert isinstance(kr_only, dict)
    kr_only["items"] = [item for item in kr_only["items"] if item["marketCountry"] == "KR"]
    api.async_get_holdings.return_value = kr_only
    await runtime.holdings.async_refresh()
    runtime.reference.kr_calendar = calendar(
        "KR", now + timedelta(hours=1), now + timedelta(hours=2)
    )
    runtime.reference.us_calendar = calendar(
        "US", now - timedelta(minutes=1), now + timedelta(minutes=1)
    )
    runtime.reschedule_prices(now)
    assert runtime.prices.update_interval == timedelta(seconds=701)


async def test_us_open_and_all_closed_schedules_are_deterministic(hass) -> None:
    now = datetime.fromisoformat("2026-07-13T10:00:00+09:00")
    api = client()
    us_only = fixture("holdings.json")
    assert isinstance(us_only, dict)
    us_only["items"] = [item for item in us_only["items"] if item["marketCountry"] == "US"]
    api.async_get_holdings.return_value = us_only
    runtime = create_runtime(
        hass,
        api,
        "account",
        {"open_price_interval": 19, "closed_price_interval": 703},
        now_fn=lambda: now,
    )
    await runtime.holdings.async_refresh()
    runtime.reference.kr_calendar = calendar(
        "KR", now + timedelta(hours=1), now + timedelta(hours=2)
    )
    runtime.reference.us_calendar = calendar(
        "US", now - timedelta(minutes=1), now + timedelta(minutes=1)
    )
    runtime.reschedule_prices(now)
    assert runtime.prices.update_interval == timedelta(seconds=19)

    runtime.reference.us_calendar = calendar(
        "US", now + timedelta(hours=1), now + timedelta(hours=2)
    )
    runtime.reschedule_prices(now)
    assert runtime.prices.update_interval == timedelta(seconds=703)


async def test_reference_snapshot_and_timer_use_actual_session_time(hass) -> None:
    now = datetime.fromisoformat("2026-07-13T10:00:00+09:00")
    api = client()
    api.async_get_market_calendar.side_effect = [
        calendar("KR", now - timedelta(minutes=1), now + timedelta(minutes=1)),
        calendar("US", now + timedelta(hours=1), now + timedelta(hours=2)),
    ]
    runtime = create_runtime(
        hass,
        api,
        "account",
        {"open_price_interval": 13, "closed_price_interval": 607},
        now_fn=lambda: now,
    )
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()
    api.async_get_prices.reset_mock()
    remove_listener = runtime.prices.async_add_listener(lambda: None)
    old_timer = runtime.prices._unsub_refresh
    await runtime.reference.async_refresh()
    await hass.async_block_till_done()
    assert runtime.reference.data.kr_market_open is True
    assert runtime.reference.data.us_market_open is False
    assert runtime.prices.update_interval == timedelta(seconds=13)
    api.async_get_prices.assert_awaited_once()
    assert runtime.prices._unsub_refresh is not old_timer
    remove_listener()
    await runtime.async_shutdown()


async def test_failed_reference_parse_preserves_last_good_calendars(hass) -> None:
    now = datetime.fromisoformat("2026-07-13T10:00:00+09:00")
    api = client()
    runtime = create_runtime(hass, api, "account", {}, now_fn=lambda: now)
    await runtime.reference.async_refresh()
    good_kr = runtime.reference.kr_calendar
    api.async_get_exchange_rate.return_value = {"rate": "not-decimal"}
    api.async_get_market_calendar.side_effect = [
        calendar("KR", now, now + timedelta(hours=1)),
        calendar("US", now, now + timedelta(hours=1)),
    ]
    await runtime.reference.async_refresh()
    assert runtime.reference.last_update_success is False
    assert runtime.reference.kr_calendar is good_kr


async def test_reference_callback_failure_preserves_calendar_and_freshness(hass) -> None:
    now = datetime.fromisoformat("2026-07-13T10:00:00+09:00")
    api = client()
    runtime = create_runtime(hass, api, "account", {}, now_fn=lambda: now)
    await runtime.reference.async_refresh()
    good_kr = runtime.reference.kr_calendar
    good_success = runtime.reference.last_success
    api.async_get_market_calendar.side_effect = [
        calendar("KR", now, now + timedelta(hours=1)),
        calendar("US", now, now + timedelta(hours=1)),
    ]

    with patch.object(
        type(runtime), "reschedule_prices", side_effect=RuntimeError("scheduling failed")
    ):
        await runtime.reference.async_refresh()

    assert runtime.reference.last_update_success is False
    assert runtime.reference.last_success is good_success
    assert runtime.reference.kr_calendar is good_kr
    assert "reference" in runtime.stale_groups


async def test_timeout_maps_to_update_failed(hass) -> None:
    api = client()
    api.async_get_holdings.side_effect = asyncio.TimeoutError()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    assert runtime.holdings.last_update_success is False
    assert runtime.holdings.last_exception.__class__.__name__ == "UpdateFailed"
