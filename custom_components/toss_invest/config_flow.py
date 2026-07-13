"""Config and options flow for the Toss Invest integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TossApiError, TossAuthError, TossInvestClient, TossRateLimitError
from .const import DOMAIN

CONF_ACCOUNT_SEQ = "account_seq"

CONF_OPEN_PRICE_INTERVAL = "open_price_interval"
CONF_HOLDINGS_INTERVAL = "holdings_interval"
CONF_CLOSED_PRICE_INTERVAL = "closed_price_interval"
CONF_REFERENCE_INTERVAL = "reference_interval"
CONF_CANDLE_LOOKBACK = "candle_lookback"
CONF_MAX_RETRIES = "max_retries"
CONF_REQUEST_TIMEOUT = "request_timeout"
CONF_ENABLE_BUYING_POWER = "enable_buying_power"
CONF_ENABLE_RANKINGS = "enable_rankings"
CONF_GAIN_COLOR = "gain_color"
CONF_LOSS_COLOR = "loss_color"
CONF_NEUTRAL_COLOR = "neutral_color"
CONF_BORDER_COLOR = "border_color"
CONF_GLOW_COLOR = "glow_color"
CONF_INCLUDE_MONETARY_ALERT_PAYLOADS = "include_monetary_alert_payloads"
CONF_ALERT_COOLDOWN = "alert_cooldown"
CONF_DAILY_MOVE_THRESHOLD = "daily_move_threshold"
CONF_TOTAL_RETURN_THRESHOLD = "total_return_threshold"
CONF_PORTFOLIO_DAILY_THRESHOLD = "portfolio_daily_threshold"
CONF_NEAR_HIGH_THRESHOLD = "near_high_threshold"
CONF_NEAR_LOW_THRESHOLD = "near_low_threshold"
CONF_DRAWDOWN_THRESHOLD = "drawdown_threshold"
CONF_VOLUME_SPIKE_THRESHOLD = "volume_spike_threshold"
CONF_STOCK_WARNING_ALERTS = "stock_warning_alerts_enabled"
CONF_STALE_DATA_ALERTS = "stale_data_alerts_enabled"
CONF_API_FAILURE_ALERTS = "api_failure_alerts_enabled"

DEFAULT_OPEN_PRICE_INTERVAL = 30
DEFAULT_HOLDINGS_INTERVAL = 300
DEFAULT_CLOSED_PRICE_INTERVAL = 600
DEFAULT_REFERENCE_INTERVAL = 1800
DEFAULT_CANDLE_LOOKBACK = 252
DEFAULT_MAX_RETRIES = 3
DEFAULT_REQUEST_TIMEOUT = 10
DEFAULT_ALERT_COOLDOWN = 3600
DEFAULT_GAIN_COLOR = [211, 47, 47]
DEFAULT_LOSS_COLOR = [25, 118, 210]
DEFAULT_NEUTRAL_COLOR = [158, 158, 158]
DEFAULT_BORDER_COLOR = [158, 158, 158]
DEFAULT_GLOW_COLOR = [255, 193, 7]

_THRESHOLD_KEYS = (
    CONF_DAILY_MOVE_THRESHOLD,
    CONF_TOTAL_RETURN_THRESHOLD,
    CONF_PORTFOLIO_DAILY_THRESHOLD,
    CONF_NEAR_HIGH_THRESHOLD,
    CONF_NEAR_LOW_THRESHOLD,
    CONF_DRAWDOWN_THRESHOLD,
    CONF_VOLUME_SPIKE_THRESHOLD,
)


def _credentials_schema(*, client_id: str | None = None) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CLIENT_ID, default=client_id): TextSelector(),
            vol.Required(CONF_CLIENT_SECRET): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _account_label(account: Mapping[str, Any]) -> str:
    account_no = str(account["accountNo"])
    account_type = str(account.get("accountType", ""))
    masked = f"••••{account_no[-4:]}" if len(account_no) >= 4 else account_no
    return f"{account_type} {masked}".strip()


def _account_options(accounts: list[dict[str, Any]]) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=str(account["accountNo"]), label=_account_label(account))
        for account in accounts
    ]


def _account_schema(accounts: list[dict[str, Any]]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACCOUNT_SEQ): SelectSelector(
                SelectSelectorConfig(options=_account_options(accounts))
            )
        }
    )


def _compute_unique_id(client_id: str, account_seq: str) -> str:
    return sha256(f"{client_id}:{account_seq}".encode()).hexdigest()


class TossInvestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Toss Invest config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._accounts: list[dict[str, Any]] = []
        self._reauth_account_seq: str | None = None

    async def _async_validate_and_fetch_accounts(
        self, client_id: str, client_secret: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        session = async_get_clientsession(self.hass)
        client = TossInvestClient(session, client_id, client_secret)
        try:
            await client.async_validate()
            accounts = await client.async_get_accounts()
        except TossAuthError:
            return [], "invalid_auth"
        except TossApiError, TossRateLimitError, aiohttp.ClientError, asyncio.TimeoutError:
            return [], "cannot_connect"
        if not accounts:
            return [], "no_accounts"
        return accounts, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]
            accounts, error = await self._async_validate_and_fetch_accounts(
                client_id, client_secret
            )
            if error is None:
                self._credentials = {
                    CONF_CLIENT_ID: client_id,
                    CONF_CLIENT_SECRET: client_secret,
                }
                self._accounts = accounts
                return await self.async_step_account()
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=_credentials_schema(), errors=errors
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            account_seq = user_input[CONF_ACCOUNT_SEQ]
            unique_id = _compute_unique_id(self._credentials[CONF_CLIENT_ID], account_seq)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_account_label(
                    next(a for a in self._accounts if str(a["accountNo"]) == account_seq)
                ),
                data={**self._credentials, CONF_ACCOUNT_SEQ: account_seq},
            )

        return self.async_show_form(step_id="account", data_schema=_account_schema(self._accounts))

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._reauth_account_seq = entry_data[CONF_ACCOUNT_SEQ]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]
            _, error = await self._async_validate_and_fetch_accounts(client_id, client_secret)
            if error is None:
                account_seq = self._reauth_account_seq
                assert account_seq is not None
                unique_id = _compute_unique_id(client_id, account_seq)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                        CONF_ACCOUNT_SEQ: account_seq,
                    },
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(client_id=reauth_entry.data[CONF_CLIENT_ID]),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TossInvestOptionsFlow:
        return TossInvestOptionsFlow()


def _bounded_number(
    *, min_value: float, max_value: float, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(min=min_value, max=max_value, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _options_schema(current: Mapping[str, Any]) -> vol.Schema:
    def get(key: str, default: Any) -> Any:
        return current.get(key, default)

    schema: dict[Any, Any] = {
        vol.Optional(
            CONF_OPEN_PRICE_INTERVAL,
            default=get(CONF_OPEN_PRICE_INTERVAL, DEFAULT_OPEN_PRICE_INTERVAL),
        ): _bounded_number(min_value=10, max_value=300, unit="s"),
        vol.Optional(
            CONF_HOLDINGS_INTERVAL,
            default=get(CONF_HOLDINGS_INTERVAL, DEFAULT_HOLDINGS_INTERVAL),
        ): _bounded_number(min_value=30, max_value=3600, unit="s"),
        vol.Optional(
            CONF_CLOSED_PRICE_INTERVAL,
            default=get(CONF_CLOSED_PRICE_INTERVAL, DEFAULT_CLOSED_PRICE_INTERVAL),
        ): _bounded_number(min_value=60, max_value=3600, unit="s"),
        vol.Optional(
            CONF_REFERENCE_INTERVAL,
            default=get(CONF_REFERENCE_INTERVAL, DEFAULT_REFERENCE_INTERVAL),
        ): _bounded_number(min_value=300, max_value=21600, unit="s"),
        vol.Optional(
            CONF_CANDLE_LOOKBACK,
            default=get(CONF_CANDLE_LOOKBACK, DEFAULT_CANDLE_LOOKBACK),
        ): _bounded_number(min_value=20, max_value=500),
        vol.Optional(
            CONF_MAX_RETRIES,
            default=get(CONF_MAX_RETRIES, DEFAULT_MAX_RETRIES),
        ): _bounded_number(min_value=0, max_value=5),
        vol.Optional(
            CONF_REQUEST_TIMEOUT,
            default=get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
        ): _bounded_number(min_value=5, max_value=60, unit="s"),
        vol.Optional(
            CONF_ENABLE_BUYING_POWER,
            default=get(CONF_ENABLE_BUYING_POWER, False),
        ): BooleanSelector(),
        vol.Optional(
            CONF_ENABLE_RANKINGS,
            default=get(CONF_ENABLE_RANKINGS, False),
        ): BooleanSelector(),
        vol.Optional(
            CONF_GAIN_COLOR, default=get(CONF_GAIN_COLOR, DEFAULT_GAIN_COLOR)
        ): ColorRGBSelector(),
        vol.Optional(
            CONF_LOSS_COLOR, default=get(CONF_LOSS_COLOR, DEFAULT_LOSS_COLOR)
        ): ColorRGBSelector(),
        vol.Optional(
            CONF_NEUTRAL_COLOR, default=get(CONF_NEUTRAL_COLOR, DEFAULT_NEUTRAL_COLOR)
        ): ColorRGBSelector(),
        vol.Optional(
            CONF_BORDER_COLOR, default=get(CONF_BORDER_COLOR, DEFAULT_BORDER_COLOR)
        ): ColorRGBSelector(),
        vol.Optional(
            CONF_GLOW_COLOR, default=get(CONF_GLOW_COLOR, DEFAULT_GLOW_COLOR)
        ): ColorRGBSelector(),
        vol.Optional(
            CONF_INCLUDE_MONETARY_ALERT_PAYLOADS,
            default=get(CONF_INCLUDE_MONETARY_ALERT_PAYLOADS, False),
        ): BooleanSelector(),
        vol.Optional(
            CONF_ALERT_COOLDOWN,
            default=get(CONF_ALERT_COOLDOWN, DEFAULT_ALERT_COOLDOWN),
        ): _bounded_number(min_value=60, max_value=86400, unit="s"),
        vol.Optional(
            CONF_STOCK_WARNING_ALERTS,
            default=get(CONF_STOCK_WARNING_ALERTS, True),
        ): BooleanSelector(),
        vol.Optional(
            CONF_STALE_DATA_ALERTS,
            default=get(CONF_STALE_DATA_ALERTS, True),
        ): BooleanSelector(),
        vol.Optional(
            CONF_API_FAILURE_ALERTS,
            default=get(CONF_API_FAILURE_ALERTS, True),
        ): BooleanSelector(),
        vol.Optional(
            CONF_DAILY_MOVE_THRESHOLD,
            description={"suggested_value": current.get(CONF_DAILY_MOVE_THRESHOLD)},
        ): _bounded_number(min_value=0, max_value=100, unit="%"),
        vol.Optional(
            CONF_TOTAL_RETURN_THRESHOLD,
            description={"suggested_value": current.get(CONF_TOTAL_RETURN_THRESHOLD)},
        ): _bounded_number(min_value=-100, max_value=1000, unit="%"),
        vol.Optional(
            CONF_PORTFOLIO_DAILY_THRESHOLD,
            description={"suggested_value": current.get(CONF_PORTFOLIO_DAILY_THRESHOLD)},
        ): _bounded_number(min_value=-100, max_value=100, unit="%"),
        vol.Optional(
            CONF_NEAR_HIGH_THRESHOLD,
            description={"suggested_value": current.get(CONF_NEAR_HIGH_THRESHOLD)},
        ): _bounded_number(min_value=0, max_value=100, unit="%"),
        vol.Optional(
            CONF_NEAR_LOW_THRESHOLD,
            description={"suggested_value": current.get(CONF_NEAR_LOW_THRESHOLD)},
        ): _bounded_number(min_value=0, max_value=100, unit="%"),
        vol.Optional(
            CONF_DRAWDOWN_THRESHOLD,
            description={"suggested_value": current.get(CONF_DRAWDOWN_THRESHOLD)},
        ): _bounded_number(min_value=0, max_value=100, unit="%"),
        vol.Optional(
            CONF_VOLUME_SPIKE_THRESHOLD,
            description={"suggested_value": current.get(CONF_VOLUME_SPIKE_THRESHOLD)},
        ): _bounded_number(min_value=0, max_value=1000, unit="%"),
    }
    return vol.Schema(schema)


class TossInvestOptionsFlow(config_entries.OptionsFlow):
    """Handle Toss Invest options.

    Bounds are enforced by the selectors in `_options_schema`. Home Assistant's
    flow manager validates submitted data against the schema from the last
    shown form before this step ever receives it, and surfaces out-of-range
    values as a 400 response with field-level errors on its own.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        current = self.config_entry.options
        if user_input is not None:
            data = dict(user_input)
            for key in _THRESHOLD_KEYS:
                data[key] = user_input.get(key)
            return self.async_create_entry(data=data)

        return self.async_show_form(step_id="init", data_schema=_options_schema(current))
