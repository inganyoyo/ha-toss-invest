"""Independent runtime coordinators for Toss Invest."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TossApiError, TossAuthError, TossInvestClient, TossRateLimitError
from .models import (
    Candle,
    HoldingsOverview,
    MarketSnapshot,
    StockWarning,
    TossDataError,
    parse_decimal,
)

_LOGGER = logging.getLogger(__name__)
_DataT = TypeVar("_DataT")

KOREAN_MARKET_INDICATORS = (
    "KOSPI",
    "KOSDAQ",
    "KR_BOND_2Y",
    "KR_BOND_3Y",
    "KR_BOND_5Y",
    "KR_BOND_10Y",
    "KR_BOND_20Y",
    "KR_BOND_30Y",
)
_ADVANCED_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class MarketIndicator:
    symbol: str
    timestamp: str | None
    last_price: Decimal

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> MarketIndicator:
        timestamp = data.get("timestamp")
        return cls(
            symbol=str(data["symbol"]),
            timestamp=str(timestamp) if timestamp is not None else None,
            last_price=parse_decimal(data["lastPrice"], "marketIndicator.lastPrice"),
        )


@dataclass(frozen=True, slots=True)
class InvestorAmounts:
    buy_amount: Decimal
    sell_amount: Decimal

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> InvestorAmounts:
        return cls(
            buy_amount=parse_decimal(data["buyAmount"], "investor.buyAmount"),
            sell_amount=parse_decimal(data["sellAmount"], "investor.sellAmount"),
        )


@dataclass(frozen=True, slots=True)
class InvestorTradingRecord:
    date: str
    updated_at: str
    individual: InvestorAmounts
    foreigner: InvestorAmounts
    institution: InvestorAmounts
    other_corporation: InvestorAmounts

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> InvestorTradingRecord:
        return cls(
            date=str(data["date"]),
            updated_at=str(data["updatedAt"]),
            individual=InvestorAmounts.from_api(data["individual"]),
            foreigner=InvestorAmounts.from_api(data["foreigner"]),
            institution=InvestorAmounts.from_api(data["institution"]),
            other_corporation=InvestorAmounts.from_api(data["otherCorporation"]),
        )


@dataclass(frozen=True, slots=True)
class MarketContextSnapshot:
    indicators: dict[str, MarketIndicator]
    investor_trading: dict[str, tuple[InvestorTradingRecord, ...]]


@dataclass(frozen=True, slots=True)
class RankingItem:
    rank: int
    symbol: str
    currency: str
    last_price: Decimal
    base_price: Decimal
    change_rate: Decimal | None
    trading_volume: Decimal
    trading_amount: Decimal

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> RankingItem:
        price = data["price"]
        try:
            rank = int(data["rank"])
        except (TypeError, ValueError) as err:
            raise TossDataError("Invalid ranking rank") from err
        return cls(
            rank=rank,
            symbol=str(data["symbol"]),
            currency=str(data["currency"]),
            last_price=parse_decimal(price["lastPrice"], "ranking.price.lastPrice"),
            base_price=parse_decimal(price["basePrice"], "ranking.price.basePrice"),
            change_rate=parse_decimal(
                price.get("changeRate"), "ranking.price.changeRate", optional=True
            ),
            trading_volume=parse_decimal(data["tradingVolume"], "ranking.tradingVolume"),
            trading_amount=parse_decimal(data["tradingAmount"], "ranking.tradingAmount"),
        )


@dataclass(frozen=True, slots=True)
class RankingSnapshot:
    ranked_at: str | None
    items: tuple[RankingItem, ...]

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> RankingSnapshot:
        ranked_at = data.get("rankedAt")
        return cls(
            ranked_at=str(ranked_at) if ranked_at is not None else None,
            items=tuple(RankingItem.from_api(item) for item in data["rankings"]),
        )


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Decimal-safe current quote returned by the price coordinator."""

    symbol: str
    timestamp: str
    last_price: Decimal
    currency: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> PriceQuote:
        return cls(
            symbol=str(data["symbol"]),
            timestamp=str(data["timestamp"]),
            last_price=parse_decimal(data["lastPrice"], "price.lastPrice"),
            currency=str(data["currency"]),
        )


class TossCoordinator(DataUpdateCoordinator[_DataT], Generic[_DataT]):
    """Coordinator that maps errors and tracks shared per-group freshness."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        name: str,
        group: str,
        stale_groups: set[str],
        update_interval: timedelta,
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Toss Invest {name}",
            update_interval=update_interval,
            always_update=False,
            config_entry=config_entry,
        )
        self.group = group
        self.stale_groups = stale_groups
        self.last_success: datetime | None = None
        self._coalesced_refresh: asyncio.Task[None] | None = None

    async def async_request_refresh(self) -> None:
        """Coalesce manual/listener refreshes that overlap in time."""
        task = self._coalesced_refresh
        if task is None:
            task = self.hass.async_create_task(super().async_refresh())
            self._coalesced_refresh = task
        try:
            await task
        finally:
            if self._coalesced_refresh is task:
                self._coalesced_refresh = None

    async def _async_fetch(self) -> _DataT:
        raise NotImplementedError

    def _on_data_success(self, data: _DataT) -> None:
        """Run group-specific state updates only after parsing succeeds."""

    async def _async_update_data(self) -> _DataT:
        try:
            data = await self._async_fetch()
            self._on_data_success(data)
        except TossAuthError as err:
            self.stale_groups.add(self.group)
            raise ConfigEntryAuthFailed from err
        except (
            TossApiError,
            TossRateLimitError,
            TossDataError,
            KeyError,
            TypeError,
            asyncio.TimeoutError,
            RuntimeError,
        ) as err:
            self.stale_groups.add(self.group)
            raise UpdateFailed(str(err)) from err
        self.last_success = datetime.now(UTC)
        self.stale_groups.discard(self.group)
        return data


class HoldingsCoordinator(TossCoordinator[HoldingsOverview]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TossInvestClient,
        account_seq: str,
        stale_groups: set[str],
        update_interval: timedelta,
        on_success: Callable[[HoldingsOverview], None],
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            name="holdings",
            group="holdings",
            stale_groups=stale_groups,
            update_interval=update_interval,
            config_entry=config_entry,
        )
        self._client = client
        self._account_seq = account_seq
        self._on_success = on_success

    async def _async_fetch(self) -> HoldingsOverview:
        return HoldingsOverview.from_api(await self._client.async_get_holdings(self._account_seq))

    def _on_data_success(self, data: HoldingsOverview) -> None:
        self._on_success(data)


class PriceCoordinator(TossCoordinator[dict[str, PriceQuote]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TossInvestClient,
        holdings: HoldingsCoordinator,
        stale_groups: set[str],
        update_interval: timedelta,
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            name="prices",
            group="prices",
            stale_groups=stale_groups,
            update_interval=update_interval,
            config_entry=config_entry,
        )
        self._client = client
        self._holdings = holdings

    async def _async_fetch(self) -> dict[str, PriceQuote]:
        holdings = self._holdings.data
        symbols = [item.symbol for item in holdings.items] if holdings is not None else []
        quotes = [
            PriceQuote.from_api(item) for item in await self._client.async_get_prices(symbols)
        ]
        return {quote.symbol: quote for quote in quotes}


class ReferenceCoordinator(TossCoordinator[MarketSnapshot]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TossInvestClient,
        stale_groups: set[str],
        update_interval: timedelta,
        on_success: Callable[[], None],
        now_fn: Callable[[], datetime],
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            name="reference",
            group="reference",
            stale_groups=stale_groups,
            update_interval=update_interval,
            config_entry=config_entry,
        )
        self._client = client
        self._on_success = on_success
        self._now_fn = now_fn
        self.kr_calendar: dict[str, Any] | None = None
        self.us_calendar: dict[str, Any] | None = None
        self._pending_calendars: tuple[dict[str, Any], dict[str, Any]]

    async def _async_fetch(self) -> MarketSnapshot:
        exchange, kr_calendar, us_calendar = await asyncio.gather(
            self._client.async_get_exchange_rate(),
            self._client.async_get_market_calendar("KR"),
            self._client.async_get_market_calendar("US"),
        )
        snapshot = MarketSnapshot.from_api(
            {
                "exchangeRate": exchange,
                "krMarketCalendar": kr_calendar,
                "usMarketCalendar": us_calendar,
            }
        )
        now = self._now_fn()
        self._pending_calendars = (kr_calendar, us_calendar)
        return replace(
            snapshot,
            kr_market_open=market_session_is_open(kr_calendar, "KR", now),
            us_market_open=market_session_is_open(us_calendar, "US", now),
        )

    def _on_data_success(self, data: MarketSnapshot) -> None:
        previous = (self.kr_calendar, self.us_calendar)
        self.kr_calendar, self.us_calendar = self._pending_calendars
        try:
            self._on_success()
        except Exception:
            self.kr_calendar, self.us_calendar = previous
            raise


class _HoldingsSymbolsCoordinator(TossCoordinator[_DataT], Generic[_DataT]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        name: str,
        group: str,
        client: TossInvestClient,
        holdings: HoldingsCoordinator,
        stale_groups: set[str],
        update_interval: timedelta,
        concurrency: asyncio.Semaphore,
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            name=name,
            group=group,
            stale_groups=stale_groups,
            update_interval=update_interval,
            config_entry=config_entry,
        )
        self._client = client
        self._holdings = holdings
        self._concurrency = concurrency

    def _symbols(self) -> list[str]:
        holdings = self._holdings.data
        return [item.symbol for item in holdings.items] if holdings is not None else []


class CandleCoordinator(_HoldingsSymbolsCoordinator[dict[str, tuple[Candle, ...]]]):
    def __init__(self, *args: Any, lookback: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lookback = max(20, min(500, lookback))

    async def _async_symbol_candles(self, symbol: str) -> tuple[Candle, ...]:
        candles: list[Candle] = []
        seen_timestamps: set[str] = set()
        seen_cursors: set[str] = set()
        before: str | None = None
        while len(candles) < self._lookback:
            count = min(200, self._lookback - len(candles))
            async with self._concurrency:
                page = await self._client.async_get_candles(
                    symbol,
                    count=count,
                    interval="1d",
                    adjusted=True,
                    **({"before": before} if before is not None else {}),
                )
            rows = page["candles"]
            if not isinstance(rows, list):
                raise TossDataError("Invalid candle page")
            if not rows:
                break
            for row in rows:
                candle = Candle.from_api(row)
                if candle.timestamp not in seen_timestamps:
                    candles.append(candle)
                    seen_timestamps.add(candle.timestamp)
                    if len(candles) == self._lookback:
                        break
            cursor = page.get("nextBefore")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            before = cursor
        return tuple(candles)

    async def _async_fetch(self) -> dict[str, tuple[Candle, ...]]:
        symbols = self._symbols()
        pages = await asyncio.gather(*(self._async_symbol_candles(symbol) for symbol in symbols))
        return dict(zip(symbols, pages, strict=True))


class WarningCoordinator(_HoldingsSymbolsCoordinator[dict[str, tuple[StockWarning, ...]]]):
    async def _async_symbol_warnings(self, symbol: str) -> tuple[StockWarning, ...]:
        async with self._concurrency:
            rows = await self._client.async_get_warnings(symbol)
        return tuple(StockWarning.from_api(row) for row in rows)

    async def _async_fetch(self) -> dict[str, tuple[StockWarning, ...]]:
        symbols = self._symbols()
        warnings = await asyncio.gather(
            *(self._async_symbol_warnings(symbol) for symbol in symbols)
        )
        return dict(zip(symbols, warnings, strict=True))


class BuyingPowerCoordinator(TossCoordinator[dict[str, Decimal]]):
    def __init__(
        self,
        *args: Any,
        client: TossInvestClient,
        account_seq: str,
        enabled: bool,
        concurrency: asyncio.Semaphore,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._client = client
        self._account_seq = account_seq
        self._enabled = enabled
        self._concurrency = concurrency

    async def _async_currency(self, currency: Literal["KRW", "USD"]) -> tuple[str, Decimal]:
        async with self._concurrency:
            data = await self._client.async_get_buying_power(self._account_seq, currency)
        returned_currency = str(data["currency"])
        if returned_currency != currency:
            raise TossDataError("Unexpected buying power currency")
        return currency, parse_decimal(data["cashBuyingPower"], "buyingPower.cashBuyingPower")

    async def _async_fetch(self) -> dict[str, Decimal]:
        if not self._enabled:
            return {}
        values = await asyncio.gather(*(self._async_currency(code) for code in ("KRW", "USD")))
        return dict(values)


class MarketContextCoordinator(TossCoordinator[MarketContextSnapshot]):
    def __init__(
        self,
        *args: Any,
        client: TossInvestClient,
        concurrency: asyncio.Semaphore,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._client = client
        self._concurrency = concurrency

    async def _async_investor_trading(
        self, symbol: Literal["KOSPI", "KOSDAQ"]
    ) -> tuple[str, tuple[InvestorTradingRecord, ...]]:
        async with self._concurrency:
            data = await self._client.async_get_investor_trading(symbol, interval="1d", count=10)
        rows = data["records"]
        if not isinstance(rows, list):
            raise TossDataError("Invalid investor trading records")
        return symbol, tuple(InvestorTradingRecord.from_api(row) for row in rows)

    async def _async_fetch(self) -> MarketContextSnapshot:
        async with self._concurrency:
            indicator_rows = await self._client.async_get_market_indicators(
                list(KOREAN_MARKET_INDICATORS)
            )
        indicators = [MarketIndicator.from_api(row) for row in indicator_rows]
        investor_trading = await asyncio.gather(
            *(self._async_investor_trading(symbol) for symbol in ("KOSPI", "KOSDAQ"))
        )
        return MarketContextSnapshot(
            indicators={item.symbol: item for item in indicators},
            investor_trading=dict(investor_trading),
        )


class RankingCoordinator(TossCoordinator[dict[tuple[str, str], RankingSnapshot]]):
    def __init__(
        self,
        *args: Any,
        client: TossInvestClient,
        enabled: bool,
        concurrency: asyncio.Semaphore,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._client = client
        self._enabled = enabled
        self._concurrency = concurrency

    async def _async_ranking(
        self, country: str, ranking_type: str
    ) -> tuple[tuple[str, str], RankingSnapshot]:
        duration = "realtime" if ranking_type == "MARKET_TRADING_AMOUNT" else "1d"
        async with self._concurrency:
            data = await self._client.async_get_rankings(
                type=ranking_type,
                market_country=country,
                duration=duration,
                count=10,
            )
        return (country, ranking_type), RankingSnapshot.from_api(data)

    async def _async_fetch(self) -> dict[tuple[str, str], RankingSnapshot]:
        if not self._enabled:
            return {}
        requests = (
            (country, ranking_type)
            for country in ("KR", "US")
            for ranking_type in ("MARKET_TRADING_AMOUNT", "TOP_GAINERS", "TOP_LOSERS")
        )
        return dict(await asyncio.gather(*(self._async_ranking(*request) for request in requests)))


@dataclass(slots=True)
class TossInvestRuntimeData:
    client: TossInvestClient
    holdings: HoldingsCoordinator
    prices: PriceCoordinator
    reference: ReferenceCoordinator
    candles: CandleCoordinator
    warnings: WarningCoordinator
    buying_power: BuyingPowerCoordinator
    market_context: MarketContextCoordinator
    rankings: RankingCoordinator
    alerts: object | None = None
    privacy: bool = True
    stale_groups: set[str] = field(default_factory=set)
    open_price_interval: timedelta = timedelta(seconds=30)
    closed_price_interval: timedelta = timedelta(minutes=10)

    @property
    def advanced_coordinators(self) -> tuple[TossCoordinator[Any], ...]:
        return (
            self.candles,
            self.warnings,
            self.buying_power,
            self.market_context,
            self.rankings,
        )

    async def async_refresh_all(self) -> None:
        """Refresh dependencies first, then all independent consumer groups."""
        await self.holdings.async_request_refresh()
        await asyncio.gather(
            self.reference.async_request_refresh(),
            self.prices.async_request_refresh(),
            *(coordinator.async_request_refresh() for coordinator in self.advanced_coordinators),
        )

    def reschedule_prices(
        self,
        now: datetime | None = None,
        holdings: HoldingsOverview | None = None,
    ) -> None:
        current = now or datetime.now(UTC)
        held_markets = (
            {item.market_country for item in (holdings or self.holdings.data).items}
            if holdings is not None or self.holdings.data is not None
            else set()
        )
        is_open = (
            "KR" in held_markets
            and market_session_is_open(self.reference.kr_calendar, "KR", current)
        ) or (
            "US" in held_markets
            and market_session_is_open(self.reference.us_calendar, "US", current)
        )
        self.prices.update_interval = (
            self.open_price_interval if is_open else self.closed_price_interval
        )
        if self.prices.last_success is not None:
            self.prices.hass.async_create_task(
                self.prices.async_request_refresh(),
                "toss_invest_reschedule_prices",
                eager_start=False,
            )

    async def async_shutdown(self) -> None:
        await asyncio.gather(
            self.holdings.async_shutdown(),
            self.prices.async_shutdown(),
            self.reference.async_shutdown(),
            *(coordinator.async_shutdown() for coordinator in self.advanced_coordinators),
        )


def _parse_instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def market_session_is_open(calendar: Mapping[str, Any] | None, country: str, now: datetime) -> bool:
    """Return whether *now* falls within an actual session in today's calendar."""
    if calendar is None:
        return False
    today = calendar.get("today")
    if not isinstance(today, Mapping):
        return False
    sessions: Any = today.get("integrated") if country == "KR" else today
    if not isinstance(sessions, Mapping):
        return False
    for session in sessions.values():
        if not isinstance(session, Mapping):
            continue
        start = _parse_instant(session.get("startTime"))
        end = _parse_instant(session.get("endTime"))
        if start is not None and end is not None and start <= now < end:
            return True
    return False


def create_runtime(
    hass: HomeAssistant,
    client: TossInvestClient,
    account_seq: str,
    options: Mapping[str, Any],
    config_entry: ConfigEntry[Any] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> TossInvestRuntimeData:
    """Build independent essential and advanced coordinators around one read-only client."""
    stale_groups: set[str] = set()
    runtime: TossInvestRuntimeData

    clock = now_fn or (lambda: datetime.now(UTC))

    def reschedule_holdings(data: HoldingsOverview) -> None:
        runtime.reschedule_prices(clock(), data)

    def reschedule_reference() -> None:
        runtime.reschedule_prices(clock())

    holdings = HoldingsCoordinator(
        hass,
        client,
        account_seq,
        stale_groups,
        timedelta(seconds=float(options.get("holdings_interval", 300))),
        reschedule_holdings,
        config_entry,
    )
    prices = PriceCoordinator(
        hass,
        client,
        holdings,
        stale_groups,
        timedelta(seconds=float(options.get("closed_price_interval", 600))),
        config_entry,
    )
    reference = ReferenceCoordinator(
        hass,
        client,
        stale_groups,
        timedelta(seconds=float(options.get("reference_interval", 1800))),
        reschedule_reference,
        clock,
        config_entry,
    )
    advanced_interval = timedelta(seconds=float(options.get("reference_interval", 1800)))
    advanced_concurrency = asyncio.Semaphore(_ADVANCED_CONCURRENCY)
    candles = CandleCoordinator(
        hass,
        name="candles",
        group="candles",
        client=client,
        holdings=holdings,
        stale_groups=stale_groups,
        update_interval=advanced_interval,
        concurrency=advanced_concurrency,
        config_entry=config_entry,
        lookback=int(options.get("candle_lookback", 252)),
    )
    warnings = WarningCoordinator(
        hass,
        name="warnings",
        group="warnings",
        client=client,
        holdings=holdings,
        stale_groups=stale_groups,
        update_interval=advanced_interval,
        concurrency=advanced_concurrency,
        config_entry=config_entry,
    )
    buying_power = BuyingPowerCoordinator(
        hass,
        name="buying power",
        group="buying_power",
        stale_groups=stale_groups,
        update_interval=timedelta(seconds=float(options.get("holdings_interval", 300))),
        config_entry=config_entry,
        client=client,
        account_seq=account_seq,
        enabled=bool(options.get("enable_buying_power", False)),
        concurrency=advanced_concurrency,
    )
    market_context = MarketContextCoordinator(
        hass,
        name="market context",
        group="market_context",
        stale_groups=stale_groups,
        update_interval=advanced_interval,
        config_entry=config_entry,
        client=client,
        concurrency=advanced_concurrency,
    )
    rankings = RankingCoordinator(
        hass,
        name="rankings",
        group="rankings",
        stale_groups=stale_groups,
        update_interval=advanced_interval,
        config_entry=config_entry,
        client=client,
        enabled=bool(options.get("enable_rankings", False)),
        concurrency=advanced_concurrency,
    )
    runtime = TossInvestRuntimeData(
        client=client,
        holdings=holdings,
        prices=prices,
        reference=reference,
        candles=candles,
        warnings=warnings,
        buying_power=buying_power,
        market_context=market_context,
        rankings=rankings,
        stale_groups=stale_groups,
        open_price_interval=timedelta(seconds=float(options.get("open_price_interval", 30))),
        closed_price_interval=timedelta(seconds=float(options.get("closed_price_interval", 600))),
    )
    return runtime
