from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import pytest

from custom_components.toss_invest.api.auth import TokenManager, TossAuthError, TossApiError
from custom_components.toss_invest.api.rate_limit import RateLimiter, TossRateLimitError

TOKEN_URL = "https://openapi.tossinvest.com/oauth2/token"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeResponse:
    """A minimal stand-in for the `async with session.post(...)` response context manager."""

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


def make_manager(session: Any, *, clock: Any = None) -> TokenManager:
    kwargs: dict[str, Any] = {}
    if clock is not None:
        kwargs["clock"] = clock
    return TokenManager(
        session,
        base_url="https://openapi.tossinvest.com",
        client_id="fake-client-id",
        client_secret="fake-client-secret",
        limiter=RateLimiter(),
        timeout=aiohttp.ClientTimeout(total=5),
        **kwargs,
    )


async def test_token_is_fetched_once_and_cached() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600}
            )
        ]
    )
    manager = make_manager(session)

    first = await manager.async_get_token()
    second = await manager.async_get_token()

    assert first == second == "fake-token"
    assert len(session.calls) == 1


async def test_token_request_is_form_encoded_with_client_credentials() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600}
            )
        ]
    )
    manager = make_manager(session)
    await manager.async_get_token()

    call = session.calls[0]
    assert call.url == TOKEN_URL
    assert call.kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "fake-client-id",
        "client_secret": "fake-client-secret",
    }


async def test_token_is_refetched_after_expiry_minus_safety_margin() -> None:
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(
                payload={"access_token": "token-1", "token_type": "Bearer", "expires_in": 120}
            ),
            FakeResponse(
                payload={"access_token": "token-2", "token_type": "Bearer", "expires_in": 120}
            ),
        ]
    )
    manager = make_manager(session, clock=clock)

    first = await manager.async_get_token()
    assert first == "token-1"

    # expires_in(120) - 60s safety margin == 60s; just before that, the cache still holds.
    clock.advance(59)
    still_cached = await manager.async_get_token()
    assert still_cached == "token-1"
    assert len(session.calls) == 1

    clock.advance(2)
    refreshed = await manager.async_get_token()
    assert refreshed == "token-2"
    assert len(session.calls) == 2


async def test_invalidate_forces_refetch() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={"access_token": "token-1", "token_type": "Bearer", "expires_in": 3600}
            ),
            FakeResponse(
                payload={"access_token": "token-2", "token_type": "Bearer", "expires_in": 3600}
            ),
        ]
    )
    manager = make_manager(session)
    await manager.async_get_token()

    manager.invalidate()

    second = await manager.async_get_token()
    assert second == "token-2"
    assert len(session.calls) == 2


async def test_rejected_credentials_raise_toss_auth_error_with_oauth_code() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status=401,
                payload={
                    "error": "invalid_client",
                    "error_description": "Client authentication failed.",
                },
            )
        ]
    )
    manager = make_manager(session)

    with pytest.raises(TossAuthError) as excinfo:
        await manager.async_get_token()
    assert excinfo.value.code == "invalid_client"


async def test_token_endpoint_429_raises_rate_limit_error_with_retry_after() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status=429,
                headers={"Retry-After": "7"},
                payload={
                    "error": {"requestId": "req-1", "code": "rate-limit-exceeded", "message": ""}
                },
            )
        ]
    )
    manager = make_manager(session)

    with pytest.raises(TossRateLimitError) as excinfo:
        await manager.async_get_token()
    assert excinfo.value.retry_after == 7.0


class _HeldResponse(FakeResponse):
    """A response whose context-manager entry waits on an event before completing.

    Used to prove that concurrent `async_get_token()` callers share a single
    in-flight fetch instead of each issuing their own POST to the token endpoint.
    """

    def __init__(self, release: asyncio.Event, payload: dict[str, Any]) -> None:
        super().__init__(payload=payload)
        self._release = release

    async def __aenter__(self) -> "_HeldResponse":
        await self._release.wait()
        return self


class _SingleFetchSession:
    def __init__(self) -> None:
        self.post_count = 0
        self.release = asyncio.Event()

    def post(self, url: str, **kwargs: Any) -> _HeldResponse:
        self.post_count += 1
        return _HeldResponse(
            self.release,
            {"access_token": "shared-token", "token_type": "Bearer", "expires_in": 3600},
        )


async def test_concurrent_get_token_calls_share_a_single_fetch() -> None:
    fake_session = _SingleFetchSession()
    manager = make_manager(fake_session)

    task_a = asyncio.ensure_future(manager.async_get_token())
    task_b = asyncio.ensure_future(manager.async_get_token())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fake_session.post_count == 1

    fake_session.release.set()
    first, second = await asyncio.gather(task_a, task_b)
    assert first == second == "shared-token"
    assert fake_session.post_count == 1


async def test_oauth_500_raises_api_error() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status=500,
                payload={
                    "error": {"code": "server-error", "message": "Failed", "requestId": "req-500"}
                },
            )
        ]
    )
    manager = make_manager(session)

    with pytest.raises(TossApiError) as excinfo:
        await manager.async_get_token()
    assert excinfo.value.code == "auth-server-error-500"
    assert excinfo.value.request_id == "req-500"


async def test_oauth_transport_error_raises_api_error() -> None:
    session = FakeSession([aiohttp.ClientError("Network issue")])
    manager = make_manager(session)

    with pytest.raises(TossApiError) as excinfo:
        await manager.async_get_token()
    assert excinfo.value.code == "auth-connection-error"


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        {},
        {"access_token": "token"},
        {"access_token": "token", "token_type": "Bearer"},
        {"access_token": "token", "token_type": "Bearer", "expires_in": "not-a-number"},
        {"access_token": "token", "token_type": "Bearer", "expires_in": -3600},
        {"access_token": "token", "token_type": "Bearer", "expires_in": float("inf")},
        {"access_token": "token", "token_type": "Bearer", "expires_in": float("nan")},
        {"access_token": "", "token_type": "Bearer", "expires_in": 3600},
        {"access_token": "token", "token_type": "MAC", "expires_in": 3600},
        "RAISE_JSON_DECODE_ERROR",
    ],
)
async def test_oauth_malformed_responses_raise_api_error(bad_payload: Any) -> None:
    session = FakeSession(
        [FakeResponse(status=200, payload=bad_payload, headers={"X-Request-Id": "req-malformed"})]
    )
    manager = make_manager(session)
    with pytest.raises(TossApiError) as excinfo:
        await manager.async_get_token()
    assert excinfo.value.code == "auth-malformed-response"
    assert excinfo.value.request_id == "req-malformed"
