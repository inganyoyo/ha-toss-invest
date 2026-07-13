"""Persisted privacy-mode switch."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import TossInvestRuntimeData
from .entity import TossInvestEntity

PRIVACY_DESCRIPTION = SwitchEntityDescription(key="privacy_mode", name="Privacy mode")


class TossInvestPrivacySwitch(TossInvestEntity, SwitchEntity, RestoreEntity):
    """Dashboard privacy preference; source sensors remain unchanged."""

    entity_description = PRIVACY_DESCRIPTION
    dependency_groups: tuple[str, ...] = ()
    _attr_is_on = True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        self._attr_is_on = previous is None or previous.state == STATE_ON
        self.runtime.privacy = self._attr_is_on

    async def async_turn_on(self, **kwargs: object) -> None:
        self._attr_is_on = True
        self.runtime.privacy = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        self._attr_is_on = False
        self.runtime.privacy = False
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TossInvestPrivacySwitch(entry.runtime_data, entry.entry_id)])
