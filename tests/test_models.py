import json
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.toss_invest.models import (
    Candle,
    Holding,
    HoldingsOverview,
    MarketSnapshot,
    MoneyByCurrency,
    StockWarning,
    TossDataError,
    parse_decimal,
)

FIXTURES_DIR = Path("tests/fixtures")


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def test_holding_preserves_decimal_and_unknown_market() -> None:
    holding = Holding.from_api(
        {
            "symbol": "TEST",
            "name": "Sanitized Corp",
            "marketCountry": "FUTURE",
            "currency": "USD",
            "quantity": "1.25",
            "lastPrice": "10.10",
            "averagePurchasePrice": "8.00",
            "marketValue": {"purchaseAmount": "10", "amount": "12.625", "amountAfterCost": "12.50"},
            "profitLoss": {
                "amount": "2.625",
                "amountAfterCost": "2.50",
                "rate": "0.2625",
                "rateAfterCost": "0.25",
            },
            "dailyProfitLoss": {"amount": "0.50", "rate": "0.04"},
            "cost": {"commission": "0.125", "tax": None},
        }
    )
    assert holding.quantity == Decimal("1.25")
    assert holding.market_country == "FUTURE"
    assert parse_decimal(None, "optional", optional=True) is None


def test_holding_from_api_parses_every_monetary_field() -> None:
    data = load_fixture("holdings.json")
    holding = Holding.from_api(data["holdings"][0])
    assert holding.symbol == "TEST"
    assert holding.purchase_amount == Decimal("10")
    assert holding.market_value == Decimal("12.625")
    assert holding.market_value_after_cost == Decimal("12.50")
    assert holding.profit_loss_amount == Decimal("2.625")
    assert holding.profit_loss_rate == Decimal("0.2625")
    assert holding.daily_profit_loss_amount == Decimal("0.50")
    assert holding.commission == Decimal("0.125")
    assert holding.tax == Decimal("0.02")


def test_holding_tax_is_optional() -> None:
    data = load_fixture("holdings.json")
    holding = Holding.from_api(data["holdings"][1])
    assert holding.tax is None


def test_parse_decimal_raises_toss_data_error_on_invalid_value() -> None:
    with pytest.raises(TossDataError):
        parse_decimal("not-a-number", "quantity")


def test_money_by_currency_preserves_unknown_currency_codes() -> None:
    money = MoneyByCurrency.from_api({"KRW": "1000", "ZZZ": "5.5"})
    assert money.amounts["KRW"] == Decimal("1000")
    assert money.amounts["ZZZ"] == Decimal("5.5")


def test_holdings_overview_from_fixture_aggregates_holdings_and_totals() -> None:
    overview = HoldingsOverview.from_api(load_fixture("holdings.json"))
    assert len(overview.holdings) == 2
    assert isinstance(overview.holdings[0], Holding)
    assert overview.total_purchase_amount.amounts["KRW"] == Decimal("500000")
    assert overview.total_market_value.amounts["USD"] == Decimal("12.625")


def test_candle_from_fixture_parses_ohlcv() -> None:
    data = load_fixture("candles.json")
    candle = Candle.from_api(data["candles"][0])
    assert candle == Candle(
        date="2026-07-06",
        open=Decimal("9.80"),
        high=Decimal("10.05"),
        low=Decimal("9.75"),
        close=Decimal("10.00"),
        volume=Decimal("12000"),
    )


def test_market_snapshot_from_fixture() -> None:
    snapshot = MarketSnapshot.from_api(load_fixture("market.json"))
    assert snapshot.kr_market_open is True
    assert snapshot.us_market_open is False
    assert snapshot.krw_usd_exchange_rate == Decimal("1350.25")


def test_stock_warning_preserves_unknown_code() -> None:
    data = load_fixture("warnings.json")
    warning = StockWarning.from_api(data["warnings"][1])
    assert warning.symbol == "SNTZ"
    assert warning.code == "FUTURE_WARNING_CODE"
