from decimal import Decimal
from custom_components.toss_invest.models import Holding, Candle
from custom_components.toss_invest.calculations import (
    calculate_allocation,
    calculate_concentration,
    calculate_period_return,
    calculate_drawdown,
    calculate_volatility,
    calculate_volume_ratio,
    # compatibility aliases
    allocation,
    concentration,
    period_return,
    drawdown,
    volatility,
    volume_ratio,
)


def make_mock_holding(
    symbol: str, market_value: Decimal, currency: str, market_country: str = "US"
) -> Holding:
    return Holding(
        symbol=symbol,
        name=f"Mock {symbol}",
        market_country=market_country,
        currency=currency,
        quantity=Decimal("1"),
        last_price=market_value,
        average_purchase_price=market_value,
        purchase_amount=market_value,
        market_value=market_value,
        market_value_after_cost=market_value,
        profit_loss_amount=Decimal("0"),
        profit_loss_amount_after_cost=Decimal("0"),
        profit_loss_rate=Decimal("0"),
        profit_loss_rate_after_cost=Decimal("0"),
        daily_profit_loss_amount=Decimal("0"),
        daily_profit_loss_rate=Decimal("0"),
        commission=Decimal("0"),
        tax=None,
    )


def make_mock_candle(
    timestamp: str, close: Decimal, volume: Decimal, currency: str = "USD"
) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        currency=currency,
    )


def test_concentration_and_return_are_decimal() -> None:
    # Verify the specific example from Task 3 brief
    weights = [Decimal("60"), Decimal("25"), Decimal("15")]
    assert concentration(weights, 1) == Decimal("0.6")
    assert concentration(weights, 3) == Decimal("1")
    assert period_return(Decimal("100"), Decimal("125")) == Decimal("0.25")


def test_calculate_period_return() -> None:
    assert calculate_period_return(Decimal("100"), Decimal("125")) == Decimal("0.25")
    assert calculate_period_return(Decimal("0"), Decimal("125")) is None
    assert calculate_period_return(Decimal("100"), Decimal("0")) == Decimal("-1")


def test_calculate_drawdown() -> None:
    assert calculate_drawdown(Decimal("100"), Decimal("90")) == Decimal("-0.1")
    assert calculate_drawdown(Decimal("0"), Decimal("90")) is None


def test_calculate_concentration_with_decimals() -> None:
    vals = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")]
    assert calculate_concentration(vals, 1) == Decimal("0.4")
    assert calculate_concentration(vals, 2) == Decimal("0.7")
    assert calculate_concentration(vals, 5) == Decimal("1.0")
    assert calculate_concentration([], 2) == Decimal("0")
    assert calculate_concentration([Decimal("0"), Decimal("0")], 2) == Decimal("0")


def test_calculate_concentration_with_holdings() -> None:
    holdings = [
        make_mock_holding("AAPL", Decimal("100"), "USD"),
        make_mock_holding("TSLA", Decimal("200"), "USD"),
    ]
    # At 1300 exchange rate, AAPL=130000, TSLA=260000. Total=390000.
    # Top 1 concentration should be 260000/390000 = 2/3
    res = calculate_concentration(holdings, 1, Decimal("1300"))
    assert abs(res - Decimal("2") / Decimal("3")) < Decimal("1e-9")


def test_calculate_allocation_empty_and_zero() -> None:
    assert calculate_allocation([], Decimal("1300")) == {}
    h1 = make_mock_holding("AAPL", Decimal("0"), "USD")
    h2 = make_mock_holding("GOOG", Decimal("0"), "USD")
    assert calculate_allocation([h1, h2], Decimal("1300")) == {
        "AAPL": Decimal("0"),
        "GOOG": Decimal("0"),
    }


def test_calculate_allocation_mixed_currencies() -> None:
    holdings = [
        make_mock_holding("AAPL", Decimal("10"), "USD", "US"),
        make_mock_holding("005930", Decimal("13000"), "KRW", "KR"),
    ]
    # At 1300 KRW/USD, AAPL value in KRW is 13000. 005930 is 13000.
    # Total is 26000. Each allocation is 0.5.
    res_symbol = calculate_allocation(holdings, Decimal("1300"), group_by="symbol")
    assert res_symbol == {"AAPL": Decimal("0.5"), "005930": Decimal("0.5")}

    res_currency = calculate_allocation(holdings, Decimal("1300"), group_by="currency")
    assert res_currency == {"USD": Decimal("0.5"), "KRW": Decimal("0.5")}

    res_country = calculate_allocation(holdings, Decimal("1300"), group_by="market_country")
    assert res_country == {"US": Decimal("0.5"), "KR": Decimal("0.5")}


def test_calculate_volatility() -> None:
    # Less than 2 returns (needs at least 3 closes or 2 valid return points)
    assert calculate_volatility([]) is None
    assert calculate_volatility([Decimal("100")]) is None
    assert calculate_volatility([Decimal("100"), Decimal("105")]) is None

    # Valid closes
    closes = [Decimal("100"), Decimal("105"), Decimal("102"), Decimal("108")]
    vol = calculate_volatility(closes)
    assert vol is not None
    assert vol > Decimal("0")

    # With candles
    candles = [
        make_mock_candle("2026-07-01", Decimal("100"), Decimal("1000")),
        make_mock_candle("2026-07-02", Decimal("105"), Decimal("1200")),
        make_mock_candle("2026-07-03", Decimal("102"), Decimal("1100")),
        make_mock_candle("2026-07-04", Decimal("108"), Decimal("1300")),
    ]
    vol_c = calculate_volatility(candles)
    assert vol_c == vol


def test_calculate_volume_ratio() -> None:
    # Empty
    assert calculate_volume_ratio([]) is None
    # 1 candle
    assert calculate_volume_ratio([Decimal("100")]) is None
    # Window <= 0
    assert calculate_volume_ratio([Decimal("100"), Decimal("120")], window=0) is None

    # Preceding average is 0
    assert calculate_volume_ratio([Decimal("0"), Decimal("100")], window=1) is None

    # Valid
    vols = [Decimal("100"), Decimal("200"), Decimal("150")]
    # window=2: trailing average is average([100, 200]) = 150. Current = 150. Ratio = 1.0.
    assert calculate_volume_ratio(vols, window=2) == Decimal("1.0")

    # With candles
    candles = [
        make_mock_candle("2026-07-01", Decimal("10"), Decimal("100")),
        make_mock_candle("2026-07-02", Decimal("10"), Decimal("200")),
        make_mock_candle("2026-07-03", Decimal("10"), Decimal("150")),
    ]
    assert calculate_volume_ratio(candles, window=2) == Decimal("1.0")


def test_compatibility_aliases() -> None:
    # Check that compatibility aliases reference the correct calculations
    assert allocation([], Decimal("1300")) == {}
    assert concentration([Decimal("60"), Decimal("25"), Decimal("15")], 1) == Decimal("0.6")
    assert period_return(Decimal("100"), Decimal("125")) == Decimal("0.25")
    assert drawdown(Decimal("100"), Decimal("90")) == Decimal("-0.1")
    assert volatility([]) is None
    assert volume_ratio([]) is None
