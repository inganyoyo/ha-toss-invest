from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]

from custom_components.toss_invest import async_setup_entry, async_unload_entry
from custom_components.toss_invest.api import TossApiError, TossAuthError
from custom_components.toss_invest.const import DOMAIN, PLATFORMS
from custom_components.toss_invest.coordinator import TossInvestRuntimeData


def fixture(name: str) -> object:
    return json.loads((Path("tests/fixtures") / name).read_text())


def api() -> AsyncMock:
    client = AsyncMock()
    client.async_get_holdings.return_value = fixture("holdings.json")
    client.async_get_prices.return_value = fixture("prices.json")
    client.async_get_candles.return_value = {"candles": [], "nextBefore": None}
    client.async_get_warnings.return_value = []
    client.async_get_market_indicators.return_value = []
    client.async_get_investor_trading.return_value = {"records": [], "nextUntil": None}
    market = fixture("market.json")
    assert isinstance(market, dict)
    client.async_get_exchange_rate.return_value = market["exchangeRate"]
    client.async_get_market_calendar.side_effect = lambda country: market[
        "krMarketCalendar" if country == "KR" else "usMarketCalendar"
    ]
    return client


def entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "account_seq": "fake-account",
        },
        options=options or {},
        unique_id="hashed",
    )


async def test_setup_first_refreshes_forwards_and_wires_options(hass) -> None:
    config_entry = entry({"request_timeout": 23, "max_retries": 5})
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch("custom_components.toss_invest.TossInvestClient", return_value=client) as factory:
        assert await async_setup_entry(hass, config_entry) is True

    factory.assert_called_once()
    assert factory.call_args.args[1:] == ("fake-client", "fake-secret")
    assert factory.call_args.kwargs == {"timeout": 23.0, "max_retries": 5}
    client.async_get_holdings.assert_awaited_once_with("fake-account")
    client.async_get_prices.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(config_entry, PLATFORMS)
    assert config_entry.runtime_data.holdings.data is not None
    assert config_entry.runtime_data.candles.last_success is not None
    assert config_entry.runtime_data.warnings.last_success is not None
    assert config_entry.runtime_data.market_context.last_success is not None
    assert len(config_entry.update_listeners) == 1

    hass.config_entries.async_reload = AsyncMock(return_value=True)
    await config_entry.update_listeners[0](hass, config_entry)
    hass.config_entries.async_reload.assert_awaited_once_with(config_entry.entry_id)


async def test_setup_transient_first_refresh_raises_not_ready(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    client.async_get_holdings.side_effect = TossApiError(None, "temporary")
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, config_entry)

    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_setup_permanent_auth_failure_requests_reauthentication(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    client.async_get_holdings.side_effect = TossAuthError("invalid-client")
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, config_entry)

    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_setup_advanced_transient_failure_does_not_block_essential_setup(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    client.async_get_candles.side_effect = TossApiError(None, "temporary")
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        assert await async_setup_entry(hass, config_entry) is True

    assert config_entry.runtime_data.holdings.data is not None
    assert config_entry.runtime_data.prices.data is not None
    assert config_entry.runtime_data.candles.last_update_success is False
    assert config_entry.runtime_data.stale_groups == {"candles"}
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()


async def test_setup_advanced_auth_failure_requests_reauthentication(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    client.async_get_warnings.side_effect = TossAuthError("invalid-client")
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, config_entry)

    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_unload_platforms_and_shuts_down_coordinators(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        await async_setup_entry(hass, config_entry)

    shutdown = AsyncMock()
    with patch.object(TossInvestRuntimeData, "async_shutdown", shutdown):
        assert await async_unload_entry(hass, config_entry) is True
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(config_entry, PLATFORMS)
    shutdown.assert_awaited_once_with()


async def test_failed_platform_unload_keeps_runtime_active(hass) -> None:
    config_entry = entry()
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    client = api()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    with patch("custom_components.toss_invest.TossInvestClient", return_value=client):
        await async_setup_entry(hass, config_entry)

    shutdown = AsyncMock()
    with patch.object(TossInvestRuntimeData, "async_shutdown", shutdown):
        assert await async_unload_entry(hass, config_entry) is False
    shutdown.assert_not_awaited()
    assert config_entry.runtime_data.holdings.data is not None
