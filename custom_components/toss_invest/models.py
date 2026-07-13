from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, overload


class TossDataError(ValueError):
    """Raised when a Toss API payload cannot be parsed into a domain model."""


@overload
def parse_decimal(value: Any, field: str, *, optional: Literal[False] = False) -> Decimal: ...
@overload
def parse_decimal(value: Any, field: str, *, optional: Literal[True]) -> Decimal | None: ...


def parse_decimal(value: Any, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise TossDataError(f"Invalid decimal field: {field}") from err


@dataclass(frozen=True, slots=True)
class MoneyByCurrency:
    """Mirrors the OpenAPI `Price` schema: a KRW/USD-converted aggregate amount.

    `krw` is always present (0 when there are no KR holdings). `usd` is null
    when there are no US holdings.
    """

    krw: Decimal
    usd: Decimal | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MoneyByCurrency":
        return cls(
            krw=parse_decimal(data["krw"], "Price.krw"),
            usd=parse_decimal(data.get("usd"), "Price.usd", optional=True),
        )


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    name: str
    market_country: str
    currency: str
    quantity: Decimal
    last_price: Decimal
    average_purchase_price: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    market_value_after_cost: Decimal
    profit_loss_amount: Decimal
    profit_loss_amount_after_cost: Decimal
    profit_loss_rate: Decimal
    profit_loss_rate_after_cost: Decimal
    daily_profit_loss_amount: Decimal
    daily_profit_loss_rate: Decimal
    commission: Decimal
    tax: Decimal | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Holding":
        market_value = data["marketValue"]
        profit_loss = data["profitLoss"]
        daily_profit_loss = data["dailyProfitLoss"]
        cost = data["cost"]
        return cls(
            symbol=str(data["symbol"]),
            name=str(data["name"]),
            market_country=str(data["marketCountry"]),
            currency=str(data["currency"]),
            quantity=parse_decimal(data["quantity"], "quantity"),
            last_price=parse_decimal(data["lastPrice"], "lastPrice"),
            average_purchase_price=parse_decimal(
                data["averagePurchasePrice"], "averagePurchasePrice"
            ),
            purchase_amount=parse_decimal(
                market_value["purchaseAmount"], "marketValue.purchaseAmount"
            ),
            market_value=parse_decimal(market_value["amount"], "marketValue.amount"),
            market_value_after_cost=parse_decimal(
                market_value["amountAfterCost"], "marketValue.amountAfterCost"
            ),
            profit_loss_amount=parse_decimal(profit_loss["amount"], "profitLoss.amount"),
            profit_loss_amount_after_cost=parse_decimal(
                profit_loss["amountAfterCost"], "profitLoss.amountAfterCost"
            ),
            profit_loss_rate=parse_decimal(profit_loss["rate"], "profitLoss.rate"),
            profit_loss_rate_after_cost=parse_decimal(
                profit_loss["rateAfterCost"], "profitLoss.rateAfterCost"
            ),
            daily_profit_loss_amount=parse_decimal(
                daily_profit_loss["amount"], "dailyProfitLoss.amount"
            ),
            daily_profit_loss_rate=parse_decimal(daily_profit_loss["rate"], "dailyProfitLoss.rate"),
            commission=parse_decimal(cost["commission"], "cost.commission"),
            tax=parse_decimal(cost.get("tax"), "cost.tax", optional=True),
        )


@dataclass(frozen=True, slots=True)
class HoldingsOverview:
    """Mirrors the `result` body of `GET /api/v1/holdings` (`HoldingsOverview` schema)."""

    total_purchase_amount: MoneyByCurrency
    market_value_amount: MoneyByCurrency
    market_value_amount_after_cost: MoneyByCurrency
    profit_loss_amount: MoneyByCurrency
    profit_loss_amount_after_cost: MoneyByCurrency
    profit_loss_rate: Decimal
    profit_loss_rate_after_cost: Decimal
    daily_profit_loss_amount: MoneyByCurrency
    daily_profit_loss_rate: Decimal
    items: tuple[Holding, ...]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "HoldingsOverview":
        market_value = data["marketValue"]
        profit_loss = data["profitLoss"]
        daily_profit_loss = data["dailyProfitLoss"]
        return cls(
            total_purchase_amount=MoneyByCurrency.from_api(data["totalPurchaseAmount"]),
            market_value_amount=MoneyByCurrency.from_api(market_value["amount"]),
            market_value_amount_after_cost=MoneyByCurrency.from_api(
                market_value["amountAfterCost"]
            ),
            profit_loss_amount=MoneyByCurrency.from_api(profit_loss["amount"]),
            profit_loss_amount_after_cost=MoneyByCurrency.from_api(profit_loss["amountAfterCost"]),
            profit_loss_rate=parse_decimal(profit_loss["rate"], "profitLoss.rate"),
            profit_loss_rate_after_cost=parse_decimal(
                profit_loss["rateAfterCost"], "profitLoss.rateAfterCost"
            ),
            daily_profit_loss_amount=MoneyByCurrency.from_api(daily_profit_loss["amount"]),
            daily_profit_loss_rate=parse_decimal(daily_profit_loss["rate"], "dailyProfitLoss.rate"),
            items=tuple(Holding.from_api(item) for item in data.get("items", [])),
        )


@dataclass(frozen=True, slots=True)
class Candle:
    """Mirrors the OpenAPI `Candle` schema (one entry of a `CandlePageResponse`)."""

    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Candle":
        return cls(
            timestamp=str(data["timestamp"]),
            open=parse_decimal(data["openPrice"], "candle.openPrice"),
            high=parse_decimal(data["highPrice"], "candle.highPrice"),
            low=parse_decimal(data["lowPrice"], "candle.lowPrice"),
            close=parse_decimal(data["closePrice"], "candle.closePrice"),
            volume=parse_decimal(data["volume"], "candle.volume"),
            currency=str(data["currency"]),
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Combines `ExchangeRateResponse` with the KR/US `MarketCalendarResponse` "today" entries.

    No single Toss endpoint returns this combination; a coordinator assembles it from three
    read-only calls. Each nested payload keeps the exact field names of its source schema so
    `from_api` can be fed real (aggregated) API responses. Market-open is derived the same way
    the calendar schemas define "holiday": KR is closed when `today.integrated` is null, US is
    closed when every one of `today.dayMarket/preMarket/regularMarket/afterMarket` is null.
    """

    kr_market_open: bool
    us_market_open: bool
    krw_usd_rate: Decimal
    krw_usd_rate_valid_from: str
    krw_usd_rate_valid_until: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MarketSnapshot":
        exchange_rate = data["exchangeRate"]
        kr_today = data["krMarketCalendar"]["today"]
        us_today = data["usMarketCalendar"]["today"]
        return cls(
            kr_market_open=kr_today.get("integrated") is not None,
            us_market_open=any(
                us_today.get(session) is not None
                for session in ("dayMarket", "preMarket", "regularMarket", "afterMarket")
            ),
            krw_usd_rate=parse_decimal(exchange_rate["rate"], "exchangeRate.rate"),
            krw_usd_rate_valid_from=str(exchange_rate["validFrom"]),
            krw_usd_rate_valid_until=str(exchange_rate["validUntil"]),
        )


@dataclass(frozen=True, slots=True)
class StockWarning:
    """Mirrors one entry of `GET /api/v1/stocks/{symbol}/warnings` (`StockWarning` schema).

    The API response does not include the symbol (it is a path parameter, not a payload
    field), so callers associate the symbol from request context.
    """

    warning_type: str
    exchange: str | None
    start_date: str | None
    end_date: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "StockWarning":
        exchange = data.get("exchange")
        start_date = data.get("startDate")
        end_date = data.get("endDate")
        return cls(
            warning_type=str(data["warningType"]),
            exchange=str(exchange) if exchange is not None else None,
            start_date=str(start_date) if start_date is not None else None,
            end_date=str(end_date) if end_date is not None else None,
        )
