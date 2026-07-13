"""Holding warning binary sensors for Toss Invest."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TossInvestRuntimeData
from .entity import TossInvestEntity
from .models import Holding

WARNING_DESCRIPTION = BinarySensorEntityDescription(key="warning", name="Warning")


class TossWarningBinarySensor(TossInvestEntity, BinarySensorEntity):
    """Whether Toss reports any warning, including future warning codes."""

    entity_description = WARNING_DESCRIPTION
    dependency_groups = ("holdings", "warnings")

    def __init__(self, runtime: TossInvestRuntimeData, entry_id: str, holding: Holding) -> None:
        super().__init__(runtime, entry_id, holding)

    @property
    def is_on(self) -> bool | None:
        if self.symbol is None:
            return None
        return bool(self.runtime.warnings.data.get(self.symbol, ()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        warnings = self.runtime.warnings.data.get(self.symbol, ()) if self.symbol else ()
        return {
            "warning_codes": [warning.warning_type for warning in warnings],
            "warnings": [
                {
                    "code": warning.warning_type,
                    "exchange": warning.exchange,
                    "start_date": warning.start_date,
                    "end_date": warning.end_date,
                }
                for warning in warnings
            ],
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up and dynamically reconcile holding warning entities."""
    runtime = entry.runtime_data
    known_symbols: set[str] = set()

    def reconcile_holdings() -> None:
        if runtime.holdings.data is None:
            return
        additions = [
            holding
            for holding in runtime.holdings.data.items
            if holding.symbol not in known_symbols
        ]
        if not additions:
            return
        known_symbols.update(holding.symbol for holding in additions)
        async_add_entities(
            TossWarningBinarySensor(runtime, entry.entry_id, holding) for holding in additions
        )

    reconcile_holdings()
    entry.async_on_unload(runtime.holdings.async_add_listener(reconcile_holdings))
