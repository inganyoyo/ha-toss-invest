from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import PERCENTAGE
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.toss_invest.const import DOMAIN
from custom_components.toss_invest.coordinator import KOREAN_MARKET_INDICATORS
from custom_components.toss_invest.market_entities import (
    MARKET_INDICATOR_DESCRIPTIONS,
    TossInvestorNetSensor,
    TossMarketIndicatorSensor,
    TossRankingSensor,
    build_market_entities,
)

from .test_sensor import api, setup_integration


def market_api() -> AsyncMock:
    client = api()
    client.async_get_market_indicators.return_value = [
        {
            "symbol": symbol,
            "timestamp": "2026-07-13T09:00:00+09:00",
            "lastPrice": str(index + 100),
        }
        for index, symbol in enumerate(KOREAN_MARKET_INDICATORS)
    ]
    records = {
        "records": [
            {
                "date": "2026-07-13",
                "updatedAt": "2026-07-13T15:30:00+09:00",
                "individual": {"buyAmount": "150", "sellAmount": "40"},
                "foreigner": {"buyAmount": "20", "sellAmount": "70"},
                "institution": {"buyAmount": "90", "sellAmount": "30"},
                "otherCorporation": {"buyAmount": "10", "sellAmount": "3"},
            },
            {
                "date": "2026-07-12",
                "updatedAt": "2026-07-12T15:30:00+09:00",
                "individual": {"buyAmount": "999", "sellAmount": "0"},
                "foreigner": {"buyAmount": "0", "sellAmount": "0"},
                "institution": {"buyAmount": "0", "sellAmount": "0"},
                "otherCorporation": {"buyAmount": "0", "sellAmount": "0"},
            },
        ],
        "nextUntil": None,
    }
    client.async_get_investor_trading.side_effect = lambda _symbol, **_kwargs: records

    def candles_for(symbol: str, **_kwargs: object) -> dict[str, object]:
        closes = {"KOSPI": ("100", "102"), "KOSDAQ": ("100", "99")}.get(symbol, ("100", "100"))
        return {
            "candles": [
                {
                    "timestamp": "2026-07-13T00:00:00.000+09:00",
                    "openPrice": closes[0],
                    "highPrice": closes[0],
                    "lowPrice": closes[0],
                    "closePrice": closes[0],
                    "volume": "1000",
                },
                {
                    "timestamp": "2026-07-14T00:00:00.000+09:00",
                    "openPrice": closes[1],
                    "highPrice": closes[1],
                    "lowPrice": closes[1],
                    "closePrice": closes[1],
                    "volume": "1000",
                },
            ],
            "nextBefore": None,
        }

    client.async_get_market_indicator_candles.side_effect = candles_for
    return client


async def test_market_indicators_have_stable_values_defaults_and_device(hass) -> None:
    entry = await setup_integration(hass, market_api())
    registry = er.async_get(hass)

    kospi = hass.states.get("sensor.toss_invest_portfolio_market_indicator_kospi")
    kosdaq = hass.states.get("sensor.toss_invest_portfolio_market_indicator_kosdaq")
    assert kospi is not None and kospi.state == "100"
    assert kosdaq is not None and kosdaq.state == "101"
    assert kospi.attributes["unit_of_measurement"] == "points"
    assert kosdaq.attributes["unit_of_measurement"] == "points"
    units = {
        description.symbol: description.native_unit_of_measurement
        for description in MARKET_INDICATOR_DESCRIPTIONS
    }
    assert all(units[symbol] == PERCENTAGE for symbol in KOREAN_MARKET_INDICATORS[2:])

    for index, symbol in enumerate(KOREAN_MARKET_INDICATORS):
        entity_id = f"sensor.toss_invest_portfolio_market_indicator_{symbol.lower()}"
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.unique_id == (
            f"{entry.entry_id}_portfolio_market_indicator_{symbol.lower()}"
        )
        if index < 2:
            assert registry_entry.disabled_by is None
        else:
            assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    portfolio = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:portfolio")}
    )
    assert portfolio is not None
    indicator_entry = registry.async_get("sensor.toss_invest_portfolio_market_indicator_kospi")
    assert indicator_entry is not None and indicator_entry.device_id == portfolio.id


async def test_market_indicators_expose_daily_return_from_candles(hass) -> None:
    await setup_integration(hass, market_api())

    kospi = hass.states.get("sensor.toss_invest_portfolio_market_indicator_kospi")
    kosdaq = hass.states.get("sensor.toss_invest_portfolio_market_indicator_kosdaq")

    assert kospi is not None and kosdaq is not None
    assert Decimal(kospi.attributes["daily_return"]) == Decimal("0.02")
    assert Decimal(kosdaq.attributes["daily_return"]) == Decimal("-0.01")


async def test_investor_net_uses_latest_record_decimal_and_krw_metadata(hass) -> None:
    entry = await setup_integration(hass, market_api())
    registry = er.async_get(hass)
    portfolio = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:portfolio")}
    )
    assert portfolio is not None

    expected = {
        "individual": "110",
        "foreigner": "-50",
        "institution": "60",
        "other_corporation": "7",
    }
    for market in ("kospi", "kosdaq"):
        for investor, value in expected.items():
            state = hass.states.get(f"sensor.toss_invest_portfolio_{market}_{investor}_net")
            assert state is not None and state.state == value
            assert state.attributes["device_class"] == "monetary"
            assert state.attributes["unit_of_measurement"] == "KRW"
            registry_entry = registry.async_get(
                f"sensor.toss_invest_portfolio_{market}_{investor}_net"
            )
            assert registry_entry is not None and registry_entry.device_id == portfolio.id


async def test_market_entities_are_unavailable_only_for_market_context_staleness(hass) -> None:
    entry = await setup_integration(hass, market_api())
    entity_id = "sensor.toss_invest_portfolio_kospi_individual_net"

    entry.runtime_data.stale_groups.add("market_context")
    entry.runtime_data.market_context.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert hass.states.get("sensor.toss_invest_portfolio_total_return").state != "unavailable"

    entry.runtime_data.stale_groups.clear()
    entry.runtime_data.market_context.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "110"


async def test_empty_and_unknown_market_data_report_unknown_without_crashing(hass) -> None:
    client = market_api()
    client.async_get_market_indicators.return_value = [
        {"symbol": "UNEXPECTED", "timestamp": None, "lastPrice": "1"}
    ]
    client.async_get_investor_trading.return_value = {"records": [], "nextUntil": None}
    client.async_get_investor_trading.side_effect = None
    await setup_integration(hass, client)

    assert hass.states.get("sensor.toss_invest_portfolio_market_indicator_kospi").state == "unknown"
    assert hass.states.get("sensor.toss_invest_portfolio_kospi_individual_net").state == "unknown"


async def test_ranking_entities_are_absent_when_option_is_disabled(hass) -> None:
    entry = await setup_integration(hass, market_api())

    entities = build_market_entities(entry)
    assert (
        len([entity for entity in entities if isinstance(entity, TossMarketIndicatorSensor)]) == 8
    )
    assert len([entity for entity in entities if isinstance(entity, TossInvestorNetSensor)]) == 8
    assert not any(isinstance(entity, TossRankingSensor) for entity in entities)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.toss_invest_portfolio_kr_top_gainers") is None


async def test_rankings_are_option_gated_disabled_and_json_safe(hass) -> None:
    client = market_api()
    client.async_get_rankings.side_effect = lambda **kwargs: {
        "rankedAt": "2026-07-13T15:30:00+09:00",
        "rankings": [
            {
                "rank": rank,
                "symbol": f"{kwargs['market_country']}{rank}",
                "currency": "KRW" if kwargs["market_country"] == "KR" else "USD",
                "price": {
                    "lastPrice": f"{100 + rank}.25",
                    "basePrice": "100",
                    "changeRate": "0.0125",
                },
                "tradingVolume": "1234.5",
                "tradingAmount": "987654.25",
            }
            for rank in range(1, 12)
        ],
    }
    entry = await setup_integration(hass, client, {"enable_rankings": True})
    registry = er.async_get(hass)
    expected_ids = {
        "sensor.toss_invest_portfolio_kr_market_trading_amount",
        "sensor.toss_invest_portfolio_kr_top_gainers",
        "sensor.toss_invest_portfolio_kr_top_losers",
        "sensor.toss_invest_portfolio_us_market_trading_amount",
        "sensor.toss_invest_portfolio_us_top_gainers",
        "sensor.toss_invest_portfolio_us_top_losers",
    }
    entities = [
        entity for entity in build_market_entities(entry) if isinstance(entity, TossRankingSensor)
    ]
    assert len(entities) == 6
    assert {entity.entity_id for entity in entities} == {None}

    for entity_id in expected_ids:
        registry_entry = registry.async_get(entity_id)
        assert registry_entry is not None
        assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    ranking = next(
        entity for entity in entities if entity.entity_description.key == "kr_top_gainers"
    )
    assert ranking.native_value == "KR1"
    assert ranking.extra_state_attributes == {
        "ranked_at": "2026-07-13T15:30:00+09:00",
        "rankings": [
            {
                "rank": rank,
                "symbol": f"KR{rank}",
                "currency": "KRW",
                "last_price": f"{100 + rank}.25",
                "base_price": "100",
                "change_rate": "0.0125",
                "trading_volume": "1234.5",
                "trading_amount": "987654.25",
            }
            for rank in range(1, 11)
        ],
    }
    assert "rankings" in ranking._unrecorded_attributes
    assert all(
        isinstance(value, (str, int))
        for row in ranking.extra_state_attributes["rankings"]
        for value in row.values()
    )
    entry.runtime_data.stale_groups.add("rankings")
    assert ranking.available is False
    assert (
        next(
            entity
            for entity in build_market_entities(entry)
            if isinstance(entity, TossMarketIndicatorSensor)
        ).available
        is True
    )


async def test_empty_ranking_has_unknown_state_and_ranked_at(hass) -> None:
    client = market_api()
    client.async_get_rankings.return_value = {
        "rankedAt": None,
        "rankings": [],
    }
    entry = await setup_integration(hass, client, {"enable_rankings": True})
    ranking = next(
        entity for entity in build_market_entities(entry) if isinstance(entity, TossRankingSensor)
    )

    assert ranking.native_value is None
    assert ranking.extra_state_attributes == {"ranked_at": None, "rankings": []}


async def test_ranking_option_reload_registered_state_staleness_and_unload(hass) -> None:
    client = market_api()
    client.async_get_rankings.side_effect = lambda **kwargs: {
        "rankedAt": "2026-07-13T15:30:00+09:00",
        "rankings": [
            {
                "rank": 1,
                "symbol": f"{kwargs['market_country']}-LEADER",
                "currency": "KRW" if kwargs["market_country"] == "KR" else "USD",
                "price": {
                    "lastPrice": "101.25",
                    "basePrice": "100",
                    "changeRate": "0.0125",
                },
                "tradingVolume": "1234.5",
                "tradingAmount": "987654.25",
            }
        ],
    }
    entry = await setup_integration(hass, client)
    entity_id = "sensor.toss_invest_portfolio_kr_top_gainers"
    registry = er.async_get(hass)
    assert registry.async_get(entity_id) is None

    with (
        patch("custom_components.toss_invest.TossInvestClient", return_value=client),
        patch("custom_components.toss_invest.PLATFORMS", ["sensor", "binary_sensor"]),
    ):
        hass.config_entries.async_update_entry(entry, options={"enable_rankings": True})
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry_entry = registry.async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None

    registry.async_update_entity(entity_id, disabled_by=None)
    with (
        patch("custom_components.toss_invest.TossInvestClient", return_value=client),
        patch("custom_components.toss_invest.PLATFORMS", ["sensor", "binary_sensor"]),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None and state.state == "KR-LEADER"
    assert state.attributes["ranked_at"] == "2026-07-13T15:30:00+09:00"
    assert state.attributes["rankings"][0]["last_price"] == "101.25"
    portfolio = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:portfolio")}
    )
    assert portfolio is not None
    ranking_entry = registry.async_get(entity_id)
    assert ranking_entry is not None and ranking_entry.device_id == portfolio.id

    entry.runtime_data.stale_groups.add("rankings")
    entry.runtime_data.rankings.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert hass.states.get("sensor.toss_invest_portfolio_market_indicator_kospi").state == "100"

    entry.runtime_data.stale_groups.remove("rankings")
    entry.runtime_data.rankings.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "KR-LEADER"

    with (
        patch("custom_components.toss_invest.TossInvestClient", return_value=client),
        patch("custom_components.toss_invest.PLATFORMS", ["sensor", "binary_sensor"]),
    ):
        hass.config_entries.async_update_entry(entry, options={"enable_rankings": False})
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id) is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert registry.async_get(entity_id) is None
