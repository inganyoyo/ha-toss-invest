"""Independent runtime coordinators for Toss Invest."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Generic, TypeVar

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TossApiError, TossAuthError, TossInvestClient, TossRateLimitError
from .models import HoldingsOverview, MarketSnapshot, TossDataError, parse_decimal

_LOGGER = logging.getLogger(__name__)
_DataT = TypeVar("_DataT")


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


@dataclass(slots=True)
class TossInvestRuntimeData:
    client: TossInvestClient
    holdings: HoldingsCoordinator
    prices: PriceCoordinator
    reference: ReferenceCoordinator
    alerts: object | None = None
    privacy: bool = True
    stale_groups: set[str] = field(default_factory=set)
    open_price_interval: timedelta = timedelta(seconds=30)
    closed_price_interval: timedelta = timedelta(minutes=10)

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
    """Build the three independent coordinators around one read-only client."""
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
    runtime = TossInvestRuntimeData(
        client=client,
        holdings=holdings,
        prices=prices,
        reference=reference,
        stale_groups=stale_groups,
        open_price_interval=timedelta(seconds=float(options.get("open_price_interval", 30))),
        closed_price_interval=timedelta(seconds=float(options.get("closed_price_interval", 600))),
    )
    return runtime
