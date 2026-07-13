"""Public market-context sensors for Toss Invest."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, Platform
from homeassistant.core import HomeAssistant

from .coordinator import (
    InvestorTradingRecord,
    KOREAN_MARKET_INDICATORS,
    RankingSnapshot,
    TossInvestRuntimeData,
)
from .entity import TossInvestEntity, remove_registry_entries


@dataclass(frozen=True, kw_only=True)
class MarketIndicatorEntityDescription(SensorEntityDescription):
    """Describe an official Korean market indicator."""

    symbol: str


@dataclass(frozen=True, kw_only=True)
class InvestorNetEntityDescription(SensorEntityDescription):
    """Describe one market and investor-class net amount."""

    market: str
    investor: str


@dataclass(frozen=True, kw_only=True)
class RankingEntityDescription(SensorEntityDescription):
    """Describe one country and ranking-type snapshot."""

    country: str
    ranking_type: str


MARKET_INDICATOR_DESCRIPTIONS = tuple(
    MarketIndicatorEntityDescription(
        key=f"market_indicator_{symbol.lower()}",
        name=f"Market indicator {symbol}",
        symbol=symbol,
        native_unit_of_measurement="points" if symbol in {"KOSPI", "KOSDAQ"} else PERCENTAGE,
        entity_registry_enabled_default=symbol in {"KOSPI", "KOSDAQ"},
    )
    for symbol in KOREAN_MARKET_INDICATORS
)

_INVESTORS = ("individual", "foreigner", "institution", "other_corporation")
INVESTOR_NET_DESCRIPTIONS = tuple(
    InvestorNetEntityDescription(
        key=f"{market.lower()}_{investor}_net",
        name=f"{market} {investor.replace('_', ' ')} net",
        market=market,
        investor=investor,
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="KRW",
    )
    for market in ("KOSPI", "KOSDAQ")
    for investor in _INVESTORS
)

_RANKING_TYPES = (
    ("MARKET_TRADING_AMOUNT", "market_trading_amount"),
    ("TOP_GAINERS", "top_gainers"),
    ("TOP_LOSERS", "top_losers"),
)
RANKING_DESCRIPTIONS = tuple(
    RankingEntityDescription(
        key=f"{country.lower()}_{key_suffix}",
        name=f"{country} {key_suffix.replace('_', ' ')}",
        country=country,
        ranking_type=ranking_type,
        entity_registry_enabled_default=False,
    )
    for country in ("KR", "US")
    for ranking_type, key_suffix in _RANKING_TYPES
)


class TossMarketIndicatorSensor(TossInvestEntity, SensorEntity):
    """Represent an official Korean market indicator."""

    dependency_groups = ("market_context",)
    entity_description: MarketIndicatorEntityDescription

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        description: MarketIndicatorEntityDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(runtime, entry_id)

    @property
    def native_value(self) -> Decimal | None:
        snapshot = self.runtime.market_context.data
        if snapshot is None:
            return None
        indicator = snapshot.indicators.get(self.entity_description.symbol)
        return indicator.last_price if indicator is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None] | None:
        snapshot = self.runtime.market_context.data
        if snapshot is None:
            return None
        indicator = snapshot.indicators.get(self.entity_description.symbol)
        if indicator is None:
            return None
        return {"symbol": indicator.symbol, "timestamp": indicator.timestamp}


class TossInvestorNetSensor(TossInvestEntity, SensorEntity):
    """Represent the latest investor-class net buy-sell amount."""

    dependency_groups = ("market_context",)
    entity_description: InvestorNetEntityDescription

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        description: InvestorNetEntityDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(runtime, entry_id)

    def _latest_record(self) -> InvestorTradingRecord | None:
        snapshot = self.runtime.market_context.data
        if snapshot is None:
            return None
        records = snapshot.investor_trading.get(self.entity_description.market, ())
        return records[0] if records else None

    @property
    def native_value(self) -> Decimal | None:
        record = self._latest_record()
        if record is None:
            return None
        amounts = getattr(record, self.entity_description.investor)
        return amounts.buy_amount - amounts.sell_amount

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        record = self._latest_record()
        if record is None:
            return None
        return {"date": record.date, "updated_at": record.updated_at}


class TossRankingSensor(TossInvestEntity, SensorEntity):
    """Represent a bounded public market ranking snapshot."""

    dependency_groups = ("rankings",)
    _unrecorded_attributes = frozenset({"rankings"})
    entity_description: RankingEntityDescription

    def __init__(
        self,
        runtime: TossInvestRuntimeData,
        entry_id: str,
        description: RankingEntityDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(runtime, entry_id)

    def _snapshot(self) -> RankingSnapshot | None:
        data = self.runtime.rankings.data
        if data is None:
            return None
        return data.get((self.entity_description.country, self.entity_description.ranking_type))

    @property
    def native_value(self) -> str | None:
        snapshot = self._snapshot()
        return snapshot.items[0].symbol if snapshot is not None and snapshot.items else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snapshot = self._snapshot()
        if snapshot is None:
            return None
        return {
            "ranked_at": snapshot.ranked_at,
            "rankings": [
                {
                    "rank": item.rank,
                    "symbol": item.symbol,
                    "currency": item.currency,
                    "last_price": str(item.last_price),
                    "base_price": str(item.base_price),
                    "change_rate": (str(item.change_rate) if item.change_rate is not None else ""),
                    "trading_volume": str(item.trading_volume),
                    "trading_amount": str(item.trading_amount),
                }
                for item in snapshot.items[:10]
            ],
        }


def build_market_entities(
    entry: ConfigEntry[TossInvestRuntimeData],
) -> list[SensorEntity]:
    """Build always-present market context and option-gated ranking entities."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        TossMarketIndicatorSensor(runtime, entry.entry_id, description)
        for description in MARKET_INDICATOR_DESCRIPTIONS
    ]
    entities.extend(
        TossInvestorNetSensor(runtime, entry.entry_id, description)
        for description in INVESTOR_NET_DESCRIPTIONS
    )
    if entry.options.get("enable_rankings", False):
        entities.extend(
            TossRankingSensor(runtime, entry.entry_id, description)
            for description in RANKING_DESCRIPTIONS
        )
    return entities


def remove_ranking_registry_entries(
    hass: HomeAssistant,
    entry: ConfigEntry[TossInvestRuntimeData],
) -> None:
    """Remove option-gated ranking registry entries when rankings are disabled."""
    if entry.options.get("enable_rankings", False):
        return
    remove_registry_entries(
        hass,
        Platform.SENSOR,
        (f"{entry.entry_id}_portfolio_{description.key}" for description in RANKING_DESCRIPTIONS),
    )
