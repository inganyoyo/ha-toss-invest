"""Decimal-safe, stateful alert evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

ALERT_TYPES = (
    "daily_move",
    "total_return",
    "portfolio_daily",
    "near_high",
    "near_low",
    "drawdown",
    "volume_spike",
    "stock_warning",
    "stale_data",
    "api_failure",
)


@dataclass(frozen=True, slots=True)
class Alert:
    """A privacy-neutral alert before event-payload policy is applied."""

    type: str
    symbol: str
    observed: Decimal | str | bool
    threshold: Decimal | str | bool
    severity: str
    source_timestamp: datetime
    monetary: bool = False


@dataclass(slots=True)
class _ConditionState:
    active: bool = False
    last_emitted: datetime | None = None


class AlertEvaluator:
    """Emit on transitions and, while active, only after the repeat cooldown."""

    def __init__(
        self,
        *,
        cooldown: timedelta,
        enabled: dict[str, Decimal | bool],
    ) -> None:
        if cooldown < timedelta(0):
            raise ValueError("cooldown cannot be negative")
        unknown = set(enabled) - set(ALERT_TYPES)
        if unknown:
            raise ValueError(f"Unsupported alert types: {sorted(unknown)}")
        self.cooldown = cooldown
        self.enabled = dict(enabled)
        self._states: dict[tuple[str, str], _ConditionState] = {}

    @staticmethod
    def _is_active(alert_type: str, observed: Any, threshold: Decimal | bool) -> bool:
        if isinstance(threshold, bool):
            return threshold and bool(observed)
        if not isinstance(observed, Decimal):
            return False
        if alert_type == "daily_move":
            return abs(observed) >= abs(threshold)
        if alert_type in {"near_high", "near_low"}:
            return observed <= threshold
        if alert_type == "drawdown":
            return abs(observed) >= abs(threshold)
        if alert_type in {"total_return", "portfolio_daily"} and threshold < 0:
            return observed <= threshold
        return observed >= threshold

    def evaluate(
        self,
        *,
        symbol: str,
        values: dict[str, Decimal | str | bool | None],
        now: datetime,
        source_timestamp: datetime | None = None,
    ) -> list[Alert]:
        """Evaluate one symbol snapshot at a timezone-aware instant."""
        timestamp = source_timestamp or now
        if now.tzinfo is None or timestamp.tzinfo is None:
            raise ValueError("alert timestamps must be timezone-aware")
        emitted: list[Alert] = []
        for alert_type in ALERT_TYPES:
            if alert_type not in self.enabled or alert_type not in values:
                continue
            observed = values[alert_type]
            threshold = self.enabled[alert_type]
            active = observed is not None and self._is_active(alert_type, observed, threshold)
            key = (symbol, alert_type)
            state = self._states.setdefault(key, _ConditionState())
            if not active:
                state.active = False
                continue
            repeat_due = state.last_emitted is None or now - state.last_emitted >= self.cooldown
            if not state.active or repeat_due:
                assert observed is not None
                emitted.append(
                    Alert(
                        type=alert_type,
                        symbol=symbol,
                        observed=observed,
                        threshold=threshold,
                        severity="warning",
                        source_timestamp=timestamp,
                    )
                )
                state.last_emitted = now
            state.active = True
        return emitted
