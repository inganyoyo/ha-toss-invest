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
    holding = Holding.from_api(data["items"][1])
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
    holding = Holding.from_api(data["items"][0])
    assert holding.tax is None


def test_parse_decimal_raises_toss_data_error_on_invalid_value() -> None:
    with pytest.raises(TossDataError):
        parse_decimal("not-a-number", "quantity")


def test_money_by_currency_uses_lowercase_krw_usd_fields() -> None:
    money = MoneyByCurrency.from_api({"krw": "1000", "usd": "5.5"})
    assert money.krw == Decimal("1000")
    assert money.usd == Decimal("5.5")


def test_money_by_currency_usd_is_optional_when_no_us_holdings() -> None:
    money = MoneyByCurrency.from_api({"krw": "1000"})
    assert money.usd is None


def test_holdings_overview_parses_items_and_overview_totals() -> None:
    overview = HoldingsOverview.from_api(load_fixture("holdings.json"))
    assert len(overview.items) == 2
    assert isinstance(overview.items[0], Holding)
    assert overview.total_purchase_amount.krw == Decimal("500000")
    assert overview.total_purchase_amount.usd == Decimal("10")
    assert overview.market_value_amount.krw == Decimal("520000")
    assert overview.market_value_amount_after_cost.usd == Decimal("12.50")
    assert overview.profit_loss_amount.krw == Decimal("20000")
    assert overview.profit_loss_amount_after_cost.usd == Decimal("2.50")
    assert overview.profit_loss_rate == Decimal("0.0742")
    assert overview.profit_loss_rate_after_cost == Decimal("0.0703")
    assert overview.daily_profit_loss_amount.krw == Decimal("5000")
    assert overview.daily_profit_loss_rate == Decimal("0.0098")


def test_candle_from_fixture_parses_open_high_low_close_volume_currency() -> None:
    data = load_fixture("candles.json")
    candle = Candle.from_api(data["candles"][-1])
    assert candle == Candle(
        timestamp="2026-07-06T09:00:00+09:00",
        open=Decimal("9.80"),
        high=Decimal("10.05"),
        low=Decimal("9.75"),
        close=Decimal("10.00"),
        volume=Decimal("12000"),
        currency="USD",
    )


def test_market_snapshot_from_fixture_derives_open_state_from_calendar() -> None:
    snapshot = MarketSnapshot.from_api(load_fixture("market.json"))
    assert snapshot.kr_market_open is True
    assert snapshot.us_market_open is False
    assert snapshot.krw_usd_rate == Decimal("1350.25")
    assert snapshot.krw_usd_rate_valid_from == "2026-07-13T09:30:00+09:00"


def test_stock_warning_parses_type_exchange_and_dates_without_symbol() -> None:
    data = load_fixture("warnings.json")
    warning = StockWarning.from_api(data[0])
    assert warning.warning_type == "OVERHEATED"
    assert warning.exchange == "KRX"
    assert warning.start_date == "2026-07-10"
    assert warning.end_date == "2026-07-17"


def test_stock_warning_preserves_unknown_warning_type_and_nullable_fields() -> None:
    data = load_fixture("warnings.json")
    warning = StockWarning.from_api(data[1])
    assert warning.warning_type == "FUTURE_WARNING_CODE"
    assert warning.exchange is None
    assert warning.end_date is None


def test_prices_fixture_matches_price_response_array_shape() -> None:
    data = load_fixture("prices.json")
    assert isinstance(data, list)
    for item in data:
        assert {"symbol", "lastPrice", "currency"} <= item.keys()
        assert parse_decimal(item["lastPrice"], "lastPrice") > Decimal("0")


def test_accounts_fixture_uses_account_no_and_valid_account_type() -> None:
    data = load_fixture("accounts.json")
    assert isinstance(data, list)
    for account in data:
        assert "accountNo" in account
        assert account["accountType"] == "BROKERAGE"
        assert "accountSeq" not in account
