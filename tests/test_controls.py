import asyncio
from unittest.mock import AsyncMock, Mock

from homeassistant.const import STATE_OFF
from homeassistant.core import State

from custom_components.toss_invest.button import TossInvestRefreshButton
from custom_components.toss_invest.switch import TossInvestPrivacySwitch

from .test_sensor import api, setup_integration


async def test_privacy_defaults_on_and_updates_only_runtime(hass) -> None:
    entry = await setup_integration(hass, api())
    entity = TossInvestPrivacySwitch(entry.runtime_data, entry.entry_id)
    entity.hass = hass
    entity.async_get_last_state = AsyncMock(return_value=None)  # type: ignore[method-assign]
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]

    await entity.async_added_to_hass()
    assert entity.is_on is True
    assert entry.runtime_data.privacy is True
    holdings_before = entry.runtime_data.holdings.data

    await entity.async_turn_off()
    assert entry.runtime_data.privacy is False
    assert entry.runtime_data.holdings.data is holdings_before


async def test_privacy_restores_previous_state(hass) -> None:
    entry = await setup_integration(hass, api())
    entity = TossInvestPrivacySwitch(entry.runtime_data, entry.entry_id)
    entity.hass = hass
    entity.async_get_last_state = AsyncMock(  # type: ignore[method-assign]
        return_value=State("switch.privacy", STATE_OFF)
    )
    entity.async_write_ha_state = Mock()  # type: ignore[method-assign,misc]

    await entity.async_added_to_hass()
    assert entity.is_on is False
    assert entry.runtime_data.privacy is False
    await entity.async_turn_on()
    assert entity.is_on is True
    assert entry.runtime_data.privacy is True


async def test_manual_refresh_is_optionally_registered(hass) -> None:
    client = api()
    disabled = await setup_integration(hass, client, {"enable_manual_refresh": False})
    assert disabled.options["enable_manual_refresh"] is False

    from custom_components.toss_invest.button import build_refresh_entities

    assert build_refresh_entities(disabled) == []
    enabled = await setup_integration(hass, api(), {"enable_manual_refresh": True})
    assert len(build_refresh_entities(enabled)) == 1


async def test_manual_refresh_coalesces_overlap_and_obeys_ten_second_cooldown(
    hass, monkeypatch
) -> None:
    entry = await setup_integration(hass, api())
    now = [100.0]
    started = asyncio.Event()
    finish = asyncio.Event()

    async def refresh() -> None:
        started.set()
        await finish.wait()

    refresh_all = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(type(entry.runtime_data), "async_refresh_all", refresh_all)
    entity = TossInvestRefreshButton(entry.runtime_data, entry.entry_id, monotonic=lambda: now[0])

    first = asyncio.create_task(entity.async_press())
    await started.wait()
    second = asyncio.create_task(entity.async_press())
    await asyncio.sleep(0)
    finish.set()
    await asyncio.gather(first, second)
    await entity.async_press()
    assert refresh_all.await_count == 1

    now[0] += 10
    finish.clear()
    finish.set()
    await entity.async_press()
    assert refresh_all.await_count == 2
