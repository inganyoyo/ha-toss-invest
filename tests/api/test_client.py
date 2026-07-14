from __future__ import annotations

import asyncio
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
        if self._payload == "RAISE_JSON_DECODE_ERROR":
            import json

            json.loads("{invalid}")
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

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[RecordedCall] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(RecordedCall(method="POST", url=url, kwargs=kwargs))
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append(RecordedCall(method=method, url=url, kwargs=kwargs))
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


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


def make_client(
    responses: list[Any], *, max_retries: int = 3
) -> tuple[TossInvestClient, FakeSession]:
    session = FakeSession(responses)
    client = TossInvestClient(
        session,  # type: ignore[arg-type]
        "fake-client-id",
        "fake-client-secret",
        max_retries=max_retries,
    )
    return client, session


@pytest.mark.parametrize("max_retries", [0, 1, 5])
async def test_configured_max_retries_is_exact(
    monkeypatch: pytest.MonkeyPatch, max_retries: int
) -> None:
    async def no_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr("custom_components.toss_invest.api.client.asyncio.sleep", no_sleep)
    client, session = make_client(
        [token_response(), *[asyncio.TimeoutError() for _ in range(max_retries + 1)]],
        max_retries=max_retries,
    )

    with pytest.raises(TossApiError, match="connection-error"):
        await client.async_get_accounts()

    assert len([call for call in session.calls if call.method == "GET"]) == max_retries + 1


@pytest.mark.parametrize("invalid", [-1, 6, True, 1.5, "1"])
def test_max_retries_rejects_values_outside_integer_option_bound(invalid: object) -> None:
    session = FakeSession([])
    with pytest.raises((TypeError, ValueError)):
        TossInvestClient(
            session,  # type: ignore[arg-type]
            "fake-client-id",
            "fake-client-secret",
            max_retries=invalid,  # type: ignore[arg-type]
        )


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


async def test_401_retry_success() -> None:
    client, session = make_client(
        [
            token_response(token="stale-token"),
            api_error(401, code="expired-token", request_id="req-401"),
            token_response(token="fresh-token"),
            ok(load_fixture("holdings.json")),
        ]
    )

    res = await client.async_get_holdings("acct")
    assert res == load_fixture("holdings.json")

    post_calls = [call for call in session.calls if call.method == "POST"]
    assert len(post_calls) == 2


async def test_401_repeated_failure() -> None:
    client, session = make_client(
        [
            token_response(token="stale-token"),
            api_error(401, code="expired-token", request_id="req-401"),
            token_response(token="fresh-token"),
            api_error(401, code="permanently-expired", request_id="req-401-2"),
        ]
    )

    with pytest.raises(TossAuthError) as excinfo:
        await client.async_get_holdings("acct")
    assert excinfo.value.code == "permanently-expired"


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


async def test_market_indicator_candles_uses_symbol_path_segment() -> None:
    inner_payload = {"candles": [{"timestamp": "2026-07-14T00:00:00+09:00"}], "nextBefore": None}
    client, session = make_client([token_response(), ok(inner_payload)])

    result = await client.async_get_market_indicator_candles("KOSPI", count=2, interval="1d")

    assert result == inner_payload
    get_call = next(call for call in session.calls if call.method == "GET")
    assert get_call.url == f"{BASE_URL}/api/v1/market-indicators/KOSPI/candles"
    assert get_call.kwargs["params"] == {"interval": "1d", "count": 2}


async def test_empty_symbols_returns_empty_without_requests() -> None:
    client, session = make_client([])
    res_prices = await client.async_get_prices([])
    assert res_prices == []

    res_indicators = await client.async_get_market_indicators([])
    assert res_indicators == []

    assert len(session.calls) == 0


async def test_request_timeout_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dummy_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(
        "custom_components.toss_invest.api.client.asyncio.sleep",
        dummy_sleep,
    )
    monkeypatch.setattr("random.uniform", lambda a, b: b)

    accounts_payload = load_fixture("accounts.json")
    client, session = make_client(
        [
            token_response(),
            asyncio.TimeoutError("Timeout 1"),
            asyncio.TimeoutError("Timeout 2"),
            ok(accounts_payload),
        ]
    )

    res = await client.async_get_accounts()
    assert res == accounts_payload
    assert len(session.calls) == 4


async def test_request_timeout_raises_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dummy_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(
        "custom_components.toss_invest.api.client.asyncio.sleep",
        dummy_sleep,
    )
    monkeypatch.setattr("random.uniform", lambda a, b: b)

    client, session = make_client(
        [
            token_response(),
            asyncio.TimeoutError("Timeout 1"),
            asyncio.TimeoutError("Timeout 2"),
            asyncio.TimeoutError("Timeout 3"),
            asyncio.TimeoutError("Timeout 4"),
        ]
    )

    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.code == "connection-error"


async def test_5xx_retries_with_backoff_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(
        "custom_components.toss_invest.api.client.asyncio.sleep",
        record_sleep,
    )
    monkeypatch.setattr("random.uniform", lambda a, b: b)

    client, session = make_client(
        [
            token_response(),
            FakeResponse(status=500),
            FakeResponse(status=500),
            FakeResponse(status=500),
            FakeResponse(status=500),
        ]
    )

    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()

    assert excinfo.value.code == "server-error-500"
    assert sleep_calls == [0.5, 1.0, 2.0]


async def test_malformed_success_envelope_raises_api_error() -> None:
    client, session = make_client(
        [
            token_response(),
            FakeResponse(payload={}),
        ]
    )

    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.code == "api-error"


async def test_api_error_request_id_fallback() -> None:
    client, session = make_client(
        [
            token_response(),
            FakeResponse(status=400, payload=None, headers={"X-Request-Id": "header-req-id"}),
        ]
    )

    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.code == "api-error"
    assert excinfo.value.request_id == "header-req-id"


async def test_source_has_no_mutation_endpoints_or_logged_secrets() -> None:
    import ast

    forbidden_substrings = ("/orders", "/conditional-orders")

    for path in sorted(API_SOURCE_DIR.glob("*.py")):
        text = path.read_text()

        for needle in forbidden_substrings:
            assert needle not in text, f"{path} references a mutating endpoint path {needle!r}"

        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            # Check logger calls
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                is_logger = False
                base = node.func.value
                if isinstance(base, ast.Name) and base.id == "_LOGGER":
                    is_logger = True

                if is_logger:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Name):
                            name_lower = child.id.lower()
                            for secret_marker in (
                                "client_secret",
                                "access_token",
                                "password",
                                "headers",
                                "payload",
                            ):
                                assert secret_marker not in name_lower, (
                                    f"{path} logs a sensitive variable {child.id!r}"
                                )
                        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                            val_lower = child.value.lower()
                            for secret_marker in (
                                "client_secret",
                                "access_token",
                                "password",
                                "headers",
                                "payload",
                            ):
                                assert secret_marker not in val_lower, (
                                    f"{path} logs a sensitive string {child.value!r}"
                                )
                        elif isinstance(child, ast.Attribute):
                            attr_lower = child.attr.lower()
                            for secret_marker in (
                                "client_secret",
                                "access_token",
                                "password",
                                "headers",
                                "payload",
                            ):
                                assert secret_marker not in attr_lower, (
                                    f"{path} logs a sensitive attribute {child.attr!r}"
                                )

            # Check direct session post/put/patch/delete
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
                if method_name in ("post", "put", "patch", "delete"):
                    if path.name == "client.py":
                        pytest.fail(f"{path} has direct session mutation call: {method_name}")
                    elif path.name == "auth.py":
                        if method_name != "post":
                            pytest.fail(
                                f"{path} has disallowed direct session mutation: {method_name}"
                            )
                        url_ok = False
                        if node.args:
                            url_node = node.args[0]
                            for sub in ast.walk(url_node):
                                if isinstance(sub, ast.Name) and sub.id == "TOKEN_PATH":
                                    url_ok = True
                                elif (
                                    isinstance(sub, ast.Constant)
                                    and isinstance(sub.value, str)
                                    and "TOKEN_PATH" in sub.value
                                ):
                                    url_ok = True
                        if not url_ok:
                            pytest.fail("auth.py has session.post call targeting unauthorized URL")
                    else:
                        pytest.fail(f"{path} has direct session mutation call: {method_name}")


async def test_5xx_exits_context_before_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    exited = False

    class TrackExitResponse(FakeResponse):
        async def __aexit__(self, *exc_info: Any) -> None:
            nonlocal exited
            exited = True
            await super().__aexit__(*exc_info)

    client, session = make_client(
        [token_response(), TrackExitResponse(status=500), ok(load_fixture("accounts.json"))]
    )

    sleep_called_after_exit = False

    async def mock_sleep(delay: float) -> None:
        nonlocal sleep_called_after_exit
        if exited:
            sleep_called_after_exit = True

    monkeypatch.setattr("custom_components.toss_invest.api.client.asyncio.sleep", mock_sleep)
    monkeypatch.setattr("random.uniform", lambda a, b: b)

    await client.async_get_accounts()
    assert sleep_called_after_exit, "Response context was not exited before asyncio.sleep"


async def test_client_genuine_json_decode_error_on_200_raises_api_error() -> None:
    client, session = make_client(
        [token_response(), FakeResponse(status=200, payload="RAISE_JSON_DECODE_ERROR")]
    )
    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.code == "api-error"


async def test_client_genuine_json_decode_error_on_400_raises_api_error() -> None:
    client, session = make_client(
        [
            token_response(),
            FakeResponse(
                status=400,
                payload="RAISE_JSON_DECODE_ERROR",
                headers={"X-Request-Id": "req-decode-fail"},
            ),
        ]
    )
    with pytest.raises(TossApiError) as excinfo:
        await client.async_get_accounts()
    assert excinfo.value.code == "api-error"
    assert excinfo.value.request_id == "req-decode-fail"
