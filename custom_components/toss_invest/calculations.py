from decimal import Decimal
from statistics import stdev
from typing import Literal

from custom_components.toss_invest.models import Holding, Candle


def calculate_allocation(
    holdings: list[Holding],
    krw_usd_rate: Decimal,
    group_by: Literal["symbol", "currency", "market_country"] = "symbol",
) -> dict[str, Decimal]:
    """Calculate the portfolio allocation by symbol, currency, or country."""
    if group_by not in ("symbol", "currency", "market_country"):
        raise ValueError(f"Invalid group_by: {group_by}")

    if not holdings:
        return {}

    values: dict[str, Decimal] = {}
    total_val = Decimal("0")

    for holding in holdings:
        if group_by == "symbol":
            key = holding.symbol
        elif group_by == "currency":
            key = holding.currency
        else:
            key = holding.market_country

        if holding.currency.upper() == "USD":
            krw_val = holding.market_value * krw_usd_rate
        else:
            krw_val = holding.market_value

        values[key] = values.get(key, Decimal("0")) + krw_val
        total_val += krw_val

    if total_val == Decimal("0"):
        return {k: Decimal("0") for k in values}

    return {k: v / total_val for k, v in values.items()}


def calculate_concentration(
    values: list[Decimal] | list[Holding],
    count: int,
    krw_usd_rate: Decimal | None = None,
) -> Decimal:
    """Calculate portfolio concentration of top elements."""
    if not values or count <= 0:
        return Decimal("0")

    has_holding = any(isinstance(val, Holding) for val in values)
    if has_holding and krw_usd_rate is None:
        raise ValueError("krw_usd_rate is required when passing Holding objects")

    decimal_values: list[Decimal] = []
    for val in values:
        if isinstance(val, Holding):
            assert krw_usd_rate is not None
            if val.currency.upper() == "USD":
                decimal_values.append(val.market_value * krw_usd_rate)
            else:
                decimal_values.append(val.market_value)
        else:
            decimal_values.append(val)

    total = sum(decimal_values, Decimal("0"))
    if total == Decimal("0"):
        return Decimal("0")

    top_values = sorted(decimal_values, reverse=True)[:count]
    return sum(top_values, Decimal("0")) / total


def calculate_period_return(start: Decimal, end: Decimal) -> Decimal | None:
    """Calculate the return rate over a period."""
    if start == Decimal("0"):
        return None
    return (end / start) - Decimal("1")


def calculate_drawdown(high: Decimal, current: Decimal) -> Decimal | None:
    """Calculate the drawdown from the highest value."""
    if high == Decimal("0"):
        return None
    return (current / high) - Decimal("1")


def calculate_volatility(closes: list[Decimal] | list[Candle]) -> Decimal | None:
    """Calculate historical volatility based on daily returns."""
    if not closes:
        return None

    decimal_closes: list[Decimal] = []
    if closes and isinstance(closes[0], Candle):
        candles = [c for c in closes if isinstance(c, Candle)]
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        decimal_closes = [c.close for c in sorted_candles]
    else:
        decimal_closes = [Decimal(str(c)) for c in closes]

    returns = [
        decimal_closes[i] / decimal_closes[i - 1] - Decimal("1")
        for i in range(1, len(decimal_closes))
        if decimal_closes[i - 1] != Decimal("0")
    ]
    if len(returns) < 2:
        return None

    return stdev(returns) * Decimal("252").sqrt()


def calculate_volume_ratio(
    volumes: list[Decimal] | list[Candle],
    window: int = 20,
) -> Decimal | None:
    """Calculate volume change relative to trailing average."""
    if not volumes or window <= 0:
        return None

    decimal_volumes: list[Decimal] = []
    if volumes and isinstance(volumes[0], Candle):
        candles = [c for c in volumes if isinstance(c, Candle)]
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        decimal_volumes = [c.volume for c in sorted_candles]
    else:
        decimal_volumes = [Decimal(str(v)) for v in volumes]

    if len(decimal_volumes) < 2:
        return None

    current_volume = decimal_volumes[-1]
    trailing_volumes = decimal_volumes[-(window + 1) : -1]

    if not trailing_volumes:
        return None

    avg_trailing = sum(trailing_volumes, Decimal("0")) / Decimal(len(trailing_volumes))
    if avg_trailing == Decimal("0"):
        return None

    return current_volume / avg_trailing


# Compatibility Aliases
allocation = calculate_allocation
concentration = calculate_concentration
period_return = calculate_period_return
drawdown = calculate_drawdown
volatility = calculate_volatility
volume_ratio = calculate_volume_ratio
