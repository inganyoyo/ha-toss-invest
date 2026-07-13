"""Manual refresh button."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TossInvestRuntimeData
from .entity import TossInvestEntity, remove_registry_entries

REFRESH_DESCRIPTION = ButtonEntityDescription(key="refresh", name="Refresh")
_COOLDOWN_SECONDS = 10.0


class TossInvestRefreshButton(TossInvestEntity, ButtonEntity):
    """Coalesced refresh-all control with a deterministic cooldown."""

    entity_description = REFRESH_DESCRIPTION
    dependency_groups: tuple[str, ...] = ()

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(runtime, entry_id)
        self._monotonic = monotonic
        self._last_started: float | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    async def async_press(self) -> None:
        task = self._refresh_task
        if task is not None and not task.done():
            await task
            return
        now = self._monotonic()
        if self._last_started is not None and now - self._last_started < _COOLDOWN_SECONDS:
            return
        self._last_started = now
        task = self.hass.async_create_background_task(
            self.runtime.async_refresh_all(), "toss_invest_manual_refresh"
        )
        self._refresh_task = task
        try:
            await task
        finally:
            if self._refresh_task is task:
                self._refresh_task = None

    async def async_will_remove_from_hass(self) -> None:
        task = self._refresh_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._refresh_task is task:
            self._refresh_task = None
        await super().async_will_remove_from_hass()


def build_refresh_entities(
    entry: ConfigEntry[TossInvestRuntimeData],
) -> list[TossInvestRefreshButton]:
    """Build the optional button without registering disabled entities."""
    if not bool(entry.options.get("enable_manual_refresh", True)):
        return []
    return [TossInvestRefreshButton(entry.runtime_data, entry.entry_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not bool(entry.options.get("enable_manual_refresh", True)):
        remove_registry_entries(
            hass,
            Platform.BUTTON,
            (f"{entry.entry_id}_portfolio_{REFRESH_DESCRIPTION.key}",),
        )
    async_add_entities(build_refresh_entities(entry))
