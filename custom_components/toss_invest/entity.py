"""Shared entity support for Toss Invest account and holding devices."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TossCoordinator, TossInvestRuntimeData
from .models import Holding


def remove_registry_entries(
    hass: HomeAssistant,
    platform: Platform,
    unique_ids: Iterable[str],
) -> None:
    """Remove stale option-gated entities by non-secret registry identifiers."""
    registry = er.async_get(hass)
    for unique_id in unique_ids:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id is not None:
            registry.async_remove(entity_id)


class TossInvestEntity(CoordinatorEntity[TossCoordinator[Any]], Entity):
    """Base entity with non-secret registry identifiers and scoped dependencies."""

    _attr_has_entity_name = True
    dependency_groups: tuple[str, ...] = ("holdings",)

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        holding: Holding | None = None,
    ) -> None:
        super().__init__(runtime.holdings)
        self.runtime = runtime
        self.symbol = holding.symbol if holding else None
        key = self.symbol or "portfolio"
        self._attr_unique_id = f"{entry_id}_{key}_{self.entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}:{key}")},
            name="Toss Invest Portfolio" if holding is None else holding.name,
            manufacturer="Toss Securities",
            **({"via_device": (DOMAIN, f"{entry_id}:portfolio")} if holding is not None else {}),
        )

    @property
    def holding(self) -> Holding | None:
        """Return the current holding object, including after a re-buy."""
        if self.symbol is None or self.runtime.holdings.data is None:
            return None
        return next(
            (item for item in self.runtime.holdings.data.items if item.symbol == self.symbol),
            None,
        )

    def _dependency(self, group: str) -> TossCoordinator[Any]:
        return getattr(self.runtime, group)

    def _dependency_has_data(self, group: str) -> bool:
        coordinator = self._dependency(group)
        if coordinator.data is None:
            return False
        if self.symbol is not None and group in {"prices", "candles", "warnings"}:
            return self.symbol in coordinator.data
        return True

    @property
    def available(self) -> bool:
        """Make only entities affected by a failed or missing group unavailable."""
        if self.symbol is not None and self.holding is None:
            return False
        return all(
            group not in self.runtime.stale_groups and self._dependency_has_data(group)
            for group in self.dependency_groups
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        for group in getattr(self, "listener_groups", self.dependency_groups):
            coordinator = self._dependency(group)
            if coordinator is not self.coordinator:
                self.async_on_remove(
                    coordinator.async_add_listener(self._handle_coordinator_update)
                )
