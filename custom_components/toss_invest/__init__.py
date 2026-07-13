from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TossInvestClient
from .const import PLATFORMS
from .coordinator import TossCoordinator, TossInvestRuntimeData, create_runtime

type TossInvestConfigEntry = ConfigEntry[TossInvestRuntimeData]

BRANDING_ICON_PATH = Path(__file__).parent / "branding" / "icon.png"
BRANDING_ICON_URL = "/toss_invest_static/icon.png"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if hass.http is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(BRANDING_ICON_URL, str(BRANDING_ICON_PATH), True)]
        )
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: TossInvestConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: TossInvestConfigEntry) -> bool:
    options = entry.options
    client = TossInvestClient(
        async_get_clientsession(hass),
        str(entry.data["client_id"]),
        str(entry.data["client_secret"]),
        timeout=float(options.get("request_timeout", 10)),
        max_retries=int(options.get("max_retries", 3)),
    )
    runtime = create_runtime(
        hass,
        client,
        str(entry.data["account_seq"]),
        options,
        entry,
    )
    entry.runtime_data = runtime

    await runtime.holdings.async_config_entry_first_refresh()
    await runtime.reference.async_config_entry_first_refresh()
    await runtime.prices.async_config_entry_first_refresh()

    async def refresh_nonessential(coordinator: TossCoordinator[Any]) -> None:
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            pass

    await asyncio.gather(
        *(refresh_nonessential(coordinator) for coordinator in runtime.advanced_coordinators)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TossInvestConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True
