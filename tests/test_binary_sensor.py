from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.toss_invest.coordinator import WarningCoordinator
from custom_components.toss_invest.models import StockWarning

from .test_sensor import api, setup_integration


async def test_warning_sensor_preserves_unknown_codes_and_dates(hass) -> None:
    client = api()
    client.async_get_warnings.return_value = [
        {
            "warningType": "FUTURE_WARNING_CODE",
            "exchange": None,
            "startDate": "2026-07-11",
            "endDate": None,
        }
    ]
    entry = await setup_integration(hass, client)
    state = hass.states.get("binary_sensor.sanitized_corp_warning")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["warning_codes"] == ["FUTURE_WARNING_CODE"]
    assert state.attributes["warnings"] == [
        {
            "code": "FUTURE_WARNING_CODE",
            "exchange": None,
            "start_date": "2026-07-11",
            "end_date": None,
        }
    ]
    registry = er.async_get(hass).async_get("binary_sensor.sanitized_corp_warning")
    assert registry is not None
    assert registry.unique_id == f"{entry.entry_id}_TEST_warning"


async def test_warning_availability_tracks_only_warning_dependency(hass) -> None:
    entry = await setup_integration(hass, api())
    entity_id = "binary_sensor.sanitized_corp_warning"
    assert hass.states.get(entity_id).state == "off"

    entry.runtime_data.stale_groups.add("warnings")
    entry.runtime_data.warnings.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert hass.states.get("sensor.sanitized_corp_total_return").state == "26.25"
    assert hass.states.get("sensor.toss_invest_portfolio_api_health").state == "stale"

    entry.runtime_data.stale_groups.discard("warnings")
    warning = StockWarning("UNKNOWN_CODE", "NASDAQ", None, "2026-12-31")
    assert isinstance(entry.runtime_data.warnings, WarningCoordinator)
    entry.runtime_data.warnings.async_set_updated_data({"TEST": (warning,), "SNTZ": ()})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
