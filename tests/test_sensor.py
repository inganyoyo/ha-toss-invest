from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]

from custom_components.toss_invest.const import DOMAIN


def fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures") / name).read_text())


def api() -> AsyncMock:
    client = AsyncMock()
    client.async_get_holdings.return_value = fixture("holdings.json")
    client.async_get_prices.return_value = fixture("prices.json")
    candles = fixture("candles.json")
    assert isinstance(candles, dict)
    client.async_get_candles.return_value = {"candles": candles["candles"], "nextBefore": None}
    client.async_get_warnings.return_value = []
    client.async_get_market_indicators.return_value = []
    client.async_get_investor_trading.return_value = {"records": [], "nextUntil": None}
    client.async_get_buying_power.side_effect = lambda _account, currency: {
        "currency": currency,
        "cashBuyingPower": "123.45",
    }
    market = fixture("market.json")
    assert isinstance(market, dict)
    client.async_get_exchange_rate.return_value = market["exchangeRate"]
    client.async_get_market_calendar.side_effect = lambda country: market[
        "krMarketCalendar" if country == "KR" else "usMarketCalendar"
    ]
    return client


async def setup_integration(
    hass,
    client: AsyncMock,
    options: dict | None = None,
    platforms: list[str] | None = None,
):
    import custom_components

    custom_components.__path__ = [str(Path.cwd() / "custom_components")]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "account_seq": "private-account-sequence",
        },
        options=options or {},
        unique_id="account-hash",
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.toss_invest.TossInvestClient", return_value=client),
        patch(
            "custom_components.toss_invest.PLATFORMS",
            platforms or ["sensor", "binary_sensor"],
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_holding_entities_use_stable_ids_and_percent_units(hass) -> None:
    entry = await setup_integration(hass, api())

    state = hass.states.get("sensor.sanitized_corp_total_return")
    assert state is not None
    assert state.state == "26.25"
    assert state.attributes["unit_of_measurement"] == "%"
    entity = er.async_get(hass).async_get("sensor.sanitized_corp_total_return")
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_TEST_total_return"
    assert "private-account-sequence" not in entity.unique_id


async def test_native_currency_devices_and_disabled_advanced_entities(hass) -> None:
    entry = await setup_integration(hass, api())
    usd_value = hass.states.get("sensor.sanitized_corp_market_value")
    assert usd_value is not None
    assert usd_value.state == "12.625"
    assert usd_value.attributes["device_class"] == "monetary"
    assert usd_value.attributes["unit_of_measurement"] == "USD"

    registry = er.async_get(hass)
    advanced = registry.async_get("sensor.sanitized_corp_one_week_return")
    assert advanced is not None
    assert advanced.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    holding_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:TEST")}
    )
    portfolio_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}:portfolio")}
    )
    assert holding_device is not None and portfolio_device is not None
    assert holding_device.via_device_id == portfolio_device.id


async def test_portfolio_before_and_after_cost_values_are_distinct(hass) -> None:
    await setup_integration(hass, api())
    before = hass.states.get("sensor.toss_invest_portfolio_market_value_krw")
    after = hass.states.get("sensor.toss_invest_portfolio_market_value_after_cost_krw")
    assert before is not None and after is not None
    assert before.state == "520000"
    assert after.state == "519000"


async def test_dynamic_sell_and_rebuy_preserve_entity_identity(hass) -> None:
    client = api()
    entry = await setup_integration(hass, client)
    registry = er.async_get(hass)
    original = registry.async_get("sensor.sanitized_corp_total_return")
    assert original is not None

    sold = fixture("holdings.json")
    assert isinstance(sold, dict)
    sold["items"] = sold["items"][:1]
    entry.runtime_data.holdings.async_set_updated_data(
        __import__(
            "custom_components.toss_invest.models", fromlist=["HoldingsOverview"]
        ).HoldingsOverview.from_api(sold)
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sanitized_corp_total_return").state == "unavailable"

    rebought = fixture("holdings.json")
    assert isinstance(rebought, dict)
    rebought["items"][1]["profitLoss"]["rate"] = "0.50"
    entry.runtime_data.holdings.async_set_updated_data(
        __import__(
            "custom_components.toss_invest.models", fromlist=["HoldingsOverview"]
        ).HoldingsOverview.from_api(rebought)
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sanitized_corp_total_return").state == "50.0"
    restored = registry.async_get("sensor.sanitized_corp_total_return")
    assert restored is not None and restored.id == original.id


async def test_new_holding_is_added_once_and_dependency_staleness_is_isolated(hass) -> None:
    client = api()
    entry = await setup_integration(hass, client)
    changed = fixture("holdings.json")
    assert isinstance(changed, dict)
    changed["items"].append(
        dict(changed["items"][0], symbol="NEW", name="New Holding", currency="KRW")
    )
    overview_type = type(entry.runtime_data.holdings.data)
    entry.runtime_data.holdings.async_set_updated_data(overview_type.from_api(changed))
    await hass.async_block_till_done()
    entry.runtime_data.holdings.async_set_updated_data(overview_type.from_api(changed))
    await hass.async_block_till_done()
    matching = [
        item
        for item in er.async_get(hass).entities.values()
        if item.unique_id == f"{entry.entry_id}_NEW_total_return"
    ]
    assert len(matching) == 1

    entry.runtime_data.stale_groups.add("candles")
    entry.runtime_data.candles.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sanitized_corp_total_return").state == "26.25"
    assert hass.states.get("sensor.sanitized_corp_current_price").state == "10.10"


async def test_diagnostic_and_candle_recorder_metadata(hass) -> None:
    await setup_integration(hass, api())
    registry = er.async_get(hass)
    health = registry.async_get("sensor.toss_invest_portfolio_api_health")
    raw = registry.async_get("sensor.sanitized_corp_daily_candles")
    assert health is not None and health.entity_category == "diagnostic"
    assert raw is not None and raw.entity_category == "diagnostic"

    from custom_components.toss_invest.sensor import TossHoldingSensor

    assert "candles" in TossHoldingSensor._unrecorded_attributes


async def test_data_freshness_is_a_timestamp_diagnostic(hass) -> None:
    await setup_integration(hass, api())
    entity_id = "sensor.toss_invest_portfolio_data_freshness"
    state = hass.states.get(entity_id)
    registry_entry = er.async_get(hass).async_get(entity_id)

    assert state is not None
    assert state.attributes["device_class"] == "timestamp"
    assert "T" in state.state
    assert registry_entry is not None
    assert registry_entry.entity_category == "diagnostic"


async def test_buying_power_entities_are_absent_when_option_is_disabled(hass) -> None:
    await setup_integration(hass, api())
    registry = er.async_get(hass)

    assert registry.async_get("sensor.toss_invest_portfolio_krw_buying_power") is None
    assert registry.async_get("sensor.toss_invest_portfolio_usd_buying_power") is None


async def test_buying_power_entities_are_enabled_and_native_when_option_is_enabled(hass) -> None:
    await setup_integration(hass, api(), {"enable_buying_power": True})
    registry = er.async_get(hass)

    for currency in ("krw", "usd"):
        entity_id = f"sensor.toss_invest_portfolio_{currency}_buying_power"
        registry_entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        assert registry_entry is not None and registry_entry.disabled_by is None
        assert state is not None and state.state == "123.45"
        assert state.attributes["unit_of_measurement"] == currency.upper()


async def test_disabling_buying_power_removes_registry_entries_and_states(hass) -> None:
    client = api()
    entry = await setup_integration(hass, client, {"enable_buying_power": True})
    registry = er.async_get(hass)
    entity_ids = {
        "sensor.toss_invest_portfolio_krw_buying_power",
        "sensor.toss_invest_portfolio_usd_buying_power",
    }
    assert all(registry.async_get(entity_id) is not None for entity_id in entity_ids)
    assert all(hass.states.get(entity_id) is not None for entity_id in entity_ids)

    with (
        patch("custom_components.toss_invest.TossInvestClient", return_value=client),
        patch("custom_components.toss_invest.PLATFORMS", ["sensor", "binary_sensor"]),
    ):
        hass.config_entries.async_update_entry(entry, options={"enable_buying_power": False})
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert all(registry.async_get(entity_id) is None for entity_id in entity_ids)
    assert all(hass.states.get(entity_id) is None for entity_id in entity_ids)
    assert registry.async_get("sensor.toss_invest_portfolio_market_value_krw") is not None
    assert hass.states.get("sensor.toss_invest_portfolio_market_value_krw") is not None


async def test_missing_candle_data_returns_an_empty_diagnostic_payload(hass) -> None:
    entry = await setup_integration(hass, api())
    entry.runtime_data.candles.data = None

    from custom_components.toss_invest.sensor import _candles

    assert _candles(entry.runtime_data, "TEST") == []
