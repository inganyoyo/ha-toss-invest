from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.toss_invest.alerts import AlertEvaluator


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def test_alert_evaluator_suppresses_repeat_inside_cooldown() -> None:
    evaluator = AlertEvaluator(cooldown=timedelta(hours=1), enabled={"daily_move": Decimal("0.05")})

    first = evaluator.evaluate(symbol="TEST", values={"daily_move": Decimal("0.06")}, now=NOW)
    second = evaluator.evaluate(
        symbol="TEST",
        values={"daily_move": Decimal("0.07")},
        now=NOW + timedelta(minutes=30),
    )

    assert [item.type for item in first] == ["daily_move"]
    assert second == []


def test_alert_evaluator_repeats_after_cooldown_and_retriggers_after_recovery() -> None:
    evaluator = AlertEvaluator(cooldown=timedelta(hours=1), enabled={"daily_move": Decimal("0.05")})
    evaluator.evaluate(symbol="TEST", values={"daily_move": Decimal("0.06")}, now=NOW)

    repeated = evaluator.evaluate(
        symbol="TEST", values={"daily_move": Decimal("0.07")}, now=NOW + timedelta(hours=1)
    )
    recovered = evaluator.evaluate(
        symbol="TEST",
        values={"daily_move": Decimal("0.01")},
        now=NOW + timedelta(hours=1, minutes=1),
    )
    retriggered = evaluator.evaluate(
        symbol="TEST",
        values={"daily_move": Decimal("-0.08")},
        now=NOW + timedelta(hours=1, minutes=2),
    )

    assert [item.type for item in repeated] == ["daily_move"]
    assert recovered == []
    assert [item.type for item in retriggered] == ["daily_move"]


def test_alert_state_is_isolated_by_symbol_and_type() -> None:
    evaluator = AlertEvaluator(
        cooldown=timedelta(hours=1),
        enabled={"daily_move": Decimal("0.05"), "stock_warning": True},
    )

    first = evaluator.evaluate(
        symbol="ONE",
        values={"daily_move": Decimal("0.06"), "stock_warning": "UNKNOWN_CODE"},
        now=NOW,
    )
    other = evaluator.evaluate(symbol="TWO", values={"daily_move": Decimal("0.06")}, now=NOW)

    assert [item.type for item in first] == ["daily_move", "stock_warning"]
    assert [item.type for item in other] == ["daily_move"]
    assert first[1].observed == "UNKNOWN_CODE"


def test_alert_evaluator_rejects_naive_time() -> None:
    evaluator = AlertEvaluator(cooldown=timedelta(hours=1), enabled={"stale_data": True})

    with pytest.raises(ValueError, match="timezone-aware"):
        evaluator.evaluate(
            symbol="portfolio",
            values={"stale_data": True},
            now=datetime(2026, 7, 13),
        )
