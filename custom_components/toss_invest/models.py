from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
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
    """Monetary amounts keyed by currency code, e.g. native KRW and USD totals."""

    amounts: Mapping[str, Decimal]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MoneyByCurrency":
        return cls(
            amounts=MappingProxyType(
                {
                    str(currency): parse_decimal(amount, f"MoneyByCurrency.{currency}")
                    for currency, amount in data.items()
                }
            )
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
    holdings: tuple[Holding, ...]
    total_purchase_amount: MoneyByCurrency
    total_market_value: MoneyByCurrency

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "HoldingsOverview":
        return cls(
            holdings=tuple(Holding.from_api(item) for item in data.get("holdings", [])),
            total_purchase_amount=MoneyByCurrency.from_api(data.get("totalPurchaseAmount", {})),
            total_market_value=MoneyByCurrency.from_api(data.get("totalMarketValue", {})),
        )


@dataclass(frozen=True, slots=True)
class Candle:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Candle":
        return cls(
            date=str(data["date"]),
            open=parse_decimal(data["open"], "candle.open"),
            high=parse_decimal(data["high"], "candle.high"),
            low=parse_decimal(data["low"], "candle.low"),
            close=parse_decimal(data["close"], "candle.close"),
            volume=parse_decimal(data["volume"], "candle.volume"),
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    kr_market_open: bool
    us_market_open: bool
    krw_usd_exchange_rate: Decimal
    as_of: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MarketSnapshot":
        return cls(
            kr_market_open=bool(data["krMarketOpen"]),
            us_market_open=bool(data["usMarketOpen"]),
            krw_usd_exchange_rate=parse_decimal(data["krwUsdExchangeRate"], "krwUsdExchangeRate"),
            as_of=str(data["asOf"]),
        )


@dataclass(frozen=True, slots=True)
class StockWarning:
    symbol: str
    code: str
    message: str
    issued_at: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "StockWarning":
        return cls(
            symbol=str(data["symbol"]),
            code=str(data["code"]),
            message=str(data.get("message", "")),
            issued_at=str(data["issuedAt"]),
        )
