"""Privacy-safe Toss Invest alert event entity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alerts import ALERT_TYPES, Alert, AlertEvaluator
from .calculations import calculate_drawdown, calculate_volume_ratio
from .coordinator import TossInvestRuntimeData
from .entity import TossInvestEntity

EVENT_TYPES = list(ALERT_TYPES)
ALERT_DESCRIPTION = EventEntityDescription(key="alert", name="Alert")
_NUMERIC_OPTIONS = {
    "daily_move": "daily_move_threshold",
    "total_return": "total_return_threshold",
    "portfolio_daily": "portfolio_daily_threshold",
    "near_high": "near_high_threshold",
    "near_low": "near_low_threshold",
    "drawdown": "drawdown_threshold",
    "volume_spike": "volume_spike_threshold",
}
_BOOLEAN_OPTIONS = {
    "stock_warning": ("stock_warning_alerts_enabled", True),
    "stale_data": ("stale_data_alerts_enabled", True),
    "api_failure": ("api_failure_alerts_enabled", True),
}


def _build_enabled(options: dict[str, Any]) -> dict[str, Decimal | bool]:
    enabled: dict[str, Decimal | bool] = {}
    for alert_type, option in _NUMERIC_OPTIONS.items():
        value = options.get(option)
        if value is not None:
            enabled[alert_type] = Decimal(str(value)) / Decimal(100)
    for alert_type, (option, default) in _BOOLEAN_OPTIONS.items():
        enabled[alert_type] = bool(options.get(option, default))
    return enabled


def _distance(value: Decimal, reference: Decimal) -> Decimal | None:
    if reference == 0:
        return None
    return abs(value - reference) / abs(reference)


def _source_time(value: str, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo is not None else fallback


class TossInvestAlertEvent(TossInvestEntity, EventEntity):
    """One account event stream driven by coordinator changes."""

    entity_description = ALERT_DESCRIPTION
    dependency_groups: tuple[str, ...] = ()
    listener_groups = (
        "holdings",
        "prices",
        "reference",
        "candles",
        "warnings",
        "buying_power",
        "market_context",
        "rankings",
    )
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        options: dict[str, Any],
    ) -> None:
        super().__init__(runtime, entry_id)
        self._include_monetary_payloads = bool(
            options.get("include_monetary_alert_payloads", False)
        )
        self._evaluator = AlertEvaluator(
            cooldown=timedelta(seconds=float(options.get("alert_cooldown", 3600))),
            enabled=_build_enabled(options),
        )
        runtime.alerts = self._evaluator

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._evaluate_coordinators()

    async def async_will_remove_from_hass(self) -> None:
        if self.runtime.alerts is self._evaluator:
            self.runtime.alerts = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._evaluate_coordinators()
        super()._handle_coordinator_update()

    def _evaluate_coordinators(self) -> None:
        overview = self.runtime.holdings.data
        if overview is None:
            return
        now = datetime.now(UTC)
        portfolio_values: dict[str, Decimal | str | bool | None] = {
            "portfolio_daily": overview.daily_profit_loss_rate,
            "stale_data": ",".join(sorted(self.runtime.stale_groups)),
            "api_failure": any(
                not bool(getattr(coordinator, "last_update_success"))
                for coordinator in (
                    self.runtime.holdings,
                    self.runtime.prices,
                    self.runtime.reference,
                    *self.runtime.advanced_coordinators,
                )
            ),
        }
        self._emit_all(
            self._evaluator.evaluate(symbol="portfolio", values=portfolio_values, now=now)
        )
        candles_by_symbol = self.runtime.candles.data or {}
        warnings_by_symbol = self.runtime.warnings.data or {}
        quotes_by_symbol = self.runtime.prices.data or {}
        for holding in overview.items:
            candles = list(candles_by_symbol.get(holding.symbol, ()))
            quote = quotes_by_symbol.get(holding.symbol)
            current_price = quote.last_price if quote is not None else holding.last_price
            high = max((candle.high for candle in candles), default=Decimal(0))
            low = min((candle.low for candle in candles), default=Decimal(0))
            drawdown = calculate_drawdown(high, current_price)
            volume_ratio = calculate_volume_ratio(candles)
            warning_codes = [
                warning.warning_type for warning in warnings_by_symbol.get(holding.symbol, ())
            ]
            values: dict[str, Decimal | str | bool | None] = {
                "daily_move": holding.daily_profit_loss_rate,
                "total_return": holding.profit_loss_rate,
                "near_high": _distance(current_price, high) if high else None,
                "near_low": _distance(current_price, low) if low else None,
                "drawdown": abs(drawdown) if drawdown is not None else None,
                "volume_spike": volume_ratio - 1 if volume_ratio is not None else None,
                "stock_warning": ",".join(warning_codes),
            }
            self._emit_all(
                self._evaluator.evaluate(
                    symbol=holding.symbol,
                    values=values,
                    now=now,
                    source_timestamp=(
                        _source_time(quote.timestamp, now) if quote is not None else now
                    ),
                )
            )

    def _emit_all(self, alerts: list[Alert]) -> None:
        for alert in alerts:
            self.async_emit(alert)

    @callback
    def async_emit(self, alert: Alert) -> None:
        if alert.type not in EVENT_TYPES:
            raise ValueError(f"Unsupported alert type: {alert.type}")
        if alert.source_timestamp.tzinfo is None:
            raise ValueError("source_timestamp must be timezone-aware")
        payload = {
            "symbol": str(alert.symbol),
            "observed": str(alert.observed),
            "threshold": str(alert.threshold),
            "severity": str(alert.severity),
            "source_timestamp": alert.source_timestamp.isoformat(),
        }
        if alert.monetary and not self._include_monetary_payloads:
            payload.pop("observed")
            payload.pop("threshold")
        self._trigger_event(alert.type, payload)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [TossInvestAlertEvent(entry.runtime_data, entry.entry_id, dict(entry.options))]
    )
