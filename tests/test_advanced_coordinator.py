from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from custom_components.toss_invest.api import TossApiError
from custom_components.toss_invest.coordinator import (
    KOREAN_MARKET_INDICATORS,
    create_runtime,
)


def fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures") / name).read_text())


def client(*, empty: bool = False) -> AsyncMock:
    api = AsyncMock()
    holdings = fixture("holdings.json")
    assert isinstance(holdings, dict)
    if empty:
        holdings["items"] = []
    api.async_get_holdings.return_value = holdings
    api.async_get_prices.return_value = [] if empty else fixture("prices.json")
    api.async_get_candles.return_value = {"candles": [], "nextBefore": None}
    api.async_get_warnings.return_value = []
    api.async_get_buying_power.side_effect = lambda _account, currency: {
        "currency": currency,
        "cashBuyingPower": "5000000" if currency == "KRW" else "123.45",
    }
    api.async_get_market_indicators.return_value = [
        {"symbol": symbol, "timestamp": None, "lastPrice": str(index + 1)}
        for index, symbol in enumerate(KOREAN_MARKET_INDICATORS)
    ]
    api.async_get_investor_trading.side_effect = lambda symbol, **_kwargs: {
        "records": [
            {
                "date": "2026-07-10",
                "updatedAt": "2026-07-10T18:10:00+09:00",
                "individual": {"buyAmount": "10", "sellAmount": "20"},
                "foreigner": {"buyAmount": "30", "sellAmount": "40"},
                "institution": {"buyAmount": "50", "sellAmount": "60"},
                "otherCorporation": {"buyAmount": "70", "sellAmount": "80"},
            }
        ],
        "nextUntil": None,
    }
    api.async_get_rankings.side_effect = lambda **kwargs: {
        "rankedAt": "2026-07-13T10:00:00+09:00",
        "rankings": [
            {
                "rank": 1,
                "symbol": f"{kwargs['market_country']}-ONE",
                "currency": "KRW" if kwargs["market_country"] == "KR" else "USD",
                "price": {
                    "lastPrice": "101.5",
                    "basePrice": "100",
                    "changeRate": "0.015",
                },
                "tradingVolume": "1234",
                "tradingAmount": "5678.90",
            }
        ],
    }
    market = fixture("market.json")
    assert isinstance(market, dict)
    api.async_get_exchange_rate.return_value = market["exchangeRate"]
    api.async_get_market_calendar.side_effect = lambda country: market[
        "krMarketCalendar" if country == "KR" else "usMarketCalendar"
    ]
    return api


async def test_candles_page_in_chunks_deduplicate_and_use_dynamic_symbols(hass) -> None:
    api = client()
    first = fixture("candles.json")
    assert isinstance(first, dict)
    overlap = first["candles"][-1]
    older = dict(overlap, timestamp="2026-07-03T09:00:00+09:00")
    api.async_get_candles.side_effect = [
        {"candles": first["candles"], "nextBefore": "page-2"},
        {"candles": [overlap, older], "nextBefore": None},
        {"candles": [], "nextBefore": None},
    ]
    runtime = create_runtime(hass, api, "account", {"candle_lookback": 500})
    await runtime.holdings.async_refresh()
    await runtime.candles.async_refresh()

    assert list(runtime.candles.data) == ["SNTZ", "TEST"]
    candles = runtime.candles.data["SNTZ"]
    assert len(candles) == 6
    assert candles[0].timestamp == first["candles"][0]["timestamp"]
    assert candles[-1].timestamp == older["timestamp"]
    assert candles[0].close == Decimal("10.10")
    assert api.async_get_candles.await_args_list[0].kwargs == {
        "count": 200,
        "interval": "1d",
        "adjusted": True,
    }
    assert api.async_get_candles.await_args_list[1].kwargs["before"] == "page-2"

    changed = fixture("holdings.json")
    assert isinstance(changed, dict)
    changed["items"] = changed["items"][:1]
    api.async_get_holdings.return_value = changed
    api.async_get_candles.side_effect = [{"candles": [], "nextBefore": None}]
    await runtime.holdings.async_refresh()
    await runtime.candles.async_refresh()
    assert list(runtime.candles.data) == ["SNTZ"]


async def test_candle_paging_stops_on_repeated_cursor_and_requested_limit(hass) -> None:
    api = client()
    holdings = fixture("holdings.json")
    assert isinstance(holdings, dict)
    holdings["items"] = holdings["items"][:1]
    api.async_get_holdings.return_value = holdings
    candle = fixture("candles.json")
    assert isinstance(candle, dict)
    row = candle["candles"][0]
    api.async_get_candles.side_effect = [
        {
            "candles": [dict(row, timestamp=f"2026-07-{day:02d}") for day in range(20, 0, -1)],
            "nextBefore": "same",
        },
        {"candles": [row], "nextBefore": "same"},
        {"candles": [], "nextBefore": None},
    ]
    runtime = create_runtime(hass, api, "account", {"candle_lookback": 25})
    await runtime.holdings.async_refresh()
    await runtime.candles.async_refresh()
    assert len(runtime.candles.data["SNTZ"]) == 21
    assert api.async_get_candles.await_count == 2
    assert api.async_get_candles.await_args_list[0].kwargs["count"] == 25
    assert api.async_get_candles.await_args_list[1].kwargs["count"] == 5


async def test_per_symbol_candles_and_warnings_have_bounded_concurrency(hass) -> None:
    api = client()
    holdings = fixture("holdings.json")
    assert isinstance(holdings, dict)
    template = holdings["items"][0]
    holdings["items"] = [dict(template, symbol=f"SYM{index}") for index in range(9)]
    api.async_get_holdings.return_value = holdings
    active = 0
    maximum = 0

    async def bounded_result(*_args, **_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return {"candles": [], "nextBefore": None}

    async def bounded_warnings(*_args, **_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return []

    api.async_get_candles.side_effect = bounded_result
    api.async_get_warnings.side_effect = bounded_warnings
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await asyncio.gather(runtime.candles.async_refresh(), runtime.warnings.async_refresh())
    assert maximum == 4


async def test_empty_holdings_skip_per_symbol_calls(hass) -> None:
    api = client(empty=True)
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await runtime.candles.async_refresh()
    await runtime.warnings.async_refresh()

    assert runtime.candles.data == {}
    assert runtime.warnings.data == {}
    api.async_get_candles.assert_not_awaited()
    api.async_get_warnings.assert_not_awaited()


async def test_warnings_are_parsed_and_failure_preserves_last_good(hass) -> None:
    api = client()
    api.async_get_warnings.return_value = fixture("warnings.json")
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await runtime.warnings.async_refresh()
    good = runtime.warnings.data
    assert good["SNTZ"][0].warning_type == "OVERHEATED"

    api.async_get_warnings.side_effect = TossApiError(None, "temporary")
    await runtime.warnings.async_refresh()
    assert runtime.warnings.data is good
    assert runtime.warnings.last_success is not None
    assert runtime.stale_groups == {"warnings"}
    assert runtime.holdings.last_update_success is True


async def test_optional_groups_are_disabled_without_calls(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.buying_power.async_refresh()
    await runtime.rankings.async_refresh()
    assert runtime.buying_power.data == {}
    assert runtime.rankings.data == {}
    api.async_get_buying_power.assert_not_awaited()
    api.async_get_rankings.assert_not_awaited()


async def test_buying_power_fetches_krw_and_usd_as_decimal(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {"enable_buying_power": True})
    await runtime.buying_power.async_refresh()
    assert runtime.buying_power.data == {
        "KRW": Decimal("5000000"),
        "USD": Decimal("123.45"),
    }
    assert {call.args[1] for call in api.async_get_buying_power.await_args_list} == {"KRW", "USD"}


async def test_market_context_fetches_official_indicators_and_investor_flows(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.market_context.async_refresh()

    assert tuple(runtime.market_context.data.indicators) == KOREAN_MARKET_INDICATORS
    assert runtime.market_context.data.indicators["KOSPI"].last_price == Decimal("1")
    assert set(runtime.market_context.data.investor_trading) == {"KOSPI", "KOSDAQ"}
    record = runtime.market_context.data.investor_trading["KOSPI"][0]
    assert record.foreigner.buy_amount == Decimal("30")
    api.async_get_market_indicators.assert_awaited_once_with(list(KOREAN_MARKET_INDICATORS))
    for call in api.async_get_investor_trading.await_args_list:
        assert call.kwargs == {"interval": "1d", "count": 10}


async def test_rankings_fetch_six_bounded_decimal_safe_snapshots(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {"enable_rankings": True})
    await runtime.rankings.async_refresh()

    expected = {
        (country, kind)
        for country in ("KR", "US")
        for kind in ("MARKET_TRADING_AMOUNT", "TOP_GAINERS", "TOP_LOSERS")
    }
    assert set(runtime.rankings.data) == expected
    assert api.async_get_rankings.await_count == 6
    for call in api.async_get_rankings.await_args_list:
        assert call.kwargs["count"] == 10
        expected_duration = "realtime" if call.kwargs["type"] == "MARKET_TRADING_AMOUNT" else "1d"
        assert call.kwargs["duration"] == expected_duration
    first = runtime.rankings.data[("KR", "MARKET_TRADING_AMOUNT")].items[0]
    assert first.last_price == Decimal("101.5")
    assert first.change_rate == Decimal("0.015")
    assert first.trading_amount == Decimal("5678.90")


async def test_malformed_ranking_preserves_last_good_and_marks_group_stale(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {"enable_rankings": True})
    await runtime.rankings.async_refresh()
    good = runtime.rankings.data

    def malformed(**kwargs):
        payload = client().async_get_rankings.side_effect(**kwargs)
        payload["rankings"][0]["rank"] = "not-an-integer"
        return payload

    api.async_get_rankings.side_effect = malformed
    await runtime.rankings.async_refresh()
    assert runtime.rankings.data is good
    assert runtime.rankings.last_update_success is False
    assert runtime.stale_groups == {"rankings"}


async def test_candle_failure_isolated_and_preserves_essential_data(hass) -> None:
    api = client()
    runtime = create_runtime(hass, api, "account", {})
    await runtime.holdings.async_refresh()
    await runtime.prices.async_refresh()
    api.async_get_candles.side_effect = TossApiError(None, "temporary")
    await runtime.candles.async_refresh()
    assert runtime.holdings.data is not None
    assert runtime.prices.data is not None
    assert runtime.candles.last_update_success is False
    assert runtime.stale_groups == {"candles"}


async def test_runtime_refresh_all_and_shutdown_include_advanced_groups(hass) -> None:
    api = client(empty=True)
    runtime = create_runtime(hass, api, "account", {})
    await runtime.async_refresh_all()
    assert runtime.holdings.last_success is not None
    assert runtime.reference.last_success is not None
    assert runtime.market_context.last_success is not None
    await runtime.async_shutdown()
