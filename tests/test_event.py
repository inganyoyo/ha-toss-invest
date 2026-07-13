from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry  # type: ignore[import-untyped]

from custom_components.toss_invest.alerts import Alert
from custom_components.toss_invest.event import EVENT_TYPES, TossInvestAlertEvent

from .test_sensor import api, setup_integration


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def make_alert(**changes) -> Alert:
    alert = Alert(
        type="portfolio_daily",
        symbol="portfolio",
        observed=Decimal("123.45"),
        threshold=Decimal("100"),
        severity="warning",
        source_timestamp=NOW,
        monetary=True,
    )
    return replace(alert, **changes)


async def test_event_types_are_fixed_and_invalid_type_is_rejected(hass) -> None:
    assert EVENT_TYPES == [
        "daily_move",
        "total_return",
        "portfolio_daily",
        "near_high",
        "near_low",
        "drawdown",
        "volume_spike",
        "stock_warning",
        "stale_data",
        "api_failure",
    ]
    entry = await setup_integration(hass, api())
    entity = TossInvestAlertEvent(entry.runtime_data, entry.entry_id, {})
    with pytest.raises(ValueError, match="Unsupported alert type"):
        entity.async_emit(make_alert(type="credential_leak"))


async def test_event_payload_masks_money_and_never_contains_private_identifiers(hass) -> None:
    entry = await setup_integration(hass, api())
    entity = TossInvestAlertEvent(entry.runtime_data, entry.entry_id, {})
    entity._trigger_event = Mock()  # type: ignore[method-assign,misc]
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]

    entity.async_emit(make_alert())

    payload = entity._trigger_event.call_args.args[1]
    assert payload == {
        "symbol": "portfolio",
        "severity": "warning",
        "source_timestamp": "2026-07-13T12:00:00+00:00",
    }
    assert "fake-secret" not in str(payload)
    assert "private-account-sequence" not in str(payload)


async def test_event_payload_stringifies_explicitly_allowed_values(hass) -> None:
    entry = await setup_integration(hass, api())
    entity = TossInvestAlertEvent(
        entry.runtime_data, entry.entry_id, {"include_monetary_alert_payloads": True}
    )
    entity._trigger_event = Mock()  # type: ignore[method-assign,misc]
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]

    entity.async_emit(make_alert())

    payload = entity._trigger_event.call_args.args[1]
    assert payload["observed"] == "123.45"
    assert payload["threshold"] == "100"
    assert all(isinstance(value, str) for value in payload.values())


async def test_event_setup_normalizes_percent_options_once_and_avoids_duplicates(hass) -> None:
    entry = await setup_integration(hass, api())
    entity = TossInvestAlertEvent(
        entry.runtime_data,
        entry.entry_id,
        {"daily_move_threshold": 3, "stock_warning_alerts_enabled": True},
    )
    entity.hass = hass
    entity._trigger_event = Mock()  # type: ignore[method-assign,misc]
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]
    await entity.async_added_to_hass()

    entry.runtime_data.holdings.async_update_listeners()
    entry.runtime_data.holdings.async_update_listeners()
    await hass.async_block_till_done()

    daily_events = [
        call for call in entity._trigger_event.call_args_list if call.args[0] == "daily_move"
    ]
    assert len(daily_events) == 1
    assert daily_events[0].args[1]["threshold"] == "0.03"
    entity._trigger_event.reset_mock()
    entry.runtime_data.stale_groups.add("rankings")
    entry.runtime_data.rankings.async_update_listeners()
    await hass.async_block_till_done()
    assert [call.args[0] for call in entity._trigger_event.call_args_list] == ["stale_data"]
    await entity.async_will_remove_from_hass()


async def test_price_coordinator_change_can_trigger_near_high_with_source_time(hass) -> None:
    from dataclasses import replace

    entry = await setup_integration(hass, api())
    entity = TossInvestAlertEvent(entry.runtime_data, entry.entry_id, {"near_high_threshold": 1})
    entity.hass = hass
    entity._trigger_event = Mock()  # type: ignore[method-assign,misc]
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]
    await entity.async_added_to_hass()
    entity._trigger_event.reset_mock()

    quotes = dict(entry.runtime_data.prices.data)
    quotes["TEST"] = replace(
        quotes["TEST"], last_price=Decimal("10.40"), timestamp="2026-07-13T10:00:00+09:00"
    )
    entry.runtime_data.prices.async_set_updated_data(quotes)
    await hass.async_block_till_done()

    near_high = [
        call for call in entity._trigger_event.call_args_list if call.args[0] == "near_high"
    ]
    assert len(near_high) == 1
    assert near_high[0].args[1]["source_timestamp"] == "2026-07-13T10:00:00+09:00"
    await entity.async_will_remove_from_hass()


async def test_control_and_event_platforms_register_and_clean_up(hass) -> None:
    from pathlib import Path
    from unittest.mock import patch

    import custom_components

    from custom_components.toss_invest.const import DOMAIN

    custom_components.__path__ = [str(Path.cwd() / "custom_components")]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "client_id": "fake-client",
            "client_secret": "fake-secret",
            "account_seq": "private-account-sequence",
        },
        options={"enable_manual_refresh": True},
        unique_id="account-hash",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.toss_invest.TossInvestClient", return_value=api()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.toss_invest_portfolio_privacy_mode") is not None
    assert hass.states.get("button.toss_invest_portfolio_refresh") is not None
    assert hass.states.get("event.toss_invest_portfolio_alert") is not None
    assert entry.runtime_data.alerts is not None
    runtime = entry.runtime_data

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert runtime.alerts is None
