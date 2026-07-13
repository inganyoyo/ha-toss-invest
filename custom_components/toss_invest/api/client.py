from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import aiohttp

from .auth import TokenManager, TossAuthError, TossApiError
from .rate_limit import RateLimiter, TossRateLimitError, parse_retry_after

__all__ = [
    "TossApiError",
    "TossAuthError",
    "TossInvestClient",
    "TossRateLimitError",
]

_LOGGER = logging.getLogger(__name__)

# Toss returns at most 200 symbols per batched Market Data / Market Indicators call.
_MAX_SYMBOL_BATCH = 200


class TossInvestClient:
    """Read-only async client for the Toss Securities Open API.

    Version 1 is strictly read-only: the only mutating request this client ever
    issues is the OAuth2 token exchange (`POST /oauth2/token`). Every domain method
    below calls a `GET` endpoint; there is no order, order-history, or
    conditional-order support.
    """

    BASE_URL = "https://openapi.tossinvest.com"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        if type(max_retries) is not int:
            raise TypeError("max_retries must be an integer")
        if not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._limiter = RateLimiter()
        self._tokens = TokenManager(
            session,
            base_url=self.BASE_URL,
            client_id=client_id,
            client_secret=client_secret,
            limiter=self._limiter,
            timeout=self._timeout,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        group: str,
        account_seq: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        attempt = 0
        max_attempts = self._max_retries + 1
        has_refreshed_token = False

        should_sleep = False
        sleep_duration = 0.0

        while True:
            await self._limiter.async_wait(group)
            token = await self._tokens.async_get_token()
            headers = {"Authorization": f"Bearer {token}"}
            if account_seq is not None:
                headers["X-Tossinvest-Account"] = str(account_seq)

            should_sleep = False
            sleep_duration = 0.0

            try:
                async with self._session.request(
                    method,
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                ) as response:
                    await self._limiter.async_update(group, response.headers)

                    if response.status == 401:
                        if not has_refreshed_token:
                            self._tokens.invalidate()
                            has_refreshed_token = True
                            continue
                        else:
                            try:
                                payload = await response.json(content_type=None)
                            except ValueError, TypeError, aiohttp.ContentTypeError:
                                payload = None
                            raise TossAuthError(_error_code(payload))

                    if response.status == 429:
                        try:
                            payload = await response.json(content_type=None)
                        except ValueError, TypeError, aiohttp.ContentTypeError:
                            payload = None
                        retry_after = parse_retry_after(
                            response.headers.get("Retry-After"), default=1.0
                        )
                        raise TossRateLimitError(retry_after)

                    if 500 <= response.status < 600:
                        if attempt < max_attempts - 1:
                            attempt += 1
                            backoff = min(10.0, 0.5 * (2 ** (attempt - 1)))
                            sleep_duration = random.uniform(0.0, backoff)
                            should_sleep = True
                        else:
                            try:
                                payload = await response.json(content_type=None)
                            except ValueError, TypeError, aiohttp.ContentTypeError:
                                payload = None
                            request_id = (
                                _error_request_id(payload) if payload else None
                            ) or response.headers.get("X-Request-Id")
                            raise TossApiError(request_id, f"server-error-{response.status}")

                    if not should_sleep:
                        if response.status >= 400:
                            try:
                                payload = await response.json(content_type=None)
                            except ValueError, TypeError, aiohttp.ContentTypeError:
                                payload = None
                            request_id = (
                                _error_request_id(payload) if payload else None
                            ) or response.headers.get("X-Request-Id")
                            code = _error_code(payload) if payload is not None else "api-error"
                            _LOGGER.debug("Toss API error code=%s request_id=%s", code, request_id)
                            raise TossApiError(request_id, code)

                        # Success (2xx)
                        try:
                            payload = await response.json(content_type=None)
                        except (ValueError, TypeError, aiohttp.ContentTypeError) as err:
                            raise TossApiError(
                                response.headers.get("X-Request-Id"), "api-error"
                            ) from err

                        if not isinstance(payload, dict) or "result" not in payload:
                            raise TossApiError(response.headers.get("X-Request-Id"), "api-error")

                        return payload["result"]

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt < max_attempts - 1:
                    attempt += 1
                    backoff = min(10.0, 0.5 * (2 ** (attempt - 1)))
                    sleep_duration = random.uniform(0.0, backoff)
                    should_sleep = True
                else:
                    raise TossApiError(None, "connection-error") from err

            if should_sleep:
                await asyncio.sleep(sleep_duration)

    async def async_validate(self) -> None:
        """Exchange credentials for a token, raising `TossAuthError` if they are rejected."""
        await self._tokens.async_get_token()

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/v1/accounts", group="ACCOUNT")

    async def async_get_holdings(self, account_seq: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/api/v1/holdings", group="ASSET", account_seq=account_seq
        )

    async def async_get_prices(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self._async_batched_symbols_get(
            "/api/v1/prices", group="MARKET_DATA", symbols=symbols
        )

    async def async_get_candles(
        self,
        symbol: str,
        count: int = 100,
        before: str | None = None,
        *,
        interval: Literal["1m", "1d"] = "1d",
        adjusted: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "count": count,
            "adjusted": str(adjusted).lower(),
        }
        if before is not None:
            params["before"] = before
        return await self._request(
            "GET", "/api/v1/candles", group="MARKET_DATA_CHART", params=params
        )

    async def async_get_warnings(self, symbol: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/v1/stocks/{symbol}/warnings", group="STOCK")

    async def async_get_exchange_rate(
        self, base_currency: str = "USD", quote_currency: str = "KRW"
    ) -> dict[str, Any]:
        params = {"baseCurrency": base_currency, "quoteCurrency": quote_currency}
        return await self._request(
            "GET", "/api/v1/exchange-rate", group="MARKET_INFO", params=params
        )

    async def async_get_market_calendar(
        self, country: Literal["KR", "US"], date: str | None = None
    ) -> dict[str, Any]:
        params = {"date": date} if date is not None else None
        return await self._request(
            "GET", f"/api/v1/market-calendar/{country}", group="MARKET_INFO", params=params
        )

    async def async_get_market_indicators(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        return await self._async_batched_symbols_get(
            "/api/v1/market-indicators/prices", group="MARKET_INDICATOR", symbols=symbols
        )

    async def async_get_investor_trading(
        self,
        symbol: Literal["KOSPI", "KOSDAQ"],
        *,
        interval: Literal["1d", "1w", "1mo", "1y"] = "1d",
        count: int = 10,
        until: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"interval": interval, "count": count}
        if until is not None:
            params["until"] = until
        return await self._request(
            "GET",
            f"/api/v1/market-indicators/{symbol}/investor-trading",
            group="MARKET_INDICATOR",
            params=params,
        )

    async def async_get_rankings(
        self,
        *,
        type: str,
        market_country: str,
        duration: str,
        exclude_investment_caution: bool = False,
        count: int = 100,
    ) -> dict[str, Any]:
        params = {
            "type": type,
            "marketCountry": market_country,
            "duration": duration,
            "excludeInvestmentCaution": str(exclude_investment_caution).lower(),
            "count": count,
        }
        return await self._request("GET", "/api/v1/rankings", group="RANKING", params=params)

    async def async_get_buying_power(
        self, account_seq: str, currency: Literal["KRW", "USD"]
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/buying-power",
            group="ORDER_INFO",
            account_seq=account_seq,
            params={"currency": currency},
        )

    async def _async_batched_symbols_get(
        self, path: str, *, group: str, symbols: Sequence[str]
    ) -> list[dict[str, Any]]:
        symbol_list = list(symbols)
        results: list[dict[str, Any]] = []
        for start in range(0, len(symbol_list), _MAX_SYMBOL_BATCH):
            chunk = symbol_list[start : start + _MAX_SYMBOL_BATCH]
            payload = await self._request(
                "GET", path, group=group, params={"symbols": ",".join(chunk)}
            )
            results.extend(payload)
        return results


def _error_code(payload: Any) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("code", "api-error"))
    return "unauthorized"


def _error_request_id(payload: Any) -> str | None:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        request_id = error.get("requestId")
        return str(request_id) if request_id is not None else None
    return None
