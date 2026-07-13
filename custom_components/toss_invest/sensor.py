"""Account and holding sensors for Toss Invest."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calculations import (
    calculate_allocation,
    calculate_concentration,
    calculate_drawdown,
    calculate_period_return,
    calculate_volatility,
    calculate_volume_ratio,
)
from .coordinator import TossInvestRuntimeData
from .entity import TossInvestEntity
from .models import Candle, Holding, HoldingsOverview

type Value = Decimal | str | datetime | int | None
type HoldingValueFn = Callable[[TossInvestRuntimeData, Holding], Value]
type AccountValueFn = Callable[[TossInvestRuntimeData, HoldingsOverview], Value]


def _description(
    key: str,
    name: str,
    *,
    monetary: bool = False,
    percentage: bool = False,
    enabled: bool = True,
    diagnostic: bool = False,
) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        name=name,
        device_class=SensorDeviceClass.MONETARY if monetary else None,
        native_unit_of_measurement=PERCENTAGE if percentage else None,
        entity_registry_enabled_default=enabled,
        entity_category=EntityCategory.DIAGNOSTIC if diagnostic else None,
    )


HOLDING_DESCRIPTIONS = (
    _description("current_price", "Current price", monetary=True),
    _description("average_purchase_price", "Average purchase price", monetary=True),
    _description("quantity", "Quantity"),
    _description("purchase_amount", "Purchase amount", monetary=True),
    _description("market_value", "Market value", monetary=True),
    _description("market_value_after_cost", "Market value after cost", monetary=True),
    _description("profit_loss", "Profit loss", monetary=True),
    _description("profit_loss_after_cost", "Profit loss after cost", monetary=True),
    _description("total_return", "Total return", percentage=True),
    _description("total_return_after_cost", "Total return after cost", percentage=True),
    _description("daily_profit_loss", "Daily profit loss", monetary=True),
    _description("daily_return", "Daily return", percentage=True),
    _description("one_week_return", "One week return", percentage=True, enabled=False),
    _description("one_month_return", "One month return", percentage=True, enabled=False),
    _description("three_month_return", "Three month return", percentage=True, enabled=False),
    _description("six_month_return", "Six month return", percentage=True, enabled=False),
    _description("one_year_return", "One year return", percentage=True, enabled=False),
    _description("period_high", "Period high", monetary=True, enabled=False),
    _description("period_low", "Period low", monetary=True, enabled=False),
    _description("drawdown", "Drawdown", percentage=True, enabled=False),
    _description("historical_volatility", "Historical volatility", percentage=True, enabled=False),
    _description("volume_change", "Volume change", percentage=True, enabled=False),
    _description("daily_candles", "Daily candles", enabled=False, diagnostic=True),
)

ACCOUNT_DESCRIPTIONS = (
    *(
        _description(f"{key}_{currency.lower()}", f"{name} {currency}", monetary=True)
        for key, name in (
            ("total_purchase_amount", "Total purchase amount"),
            ("market_value", "Market value"),
            ("market_value_after_cost", "Market value after cost"),
            ("profit_loss", "Profit loss"),
            ("profit_loss_after_cost", "Profit loss after cost"),
            ("daily_profit_loss", "Daily profit loss"),
        )
        for currency in ("KRW", "USD")
    ),
    _description("total_return", "Total return", percentage=True),
    _description("total_return_after_cost", "Total return after cost", percentage=True),
    _description("daily_return", "Daily return", percentage=True),
    _description("allocation_kr", "KR allocation", percentage=True),
    _description("allocation_us", "US allocation", percentage=True),
    _description("allocation_krw", "KRW allocation", percentage=True),
    _description("allocation_usd", "USD allocation", percentage=True),
    _description("top_one_concentration", "Top one concentration", percentage=True),
    _description("top_three_concentration", "Top three concentration", percentage=True),
    _description("buying_power_krw", "KRW buying power", monetary=True),
    _description("buying_power_usd", "USD buying power", monetary=True),
    _description("krw_usd_exchange_rate", "KRW USD exchange rate"),
    _description("kr_market_status", "KR market status"),
    _description("us_market_status", "US market status"),
    _description("data_freshness", "Data freshness", diagnostic=True),
    _description("api_health", "API health", diagnostic=True),
)

_HOLDING_DIRECT: dict[str, str] = {
    "average_purchase_price": "average_purchase_price",
    "quantity": "quantity",
    "purchase_amount": "purchase_amount",
    "market_value": "market_value",
    "market_value_after_cost": "market_value_after_cost",
    "profit_loss": "profit_loss_amount",
    "profit_loss_after_cost": "profit_loss_amount_after_cost",
    "total_return": "profit_loss_rate",
    "total_return_after_cost": "profit_loss_rate_after_cost",
    "daily_profit_loss": "daily_profit_loss_amount",
    "daily_return": "daily_profit_loss_rate",
}
_PERIODS = {
    "one_week_return": 5,
    "one_month_return": 21,
    "three_month_return": 63,
    "six_month_return": 126,
    "one_year_return": 252,
}


def _candles(runtime: TossInvestRuntimeData, symbol: str) -> list[Candle]:
    return sorted(runtime.candles.data.get(symbol, ()), key=lambda candle: candle.timestamp)


def _period_return(runtime: TossInvestRuntimeData, symbol: str, window: int) -> Decimal | None:
    candles = _candles(runtime, symbol)[-window:]
    if len(candles) < 2:
        return None
    return calculate_period_return(candles[0].close, candles[-1].close)


def _as_percentage(value: Decimal | None) -> Decimal | None:
    """Convert API ratios at the entity boundary without binary floating point."""
    if value is None:
        return None
    percentage = value * Decimal(100)
    return (
        percentage.quantize(Decimal("0.1"))
        if percentage == percentage.to_integral()
        else percentage.normalize()
    )


class TossHoldingSensor(TossInvestEntity, SensorEntity):
    """A sensor attached to one holding device."""

    _unrecorded_attributes = frozenset({"candles"})
    entity_description: SensorEntityDescription

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        holding: Holding,
        description: SensorEntityDescription,
    ) -> None:
        self.entity_description = description
        if description.key == "current_price":
            self.dependency_groups = ("holdings", "prices")
        elif description.key in _PERIODS or description.key in {
            "period_high",
            "period_low",
            "drawdown",
            "historical_volatility",
            "volume_change",
            "daily_candles",
        }:
            self.dependency_groups = ("holdings", "candles")
        super().__init__(runtime, entry_id, holding)
        if description.device_class is SensorDeviceClass.MONETARY:
            self._attr_native_unit_of_measurement = holding.currency

    @property
    def native_value(self) -> Value:
        holding = self.holding
        if holding is None:
            return None
        key = self.entity_description.key
        if key == "current_price":
            quote = self.runtime.prices.data.get(holding.symbol)
            return quote.last_price if quote else None
        if key in _HOLDING_DIRECT:
            value = getattr(holding, _HOLDING_DIRECT[key])
            return (
                _as_percentage(value)
                if self.entity_description.native_unit_of_measurement == PERCENTAGE
                else value
            )
        candles = _candles(self.runtime, holding.symbol)
        if key in _PERIODS:
            value = _period_return(self.runtime, holding.symbol, _PERIODS[key])
        elif key == "period_high":
            value = max((item.high for item in candles), default=None)
        elif key == "period_low":
            value = min((item.low for item in candles), default=None)
        elif key == "drawdown":
            value = calculate_drawdown(
                max((item.high for item in candles), default=Decimal(0)), holding.last_price
            )
        elif key == "historical_volatility":
            value = calculate_volatility(candles)
        elif key == "volume_change":
            ratio = calculate_volume_ratio(candles)
            value = ratio - Decimal(1) if ratio is not None else None
        else:
            return len(candles)
        return (
            _as_percentage(value)
            if self.entity_description.native_unit_of_measurement == PERCENTAGE
            else value
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "daily_candles" or self.symbol is None:
            return None
        return {
            "candles": [
                {
                    "timestamp": item.timestamp,
                    "open": str(item.open),
                    "high": str(item.high),
                    "low": str(item.low),
                    "close": str(item.close),
                    "volume": str(item.volume),
                    "currency": item.currency,
                }
                for item in _candles(self.runtime, self.symbol)
            ]
        }


_ACCOUNT_MONEY = {
    "total_purchase_amount": "total_purchase_amount",
    "market_value": "market_value_amount",
    "market_value_after_cost": "market_value_amount_after_cost",
    "profit_loss": "profit_loss_amount",
    "profit_loss_after_cost": "profit_loss_amount_after_cost",
    "daily_profit_loss": "daily_profit_loss_amount",
}


class TossAccountSensor(TossInvestEntity, SensorEntity):
    """A sensor attached to the portfolio device."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        description: SensorEntityDescription,
    ) -> None:
        self.entity_description = description
        key = description.key
        if key.startswith(("allocation_", "top_")):
            self.dependency_groups = ("holdings", "reference")
        elif key.startswith("buying_power_"):
            self.dependency_groups = ("buying_power",)
        elif key in {"krw_usd_exchange_rate", "kr_market_status", "us_market_status"}:
            self.dependency_groups = ("reference",)
        elif key in {"data_freshness", "api_health"}:
            self.dependency_groups = ()
            if key == "api_health":
                self.listener_groups = (
                    "prices",
                    "reference",
                    "candles",
                    "warnings",
                    "buying_power",
                    "market_context",
                    "rankings",
                )
        super().__init__(runtime, entry_id)
        if description.device_class is SensorDeviceClass.MONETARY:
            self._attr_native_unit_of_measurement = key.rsplit("_", 1)[-1].upper()

    @property
    def native_value(self) -> Value:
        overview = self.runtime.holdings.data
        if overview is None:
            return None
        key = self.entity_description.key
        currency = key.rsplit("_", 1)[-1].upper()
        for prefix, attribute in _ACCOUNT_MONEY.items():
            if key == f"{prefix}_{currency.lower()}":
                return getattr(getattr(overview, attribute), currency.lower())
        if key == "total_return":
            return _as_percentage(overview.profit_loss_rate)
        if key == "total_return_after_cost":
            return _as_percentage(overview.profit_loss_rate_after_cost)
        if key == "daily_return":
            return _as_percentage(overview.daily_profit_loss_rate)
        reference = self.runtime.reference.data
        if key.startswith("allocation_") and reference is not None:
            group: Literal["market_country", "currency"] = (
                "market_country" if key in {"allocation_kr", "allocation_us"} else "currency"
            )
            allocations = calculate_allocation(list(overview.items), reference.krw_usd_rate, group)
            return _as_percentage(allocations.get(key.rsplit("_", 1)[-1].upper(), Decimal(0)))
        if key.startswith("top_") and reference is not None:
            count = 1 if key == "top_one_concentration" else 3
            return _as_percentage(
                calculate_concentration(list(overview.items), count, reference.krw_usd_rate)
            )
        if key.startswith("buying_power_"):
            return self.runtime.buying_power.data.get(currency)
        if key == "krw_usd_exchange_rate":
            return reference.krw_usd_rate if reference else None
        if key == "kr_market_status":
            return "open" if reference and reference.kr_market_open else "closed"
        if key == "us_market_status":
            return "open" if reference and reference.us_market_open else "closed"
        if key == "data_freshness":
            return self.runtime.holdings.last_success
        if key == "api_health":
            return "stale" if self.runtime.stale_groups else "ok"
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up account sensors and reconcile newly discovered holdings."""
    runtime = entry.runtime_data
    async_add_entities(
        TossAccountSensor(runtime, entry.entry_id, description)
        for description in ACCOUNT_DESCRIPTIONS
    )
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
            TossHoldingSensor(runtime, entry.entry_id, holding, description)
            for holding in additions
            for description in HOLDING_DESCRIPTIONS
        )

    reconcile_holdings()
    entry.async_on_unload(runtime.holdings.async_add_listener(reconcile_holdings))
