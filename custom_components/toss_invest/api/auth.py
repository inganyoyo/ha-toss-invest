from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from .rate_limit import RateLimiter, TossRateLimitError, parse_retry_after

TOKEN_PATH = "/oauth2/token"
AUTH_RATE_LIMIT_GROUP = "AUTH"

# Toss access tokens are treated as expired 60 seconds early so an in-flight
# request never races a real expiry against the server clock.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60.0


class TossAuthError(Exception):
    """Raised when the Toss OAuth2 token endpoint permanently rejects credentials."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Toss auth error: {code}")
        self.code = code


class TossApiError(Exception):
    """Raised for any 4xx/5xx Toss API response that is not an auth or rate-limit error."""

    def __init__(self, request_id: str | None, code: str) -> None:
        super().__init__(f"Toss API error {code} (request {request_id or 'unknown'})")
        self.request_id = request_id
        self.code = code


class TokenManager:
    """Caches a single OAuth2 client-credentials token per client.

    Toss issues at most one valid access token per client at a time and provides no
    refresh token, so a re-issued token immediately invalidates the previous one.
    An `asyncio.Lock` ensures concurrent callers share a single in-flight fetch
    instead of racing each other for a new token.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        limiter: RateLimiter,
        timeout: aiohttp.ClientTimeout,
        clock: Any = time.monotonic,
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._limiter = limiter
        self._timeout = timeout
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    async def async_get_token(self) -> str:
        async with self._lock:
            now = self._clock()
            if self._token is not None and now < self._expires_at:
                return self._token
            return await self._async_fetch_token()

    async def _async_fetch_token(self) -> str:
        await self._limiter.async_wait(AUTH_RATE_LIMIT_GROUP)
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            async with self._session.post(
                f"{self._base_url}{TOKEN_PATH}", data=data, timeout=self._timeout
            ) as response:
                await self._limiter.async_update(AUTH_RATE_LIMIT_GROUP, response.headers)
                try:
                    payload: Any = await response.json(content_type=None)
                except Exception:
                    payload = None

                if response.status == 429:
                    retry_after = parse_retry_after(
                        response.headers.get("Retry-After"), default=1.0
                    )
                    raise TossRateLimitError(retry_after)

                if 500 <= response.status < 600:
                    request_id = None
                    if isinstance(payload, dict) and "error" in payload:
                        err_data = payload["error"]
                        if isinstance(err_data, dict):
                            request_id = err_data.get("requestId")
                    request_id = request_id or response.headers.get("X-Request-Id")
                    raise TossApiError(request_id, f"auth-server-error-{response.status}")

                if response.status >= 400:
                    code = (
                        payload.get("error", "unauthorized")
                        if isinstance(payload, dict)
                        else "unauthorized"
                    )
                    raise TossAuthError(str(code))

                token = str(payload["access_token"])
                expires_in = float(payload["expires_in"])
                self._token = token
                self._expires_at = self._clock() + max(
                    expires_in - _EXPIRY_SAFETY_MARGIN_SECONDS, 0.0
                )
                return token
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TossApiError(None, "auth-connection-error") from err
