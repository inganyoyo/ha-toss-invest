from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Generator
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)

from custom_components.toss_invest.api import TossApiError, TossAuthError, TossRateLimitError
from custom_components.toss_invest.config_flow import (
    CONF_ACCOUNT_SEQ,
    CONF_ALERT_COOLDOWN,
    CONF_DAILY_MOVE_THRESHOLD,
    CONF_GAIN_COLOR,
    CONF_HOLDINGS_INTERVAL,
    CONF_OPEN_PRICE_INTERVAL,
    _compute_unique_id,
)
from custom_components.toss_invest.const import DOMAIN
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET

INTEGRATION_DIR = pathlib.Path("custom_components/toss_invest")

SANITIZED_ACCOUNTS = [
    {"accountNo": "sanitized-account", "accountType": "BROKERAGE"},
    {"accountNo": "sanitized-account-2", "accountType": "BROKERAGE"},
]

# An unrelated worktree's editable install of this same package registers a meta
# path finder that injects a non-existent placeholder into `sys.path` and into
# the `custom_components` namespace package's `__path__`. Home Assistant's
# integration loader crashes iterating that placeholder, so strip it before any
# test in this module exercises the config entries flow manager.
_STALE_PATH_PLACEHOLDER = "__editable__.toss_invest-0.1.0.finder.__path_hook__"


@pytest.fixture(autouse=True)
def _sanitize_custom_components_namespace() -> None:
    if _STALE_PATH_PLACEHOLDER in sys.path:
        sys.path.remove(_STALE_PATH_PLACEHOLDER)
    module = sys.modules.get("custom_components")
    if module is not None and hasattr(module, "__path__"):
        module.__path__ = [p for p in module.__path__ if pathlib.Path(p).is_dir()]


def _make_mock_client(
    *,
    validate_error: Exception | None = None,
    accounts: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    client = AsyncMock()
    if validate_error is not None:
        client.async_validate.side_effect = validate_error
    else:
        client.async_validate.return_value = None
    client.async_get_accounts.return_value = SANITIZED_ACCOUNTS if accounts is None else accounts
    return client


@pytest.fixture
def mock_client() -> Generator[AsyncMock, None, None]:
    client = _make_mock_client()
    with patch(
        "custom_components.toss_invest.config_flow.TossInvestClient",
        return_value=client,
    ):
        yield client


@pytest.fixture
def patched_client() -> Generator[Any, None, None]:
    """Yield the mock target so individual tests can configure return/side effects."""
    with patch("custom_components.toss_invest.config_flow.TossInvestClient") as target:
        yield target


def _existing_entry(hass: Any) -> MockConfigEntry:
    unique_id = _compute_unique_id("public-id", "sanitized-account")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={
            CONF_CLIENT_ID: "public-id",
            CONF_CLIENT_SECRET: "fake-secret",
            CONF_ACCOUNT_SEQ: "sanitized-account",
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# User flow: happy path and selectors
# ---------------------------------------------------------------------------


async def test_user_flow_selects_account(hass: Any, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
    )
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_seq": "sanitized-account"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["account_seq"] == "sanitized-account"
    assert result["data"]["client_id"] == "public-id"
    assert result["data"]["client_secret"] == "fake-secret"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == _compute_unique_id("public-id", "sanitized-account")


async def test_secret_field_uses_password_selector(hass: Any) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    schema = result["data_schema"].schema
    secret_selector = next(value for key, value in schema.items() if str(key) == CONF_CLIENT_SECRET)
    assert isinstance(secret_selector, selector.TextSelector)
    assert secret_selector.config["type"] == selector.TextSelectorType.PASSWORD


async def test_account_step_uses_select_selector_with_masked_labels(
    hass: Any, mock_client: AsyncMock
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
    )
    schema = result["data_schema"].schema
    account_selector = next(value for key, value in schema.items() if str(key) == CONF_ACCOUNT_SEQ)
    assert isinstance(account_selector, selector.SelectSelector)
    options = cast("list[selector.SelectOptionDict]", account_selector.config["options"])
    assert options[0]["value"] == "sanitized-account"
    assert "sanitized-account" not in options[0]["label"]
    assert options[0]["label"].endswith("ount")  # last 4 chars of accountNo


async def test_account_label_masks_short_account_numbers(hass: Any) -> None:
    short_client = _make_mock_client(accounts=[{"accountNo": "12", "accountType": "CMA"}])
    with patch(
        "custom_components.toss_invest.config_flow.TossInvestClient",
        return_value=short_client,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account_seq": "12"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "CMA 12"


# ---------------------------------------------------------------------------
# User flow: invalid auth vs transient errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TossAuthError("invalid_client"), "invalid_auth"),
        (TossApiError("req-1", "server-error-500"), "cannot_connect"),
        (TossRateLimitError(5.0), "cannot_connect"),
        (aiohttp.ClientConnectionError(), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_user_flow_credential_errors(
    hass: Any, patched_client: Any, error: Exception, expected_code: str
) -> None:
    patched_client.return_value = _make_mock_client(validate_error=error)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected_code}


async def test_user_flow_no_accounts_shows_error(hass: Any) -> None:
    empty_client = _make_mock_client(accounts=[])
    with patch(
        "custom_components.toss_invest.config_flow.TossInvestClient",
        return_value=empty_client,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
        )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_accounts"}


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------


async def test_duplicate_account_aborts_already_configured(
    hass: Any, mock_client: AsyncMock
) -> None:
    _existing_entry(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_seq": "sanitized-account"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_different_account_on_same_credentials_is_not_a_duplicate(
    hass: Any, mock_client: AsyncMock
) -> None:
    _existing_entry(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"account_seq": "sanitized-account-2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Reauthentication
# ---------------------------------------------------------------------------


async def test_reauth_flow_preserves_unique_id_and_reloads(
    hass: Any, mock_client: AsyncMock
) -> None:
    entry = _existing_entry(hass)
    original_unique_id = entry.unique_id

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": "public-id", "client_secret": "new-secret"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == original_unique_id
    assert entry.data[CONF_CLIENT_SECRET] == "new-secret"
    assert entry.data[CONF_ACCOUNT_SEQ] == "sanitized-account"


async def test_reauth_flow_prefills_client_id(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    schema = result["data_schema"].schema
    client_id_key = next(key for key in schema if str(key) == CONF_CLIENT_ID)
    assert client_id_key.default() == "public-id"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TossAuthError("invalid_client"), "invalid_auth"),
        (TossApiError("req-1", "server-error-500"), "cannot_connect"),
    ],
)
async def test_reauth_flow_credential_errors(
    hass: Any, patched_client: Any, error: Exception, expected_code: str
) -> None:
    entry = _existing_entry(hass)
    patched_client.return_value = _make_mock_client(validate_error=error)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": "public-id", "client_secret": "bad-secret"},
    )
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": expected_code}


async def test_reauth_flow_unique_id_mismatch_aborts(hass: Any, mock_client: AsyncMock) -> None:
    entry = _existing_entry(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"client_id": "a-different-client-id", "client_secret": "new-secret"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_CLIENT_ID] == "public-id"


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_shows_bounded_defaults(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    schema = result["data_schema"].schema
    open_price_selector = next(
        value for key, value in schema.items() if str(key) == CONF_OPEN_PRICE_INTERVAL
    )
    assert isinstance(open_price_selector, selector.NumberSelector)
    assert open_price_selector.config["min"] == 10
    assert open_price_selector.config["max"] == 300


async def test_options_flow_creates_entry_with_defaults(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_OPEN_PRICE_INTERVAL: 30,
            CONF_HOLDINGS_INTERVAL: 300,
            "closed_price_interval": 600,
            "reference_interval": 1800,
            "candle_lookback": 252,
            "max_retries": 3,
            "request_timeout": 10,
            "enable_buying_power": False,
            "enable_rankings": False,
            CONF_GAIN_COLOR: [211, 47, 47],
            "loss_color": [25, 118, 210],
            "neutral_color": [158, 158, 158],
            "border_color": [158, 158, 158],
            "glow_color": [255, 193, 7],
            "include_monetary_alert_payloads": False,
            CONF_ALERT_COOLDOWN: 3600,
            "stock_warning_alerts_enabled": True,
            "stale_data_alerts_enabled": True,
            "api_failure_alerts_enabled": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_OPEN_PRICE_INTERVAL] == 30
    assert result["data"][CONF_DAILY_MOVE_THRESHOLD] is None
    assert entry.options[CONF_OPEN_PRICE_INTERVAL] == 30


async def test_options_flow_sets_alert_threshold_when_provided(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_OPEN_PRICE_INTERVAL: 30,
            CONF_HOLDINGS_INTERVAL: 300,
            "closed_price_interval": 600,
            "reference_interval": 1800,
            "candle_lookback": 252,
            "max_retries": 3,
            "request_timeout": 10,
            CONF_DAILY_MOVE_THRESHOLD: 5.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DAILY_MOVE_THRESHOLD] == 5.0


async def test_options_flow_prefills_previous_values(hass: Any) -> None:
    entry = _existing_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_OPEN_PRICE_INTERVAL: 45, CONF_DAILY_MOVE_THRESHOLD: 7.5}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    open_price_key = next(key for key in schema if str(key) == CONF_OPEN_PRICE_INTERVAL)
    assert open_price_key.default() == 45
    threshold_key = next(key for key in schema if str(key) == CONF_DAILY_MOVE_THRESHOLD)
    assert threshold_key.description == {"suggested_value": 7.5}


async def test_options_flow_rejects_out_of_range_interval(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData) as excinfo:
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_OPEN_PRICE_INTERVAL: 3000,
                CONF_HOLDINGS_INTERVAL: 300,
                "closed_price_interval": 600,
                "reference_interval": 1800,
                "candle_lookback": 252,
                "max_retries": 3,
                "request_timeout": 10,
            },
        )
    assert CONF_OPEN_PRICE_INTERVAL in excinfo.value.schema_errors
    # The bound is enforced server-side; the entry's stored options are untouched.
    assert entry.options == {}


async def test_options_flow_rejects_invalid_color(hass: Any) -> None:
    entry = _existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData) as excinfo:
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_OPEN_PRICE_INTERVAL: 30,
                CONF_HOLDINGS_INTERVAL: 300,
                "closed_price_interval": 600,
                "reference_interval": 1800,
                "candle_lookback": 252,
                "max_retries": 3,
                "request_timeout": 10,
                CONF_GAIN_COLOR: [999, 0, 0],
            },
        )
    assert CONF_GAIN_COLOR in excinfo.value.schema_errors


async def test_options_flow_clears_threshold_when_left_blank(hass: Any) -> None:
    entry = _existing_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_DAILY_MOVE_THRESHOLD: 5.0})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_OPEN_PRICE_INTERVAL: 30,
            CONF_HOLDINGS_INTERVAL: 300,
            "closed_price_interval": 600,
            "reference_interval": 1800,
            "candle_lookback": 252,
            "max_retries": 3,
            "request_timeout": 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DAILY_MOVE_THRESHOLD] is None


# ---------------------------------------------------------------------------
# Secrets and account identifiers must never be logged
# ---------------------------------------------------------------------------


def test_source_never_logs_secrets_or_account_ids() -> None:
    text = (INTEGRATION_DIR / "config_flow.py").read_text()
    assert "_LOGGER" not in text
    assert "logging.getLogger" not in text
    for needle in ("print(", ".debug(", ".info(", ".warning(", ".error(", ".exception("):
        assert needle not in text


# ---------------------------------------------------------------------------
# Translations: complete and key-consistent
# ---------------------------------------------------------------------------


def _flatten_keys(data: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            keys.add(path)
            keys |= _flatten_keys(value, path)
    return keys


@pytest.mark.parametrize(
    "filename",
    ["strings.json", "translations/en.json", "translations/ko.json"],
)
def test_translation_files_are_valid_json(filename: str) -> None:
    json.loads((INTEGRATION_DIR / filename).read_text())


def test_translation_files_share_identical_keys() -> None:
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text())
    en = json.loads((INTEGRATION_DIR / "translations/en.json").read_text())
    ko = json.loads((INTEGRATION_DIR / "translations/ko.json").read_text())

    strings_keys = _flatten_keys(strings)
    assert strings_keys == _flatten_keys(en)
    assert strings_keys == _flatten_keys(ko)


def test_translations_cover_all_error_and_abort_codes_used_by_the_flow() -> None:
    en = json.loads((INTEGRATION_DIR / "translations/en.json").read_text())
    config_errors = set(en["config"]["error"])
    config_aborts = set(en["config"]["abort"])

    assert {"invalid_auth", "cannot_connect", "no_accounts"} <= config_errors
    assert {"already_configured", "reauth_successful", "unique_id_mismatch"} <= config_aborts


def test_translations_never_contain_placeholder_secrets() -> None:
    for filename in ("strings.json", "translations/en.json", "translations/ko.json"):
        text = (INTEGRATION_DIR / filename).read_text().lower()
        assert "fake-secret" not in text
        assert "sanitized-account" not in text
