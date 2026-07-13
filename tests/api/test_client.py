from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from custom_components.toss_invest.api.client import (
    TossApiError,
    TossAuthError,
    TossInvestClient,
    TossRateLimitError,
)

FIXTURES_DIR = Path("tests/fixtures")
API_SOURCE_DIR = Path("custom_components/toss_invest/api")
BASE_URL = "https://openapi.tossinvest.com"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


class FakeResponse:
    def __init__(
        self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self._payload = payload
        self.headers: dict[str, str] = headers or {}

    async def json(self, content_type: Any = None) -> Any:
        return self._payload

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


@dataclass
class RecordedCall:
    method: str
    url: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class FakeSession:
    """A minimal `aiohttp.ClientSession` double that serves queued canned responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[RecordedCall] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall(method="POST", url=url, kwargs=kwargs))
        return self._responses.pop(0)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall(method=method, url=url, kwargs=kwargs))
        return self._responses.pop(0)


def token_response(token: str = "fake-token", expires_in: int = 3600) -> FakeResponse:
    return FakeResponse(
        payload={"access_token": token, "token_type": "Bearer", "expires_in": expires_in}
    )


def ok(result: Any, headers: dict[str, str] | None = None) -> FakeResponse:
    return FakeResponse(payload={"result": result}, headers=headers)


def api_error(
    status: int,
    *,
    code: str = "api-error",
    request_id: str | None = "req-1",
    headers: dict[str, str] | None = None,
) -> FakeResponse:
    error: dict[str, Any] = {"code": code, "message": ""}
    if request_id is not None:
        error["requestId"] = request_id
    return FakeResponse(status=status, payload={"error": error}, headers=headers)


def make_client(responses: list[FakeResponse]) -> tuple[TossInvestClient, FakeSession]:
    session = FakeSession(responses)
    client = TossInvestClient(session, "fake-client-id", "fake-client-secret")  # type: ignore[arg-type]
    return client, session


async def test_holdings_uses_cached_token_and_account_header() -> None:
    holdings_payload = load_fixture("holdings.json")
    client, session = make_client([token_response(), ok(holdings_payload), ok(holdings_payload)])

    first = await client.async_get_holdings("sanitized-account")
    second = await client.async_get_holdings("sanitized-account")

    assert first == second == holdings_payload
    post_calls = [call for call in session.calls if call.method == "POST"]
    assert len(post_calls) == 1
    get_calls = [call for call in session.calls if call.method == "GET"]
    assert get_calls[0].kwargs["headers"]["X-Tossinvest-Account"] == "sanitized-account"


async def test_accounts_does_not_send_account_header() -> None:
    accounts_payload = load_fixture("accounts.json")
    client, session = make_client([token_response(), ok(accounts_payload)])

    result = await client.async_get_accounts()

    assert result == accounts_payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert "X-Tossinvest-Account" not in get_call.kwargs["headers"]
    assert get_call.url == f"{BASE_URL}/api/v1/accounts"


async def test_async_validate_only_fetches_a_token() -> None:
    client, session = make_client([token_response()])

    await client.async_validate()

    assert len(session.calls) == 1
    assert session.calls[0].method == "POST"


async def test_401_invalidates_token_and_raises_auth_error() -> None:
    client, session = make_client(
        [
            token_response(token="stale-token"),
            api_error(401, code="expired-token", request_id="req-401"),
            token_response(token="fresh-token"),
            ok(load_fixture("holdings.json")),
        ]
    )

    with pytest.raises(TossAuthError) as excinfo:
        await client.async_get_holdings("acct")
    assert excinfo.value.code == "expired-token"

    # A subsequent call must fetch a brand new token rather than reuse the rejected one.
    await client.async_get_holdings("acct")
    post_calls = [call for call in session.calls if call.method == "POST"]
    assert len(post_calls) == 2


async def test_429_raises_rate_limit_error_with_retry_after() -> None:
    client, session = make_client(
        [
            token_response(),
            FakeResponse(
                status=429,
                headers={"Retry-After": "12"},
                payload={
                    "error": {"requestId": "req-429", "code": "rate-limit-exceeded", "message": ""}
                },
            ),
        ]
    )

    with pytest.raises(TossRateLimitError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.retry_after == 12.0


async def test_generic_4xx_raises_api_error_with_request_id_and_code() -> None:
    client, session = make_client(
        [token_response(), api_error(404, code="stock-not-found", request_id="req-404")]
    )

    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_warnings("999999")
    assert excinfo.value.code == "stock-not-found"
    assert excinfo.value.request_id == "req-404"


async def test_unknown_top_level_fields_do_not_break_parsing() -> None:
    holdings_payload = load_fixture("holdings.json")
    response = FakeResponse(
        payload={"result": holdings_payload, "unexpectedField": "some-future-server-addition"}
    )
    client, session = make_client([token_response(), response])

    result = await client.async_get_holdings("acct")
    assert result == holdings_payload


async def test_prices_batches_over_200_symbols_into_multiple_requests() -> None:
    symbols = [f"{i:06d}" for i in range(250)]
    first_batch_payload = load_fixture("prices.json")
    second_batch_payload = load_fixture("prices.json")
    client, session = make_client(
        [token_response(), ok(first_batch_payload), ok(second_batch_payload)]
    )

    result = await client.async_get_prices(symbols)

    assert result == first_batch_payload + second_batch_payload
    get_calls = [call for call in session.calls if call.method == "GET"]
    assert len(get_calls) == 2
    assert get_calls[0].kwargs["params"]["symbols"] == ",".join(symbols[:200])
    assert get_calls[1].kwargs["params"]["symbols"] == ",".join(symbols[200:])


async def test_market_indicators_batches_over_200_symbols() -> None:
    symbols = [f"SYM{i}" for i in range(201)]
    client, session = make_client(
        [token_response(), ok([{"symbol": "KOSPI"}]), ok([{"symbol": "KOSDAQ"}])]
    )

    result = await client.async_get_market_indicators(symbols)

    assert result == [{"symbol": "KOSPI"}, {"symbol": "KOSDAQ"}]
    get_calls = [call for call in session.calls if call.method == "GET"]
    assert len(get_calls) == 2
    assert get_calls[1].kwargs["params"]["symbols"] == symbols[200]


async def test_candles_sends_interval_count_before_and_adjusted() -> None:
    candles_payload = load_fixture("candles.json")
    client, session = make_client([token_response(), ok(candles_payload)])

    result = await client.async_get_candles(
        "TEST", 50, "2026-07-10T09:00:00+09:00", interval="1d", adjusted=False
    )

    assert result == candles_payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/candles"
    assert get_call.kwargs["params"] == {
        "symbol": "TEST",
        "interval": "1d",
        "count": 50,
        "adjusted": "false",
        "before": "2026-07-10T09:00:00+09:00",
    }


async def test_exchange_rate_defaults_to_usd_krw() -> None:
    payload = load_fixture("market.json")["exchangeRate"]
    client, session = make_client([token_response(), ok(payload)])

    result = await client.async_get_exchange_rate()

    assert result == payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.kwargs["params"] == {"baseCurrency": "USD", "quoteCurrency": "KRW"}


async def test_market_calendar_uses_country_path_and_optional_date() -> None:
    kr_payload = load_fixture("market.json")["krMarketCalendar"]
    client, session = make_client([token_response(), ok(kr_payload)])

    result = await client.async_get_market_calendar("KR", date="2026-07-13")

    assert result == kr_payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/market-calendar/KR"
    assert get_call.kwargs["params"] == {"date": "2026-07-13"}


async def test_investor_trading_sends_symbol_path_and_query_params() -> None:
    client, session = make_client([token_response(), ok({"records": [], "nextUntil": None})])

    await client.async_get_investor_trading("KOSPI", interval="1w", count=5, until="2026-07-01")

    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/market-indicators/KOSPI/investor-trading"
    assert get_call.kwargs["params"] == {"interval": "1w", "count": 5, "until": "2026-07-01"}


async def test_rankings_sends_all_required_params() -> None:
    client, session = make_client([token_response(), ok({"rankedAt": None, "rankings": []})])

    await client.async_get_rankings(
        type="TOP_GAINERS",
        market_country="KR",
        duration="1w",
        exclude_investment_caution=True,
        count=10,
    )

    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/rankings"
    assert get_call.kwargs["params"] == {
        "type": "TOP_GAINERS",
        "marketCountry": "KR",
        "duration": "1w",
        "excludeInvestmentCaution": "true",
        "count": 10,
    }


async def test_buying_power_sends_account_header_and_currency_param() -> None:
    client, session = make_client(
        [token_response(), ok({"currency": "KRW", "cashBuyingPower": "5000000"})]
    )

    result = await client.async_get_buying_power("sanitized-account", "KRW")

    assert result == {"currency": "KRW", "cashBuyingPower": "5000000"}
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/buying-power"
    assert get_call.kwargs["headers"]["X-Tossinvest-Account"] == "sanitized-account"
    assert get_call.kwargs["params"] == {"currency": "KRW"}


async def test_warnings_uses_symbol_path_segment() -> None:
    warnings_payload = load_fixture("warnings.json")
    client, session = make_client([token_response(), ok(warnings_payload)])

    result = await client.async_get_warnings("TEST")

    assert result == warnings_payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/stocks/TEST/warnings"


def test_source_has_no_mutation_endpoints_or_logged_secrets() -> None:
    forbidden_substrings = ("/orders", "/conditional-orders")
    forbidden_methods = ('"POST"', "'POST'", '"DELETE"', "'DELETE'")

    for path in sorted(API_SOURCE_DIR.glob("*.py")):
        text = path.read_text()

        for needle in forbidden_substrings:
            assert needle not in text, f"{path} references a mutating endpoint path {needle!r}"

        for method_literal in forbidden_methods:
            if method_literal not in text:
                continue
            # The only legitimate mutating call in this package is the OAuth2 token
            # exchange, which uses `session.post(...)`, never a "POST"/"DELETE" method
            # literal passed to the generic `_request` dispatcher.
            pytest.fail(f"{path} passes a mutating HTTP method literal {method_literal!r}")

        for line in text.splitlines():
            if "_LOGGER" not in line:
                continue
            lowered = line.lower()
            for secret_marker in (
                "client_secret",
                "access_token",
                "password",
                "headers",
                "payload",
            ):
                assert secret_marker not in lowered, f"{path} logs a sensitive value: {line!r}"
