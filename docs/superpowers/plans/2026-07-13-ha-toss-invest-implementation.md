# Home Assistant Toss Invest Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a read-only, HACS-installable Home Assistant integration that displays Korean and US Toss Securities holdings, derived portfolio metrics, alerts, and a theme-adaptive dashboard.

**Architecture:** A typed async Toss API client feeds independent Home Assistant coordinators for holdings, prices, candles, and market context. Native entities expose stable essential data, optional advanced metrics, privacy and refresh controls, and alert events; dashboard YAML consumes those entities without coupling the API layer to frontend cards.

**Tech Stack:** Python 3.14, Home Assistant 2026.7.2, aiohttp, pytest-homeassistant-custom-component, Ruff, mypy, Docker Compose, HACS validation, Hassfest, Lovelace YAML, GitHub Actions.

## Global Constraints

- Domain and package name: `toss_invest`.
- Production compatibility floor: Home Assistant `2026.7.2`.
- Python compatibility floor: `>=3.14`, matching Home Assistant 2026.7.2.
- Repository: public `inganyoyo/ha-toss-invest`, one directory under `custom_components/`.
- Version 1 is read-only: do not implement order mutation, order history, or conditional-order endpoints.
- Buying power is optional and read-only.
- Support both KR and US markets and preserve KRW and USD source values.
- Use `Decimal` for money and rates; never calculate money with binary floats.
- Default refreshes: open prices 30s, holdings 5m, closed prices 10m, reference data 30m.
- Always honor Toss rate-limit response headers and `Retry-After`.
- Never log or fixture `client_secret`, access tokens, account identifiers, or real portfolio data.
- Privacy mode defaults on and masks dashboard presentation only.
- All user-facing strings require Korean and English translations.
- Follow TDD and commit after every task.

## Managed Agent Allocation

The primary Codex agent is the integration manager: it dispatches bounded tasks, reviews every diff against this plan, runs the acceptance command for each task, and alone decides when a task is accepted. Claude Code and AGY work in separate Orca worktrees; they never edit the same files concurrently.

Execution phases:

1. Claude completes Task 1; the manager reviews and establishes the shared baseline.
2. Claude completes Task 2. After acceptance, Claude takes Task 4 while AGY takes Task 3 in parallel.
3. After Tasks 3–4 merge, Claude completes Tasks 5–6 sequentially.
4. After Task 6 merges, AGY takes Task 7 while Claude takes Task 8 in parallel.
5. After entity interfaces are accepted, AGY completes Task 9 while Claude completes Task 10 in parallel.
6. The manager performs Task 11, requests focused fixes from the agent that owns the defective area, and does not delegate the final production gate.

Every dispatch includes the design spec, this plan, the task number, allowed file paths, required test command, and a prohibition on touching production Home Assistant. An agent commit is evidence for review, not automatic approval.

## File Map

- `custom_components/toss_invest/api/{auth,client,rate_limit}.py`: OAuth, request execution, endpoint methods, throttling.
- `custom_components/toss_invest/{models,calculations,alerts}.py`: immutable domain data, derived metrics, alert evaluation.
- `custom_components/toss_invest/{config_flow,coordinator,__init__}.py`: setup, options, reauth, schedules, runtime lifecycle.
- `custom_components/toss_invest/{entity,sensor,binary_sensor,button,switch,event}.py`: native Home Assistant entity platforms.
- `custom_components/toss_invest/translations/{en,ko}.json`, `strings.json`: UI copy.
- `tests/`: sanitized API fixtures and focused tests mirroring integration modules.
- `dev/`: disposable Home Assistant and mock API environment.
- `dashboards/`: native and enhanced `Toss 주식` view examples.
- `blueprints/automation/toss_invest_alert.yaml`: user-selected notification routing.
- `.github/workflows/`: test, validation, compatibility, and release checks.

---

### Task 1: Repository Foundation and Disposable Home Assistant

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `hacs.json`, `custom_components/toss_invest/manifest.json`
- Create: `custom_components/toss_invest/{__init__,const}.py`
- Create: `dev/{compose.yaml,configuration.yaml,secrets.yaml.example}`
- Create: `tests/{__init__,conftest}.py`, `tests/test_manifest.py`

**Interfaces:**
- Produces: `DOMAIN`, platform list, typed pytest environment, Home Assistant at `http://localhost:8123`.

- [ ] **Step 1: Write the failing foundation test**

```python
# tests/test_manifest.py
import json
from pathlib import Path

def test_manifest_is_read_only_and_config_flow_enabled() -> None:
    manifest = json.loads(Path("custom_components/toss_invest/manifest.json").read_text())
    assert manifest["domain"] == "toss_invest"
    assert manifest["config_flow"] is True
    assert "iot_class" in manifest
    assert not any("order" in item for item in manifest.get("requirements", []))
```

- [ ] **Step 2: Run it and confirm the missing-file failure**

Run: `pytest tests/test_manifest.py -q`
Expected: FAIL with `FileNotFoundError: custom_components/toss_invest/manifest.json`.

- [ ] **Step 3: Add the minimal package, tooling, and container**

```python
# custom_components/toss_invest/const.py
from datetime import timedelta

DOMAIN = "toss_invest"
PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "event"]
DEFAULT_OPEN_PRICE_INTERVAL = timedelta(seconds=30)
DEFAULT_HOLDINGS_INTERVAL = timedelta(minutes=5)
DEFAULT_CLOSED_PRICE_INTERVAL = timedelta(minutes=10)
DEFAULT_REFERENCE_INTERVAL = timedelta(minutes=30)
```

```json
// custom_components/toss_invest/manifest.json
{
  "domain": "toss_invest",
  "name": "Toss Invest",
  "codeowners": ["@inganyoyo"],
  "config_flow": true,
  "documentation": "https://github.com/inganyoyo/ha-toss-invest",
  "issue_tracker": "https://github.com/inganyoyo/ha-toss-invest/issues",
  "iot_class": "cloud_polling",
  "version": "0.1.0"
}
```

Create `pyproject.toml` with Python `>=3.14`, pytest, pytest-asyncio, pytest-homeassistant-custom-component, aioresponses, Ruff, and mypy; configure Ruff line length 100 and pytest `asyncio_mode = "auto"`. Create Compose service `homeassistant` using `ghcr.io/home-assistant/home-assistant:2026.7.2`, bind-mount `../custom_components/toss_invest`, and expose `8123:8123`. Ignore `.venv`, `.pytest_cache`, `.mypy_cache`, `dev/config`, `.env`, and secrets.

- [ ] **Step 4: Verify package and container configuration**

Run: `pytest tests/test_manifest.py -q && docker compose -f dev/compose.yaml config -q`
Expected: test PASS and Compose exits 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore hacs.json custom_components dev tests
git commit -m "chore: scaffold Toss Invest integration"
```

### Task 2: Typed Domain Models and Sanitized Fixtures

**Files:**
- Create: `custom_components/toss_invest/models.py`
- Create: `tests/fixtures/{accounts,holdings,prices,candles,warnings,market}.json`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `MoneyByCurrency`, `Holding`, `HoldingsOverview`, `Candle`, `MarketSnapshot`, `StockWarning`, and `parse_decimal(value, field)`.

- [ ] **Step 1: Specify decimal and unknown-enum behavior**

```python
# tests/test_models.py
from decimal import Decimal
from custom_components.toss_invest.models import Holding, parse_decimal

def test_holding_preserves_decimal_and_unknown_market() -> None:
    holding = Holding.from_api({
        "symbol": "TEST", "name": "Sanitized Corp", "marketCountry": "FUTURE",
        "currency": "USD", "quantity": "1.25", "lastPrice": "10.10",
        "averagePurchasePrice": "8.00", "marketValue": {"purchaseAmount": "10", "amount": "12.625", "amountAfterCost": "12.50"},
        "profitLoss": {"amount": "2.625", "amountAfterCost": "2.50", "rate": "0.2625", "rateAfterCost": "0.25"},
        "dailyProfitLoss": {"amount": "0.50", "rate": "0.04"}, "cost": {"commission": "0.125", "tax": None},
    })
    assert holding.quantity == Decimal("1.25")
    assert holding.market_country == "FUTURE"
    assert parse_decimal(None, "optional", optional=True) is None
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_models.py -q`
Expected: FAIL importing `custom_components.toss_invest.models`.

- [ ] **Step 3: Implement immutable parsers**

```python
# custom_components/toss_invest/models.py
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, overload

class TossDataError(ValueError):
    pass

@overload
def parse_decimal(value: Any, field: str, *, optional: bool = False) -> Decimal: ...
@overload
def parse_decimal(value: Any, field: str, *, optional: bool) -> Decimal | None: ...

def parse_decimal(value: Any, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as err:
        raise TossDataError(f"Invalid decimal field: {field}") from err

@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    name: str
    market_country: str
    currency: str
    quantity: Decimal
    last_price: Decimal
    average_purchase_price: Decimal
    market_value: Decimal
    profit_loss_rate_after_cost: Decimal
    daily_profit_loss_rate: Decimal

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Holding":
        return cls(
            symbol=str(data["symbol"]), name=str(data["name"]),
            market_country=str(data["marketCountry"]), currency=str(data["currency"]),
            quantity=parse_decimal(data["quantity"], "quantity"),
            last_price=parse_decimal(data["lastPrice"], "lastPrice"),
            average_purchase_price=parse_decimal(data["averagePurchasePrice"], "averagePurchasePrice"),
            market_value=parse_decimal(data["marketValue"]["amount"], "marketValue.amount"),
            profit_loss_rate_after_cost=parse_decimal(data["profitLoss"]["rateAfterCost"], "profitLoss.rateAfterCost"),
            daily_profit_loss_rate=parse_decimal(data["dailyProfitLoss"]["rate"], "dailyProfitLoss.rate"),
        )
```

Expand the same explicit parsing pattern for the other produced dataclasses. Store only fabricated symbols and values in fixtures; add `tests/test_fixtures_are_sanitized.py` rejecting keys matching `client_secret|access_token|accountSeq`.

- [ ] **Step 4: Run model and fixture tests**

Run: `pytest tests/test_models.py tests/test_fixtures_are_sanitized.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/models.py tests
git commit -m "feat: add typed Toss domain models"
```

### Task 3: Portfolio Calculations

**Files:**
- Create: `custom_components/toss_invest/calculations.py`
- Create: `tests/test_calculations.py`

**Interfaces:**
- Consumes: `Holding`, `Candle`.
- Produces: `calculate_allocation`, `calculate_concentration`, `calculate_period_return`, `calculate_drawdown`, `calculate_volatility`, `calculate_volume_ratio`.

- [ ] **Step 1: Write exact calculation examples**

```python
# tests/test_calculations.py
from decimal import Decimal
from custom_components.toss_invest.calculations import concentration, period_return

def test_concentration_and_return_are_decimal() -> None:
    weights = [Decimal("60"), Decimal("25"), Decimal("15")]
    assert concentration(weights, 1) == Decimal("0.6")
    assert concentration(weights, 3) == Decimal("1")
    assert period_return(Decimal("100"), Decimal("125")) == Decimal("0.25")
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_calculations.py -q`
Expected: FAIL importing `calculations`.

- [ ] **Step 3: Implement pure functions with defined empty behavior**

```python
# custom_components/toss_invest/calculations.py
from decimal import Decimal
from statistics import stdev

ZERO = Decimal("0")

def period_return(start: Decimal, end: Decimal) -> Decimal | None:
    return None if start == ZERO else (end / start) - Decimal("1")

def concentration(values: list[Decimal], count: int) -> Decimal:
    total = sum(values, ZERO)
    return ZERO if total == ZERO else sum(sorted(values, reverse=True)[:count], ZERO) / total

def drawdown(high: Decimal, current: Decimal) -> Decimal | None:
    return None if high == ZERO else (current / high) - Decimal("1")

def volatility(closes: list[Decimal]) -> Decimal | None:
    returns = [float(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] != ZERO]
    return None if len(returns) < 2 else Decimal(str(stdev(returns))) * Decimal("252").sqrt()
```

Add allocation and trailing-volume ratio using the same zero-safe rules. Tests must cover zero values, one candle, missing windows, and KRW-converted mixed-currency weights.

- [ ] **Step 4: Verify calculation suite**

Run: `pytest tests/test_calculations.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/calculations.py tests/test_calculations.py
git commit -m "feat: calculate portfolio performance metrics"
```

### Task 4: OAuth, Rate Limiting, and Read-only API Client

**Files:**
- Create: `custom_components/toss_invest/api/{__init__,auth,rate_limit,client}.py`
- Create: `tests/api/{__init__,test_auth,test_rate_limit,test_client}.py`

**Interfaces:**
- Produces: `TossInvestClient(session, client_id, client_secret)`, `async_validate()`, `async_get_accounts()`, `async_get_holdings(account_seq)`, `async_get_prices(symbols)`, `async_get_candles(symbol, count, before)`, `async_get_warnings(symbol)`, `async_get_exchange_rate()`, `async_get_market_calendar(country)`, `async_get_market_indicators(symbols)`, `async_get_investor_trading(symbol)`, `async_get_rankings(...)`, `async_get_buying_power(account_seq)`.
- Raises: `TossAuthError`, `TossRateLimitError(retry_after)`, `TossApiError(request_id, code)`.

- [ ] **Step 1: Test token caching, headers, and 429**

```python
# tests/api/test_client.py
async def test_holdings_uses_cached_token_account_header_and_decimal_fixture(client, responses):
    responses.post("https://openapi.tossinvest.com/oauth2/token", payload={"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
    responses.get("https://openapi.tossinvest.com/api/v1/holdings", payload=load_fixture("holdings.json"))
    first = await client.async_get_holdings("sanitized-account")
    second = await client.async_get_holdings("sanitized-account")
    assert first == second
    assert responses.requests[0][1].kwargs["headers"]["X-Tossinvest-Account"] == "sanitized-account"
    assert len([r for r in responses.requests if r[0] == "POST"]) == 1
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/api -q`
Expected: FAIL importing API modules.

- [ ] **Step 3: Implement the shared request path**

```python
# custom_components/toss_invest/api/client.py
class TossAuthError(Exception):
    """Credentials were permanently rejected."""

class TossRateLimitError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Rate limited for {retry_after} seconds")
        self.retry_after = retry_after

class TossApiError(Exception):
    def __init__(self, request_id: str | None, code: str) -> None:
        super().__init__(f"Toss API error {code} (request {request_id or 'unknown'})")
        self.request_id = request_id
        self.code = code

class TossInvestClient:
    BASE_URL = "https://openapi.tossinvest.com"

    async def _request(self, method: str, path: str, *, group: str, account_seq: str | None = None, params: dict | None = None) -> dict:
        await self._limiter.async_wait(group)
        token = await self._tokens.async_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = account_seq
        async with self._session.request(method, f"{self.BASE_URL}{path}", headers=headers, params=params, timeout=self._timeout) as response:
            await self._limiter.async_update(group, response.headers)
            payload = await response.json(content_type=None)
            if response.status == 401:
                self._tokens.invalidate()
                raise TossAuthError(payload.get("error", {}).get("code", "unauthorized"))
            if response.status == 429:
                raise TossRateLimitError(float(response.headers.get("Retry-After", "1")))
            if response.status >= 400:
                error = payload.get("error", {})
                raise TossApiError(error.get("requestId"), error.get("code", "api-error"))
            return payload["result"]
```

Implement token expiry with a 60-second safety margin and an `asyncio.Lock`. Implement one lock and `next_allowed` monotonic timestamp per API group; update runtime limits from `X-RateLimit-*`. Endpoint methods only call GET endpoints listed in the design. Add a source scan test rejecting `/orders`, `/conditional-orders`, `POST /api`, `DELETE /api`, and secrets in log calls.

- [ ] **Step 4: Verify API suite**

Run: `pytest tests/api -q`
Expected: all PASS, including 401 invalidation, 429 `Retry-After`, unknown fields, batching, and no mutation paths.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/api tests/api
git commit -m "feat: add rate-aware read-only Toss API client"
```

### Task 5: Config Flow, Account Selection, Options, and Reauthentication

**Files:**
- Create: `custom_components/toss_invest/config_flow.py`, `custom_components/toss_invest/strings.json`
- Create: `custom_components/toss_invest/translations/{en,ko}.json`
- Create: `tests/test_config_flow.py`

**Interfaces:**
- Stores config data keys `client_id`, `client_secret`, `account_seq`.
- Stores options keys for four intervals, lookback, retries, timeout, optional groups, colors, privacy, cooldown, and alert thresholds.

- [ ] **Step 1: Test credentials-to-account flow and unique entry**

```python
# tests/test_config_flow.py
async def test_user_flow_selects_account(hass, mock_client):
    result = await hass.config_entries.flow.async_init("toss_invest", context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"client_id": "public-id", "client_secret": "fake-secret"})
    assert result["step_id"] == "account"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"account_seq": "sanitized-account"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["account_seq"] == "sanitized-account"
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_config_flow.py -q`
Expected: FAIL because the config flow is absent.

- [ ] **Step 3: Implement selector-based multi-step flow**

```python
# custom_components/toss_invest/config_flow.py
class TossInvestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._credentials = user_input
            self._accounts = await self._async_accounts(user_input)
            return await self.async_step_account()
        schema = vol.Schema({vol.Required(CONF_CLIENT_ID): TextSelector(), vol.Required(CONF_CLIENT_SECRET): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_account(self, user_input=None):
        if user_input is not None:
            account_seq = user_input[CONF_ACCOUNT_SEQ]
            unique_id = sha256(f"{self._credentials[CONF_CLIENT_ID]}:{account_seq}".encode()).hexdigest()
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._account_title(account_seq), data={**self._credentials, CONF_ACCOUNT_SEQ: account_seq})
        return self.async_show_form(step_id="account", data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_SEQ): SelectSelector(SelectSelectorConfig(options=self._account_options()))}))
```

Add bounded Options Flow selectors exactly matching the design, `async_step_reauth` with `async_update_reload_and_abort`, English/Korean validation errors, and tests for invalid auth, network failure, duplicate account, bounds, unchanged reauth unique ID, and secret password selector.

- [ ] **Step 4: Run full flow coverage**

Run: `pytest tests/test_config_flow.py --cov=custom_components.toss_invest.config_flow --cov-fail-under=100 -q`
Expected: PASS with 100% config-flow coverage.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/config_flow.py custom_components/toss_invest/strings.json custom_components/toss_invest/translations tests/test_config_flow.py
git commit -m "feat: configure Toss accounts through the UI"
```

### Task 6: Runtime Setup and Independent Coordinators

**Files:**
- Modify: `custom_components/toss_invest/__init__.py`
- Create: `custom_components/toss_invest/coordinator.py`
- Create: `tests/test_init.py`, `tests/test_coordinator.py`

**Interfaces:**
- Produces: `TossInvestRuntimeData(client, holdings, prices, reference, alerts, privacy)` stored on typed `ConfigEntry`.
- Coordinator data remains last-good on partial failure and exposes `last_success` and `stale_groups`.

- [ ] **Step 1: Test independent failures and platform forwarding**

```python
# tests/test_coordinator.py
async def test_price_failure_keeps_holdings_and_marks_only_prices_stale(runtime):
    await runtime.holdings.async_refresh()
    runtime.client.async_get_prices.side_effect = TossApiError("request", "temporary")
    await runtime.prices.async_refresh()
    assert runtime.holdings.last_update_success is True
    assert runtime.prices.last_update_success is False
    assert "prices" in runtime.stale_groups
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_init.py tests/test_coordinator.py -q`
Expected: FAIL importing coordinator/runtime types.

- [ ] **Step 3: Implement coordinator boundaries and market-aware rescheduling**

```python
# custom_components/toss_invest/coordinator.py
@dataclass(slots=True)
class TossInvestRuntimeData:
    client: TossInvestClient
    holdings: DataUpdateCoordinator[HoldingsOverview]
    prices: DataUpdateCoordinator[dict[str, PriceQuote]]
    reference: DataUpdateCoordinator[MarketSnapshot]
    stale_groups: set[str] = field(default_factory=set)

class PriceCoordinator(DataUpdateCoordinator[dict[str, PriceQuote]]):
    async def _async_update_data(self) -> dict[str, PriceQuote]:
        try:
            symbols = [item.symbol for item in self._holdings.data.items]
            return await self._client.async_get_prices(symbols)
        except TossAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TossApiError as err:
            raise UpdateFailed(str(err)) from err
```

Create holdings, price, and reference coordinators; calculate price interval from independent KR/US calendar state after every reference update. Setup must perform first refresh, forward all platforms, register the options-update reload listener, and close cleanly on unload. Tests cover 401 reauth, first-refresh failure, concurrent refresh coalescing, dynamic holding symbols, and options reload.

- [ ] **Step 4: Verify runtime tests**

Run: `pytest tests/test_init.py tests/test_coordinator.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/__init__.py custom_components/toss_invest/coordinator.py tests/test_init.py tests/test_coordinator.py
git commit -m "feat: coordinate portfolio and market refreshes"
```

### Task 6B: Advanced Read-only Data Coordinators

**Files:**
- Modify: `custom_components/toss_invest/__init__.py`
- Modify: `custom_components/toss_invest/coordinator.py`
- Modify: `custom_components/toss_invest/api/client.py` only where a missing read-only endpoint wrapper is required
- Create: `tests/test_advanced_coordinator.py`
- Modify: `tests/test_init.py`
- Modify: `tests/api/test_client.py` only for a new read-only wrapper

**Interfaces:**
- Extends `TossInvestRuntimeData` with independent candle, warning, buying-power, market-context, and ranking coordinators.
- Produces Decimal-safe candle history and buying power, parsed warning records, market indicators/investor flows, and bounded KR/US ranking snapshots.
- Every group preserves last-good data and maintains its own `last_success`/`stale_groups` state.

- [ ] **Step 1: Test useful-source coverage and failure isolation**

Cover dynamic holding symbols; no API calls for empty holdings; warning and candle failures isolated from essential holdings/prices; optional buying-power/ranking groups disabled without calls; KRW/USD buying power; KOSPI/KOSDAQ indicators and investor trading; KR/US market amount, gainers, and losers rankings.

- [ ] **Step 2: Implement bounded, rate-safe advanced refreshes**

Use the reference interval for candles, warnings, market context, and rankings, and the holdings interval for buying power. Fetch per-symbol sources with bounded concurrency. Daily candle lookback supports 20–500 trading days by paging the official endpoint in chunks of at most 200, stopping on an empty page, missing/repeated `nextBefore`, or the requested limit. Deduplicate candles by timestamp and preserve newest-first source order.

Warnings and candles are always available for active holdings. Buying power and rankings honor `enable_buying_power` and `enable_rankings`. Rankings request a bounded top 10 for market trading amount, 1-day gainers, and 1-day losers for each of KR and US. Market context includes all eight official Korean indicator symbols plus KOSPI/KOSDAQ daily investor flows. All calls remain GET-only.

Advanced groups are nonessential during initial setup: a transient failure marks only that group stale and does not block the essential holdings/reference/price setup. Permanent authentication failures still trigger Home Assistant reauthentication.

- [ ] **Step 3: Verify and commit**

Run focused/full pytest, Ruff, and mypy. Review the canonical OpenAPI endpoint names, query bounds, and read-only safety before merge.

```bash
git add custom_components/toss_invest/__init__.py custom_components/toss_invest/coordinator.py custom_components/toss_invest/api/client.py tests/test_advanced_coordinator.py tests/test_init.py tests/api/test_client.py
git commit -m "feat: coordinate advanced investment context"
```

### Task 7: Account and Holding Entities

**Files:**
- Create: `custom_components/toss_invest/{entity,sensor,binary_sensor}.py`
- Create: `tests/test_sensor.py`, `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: runtime coordinator data.
- Produces: account and holding devices with stable unique IDs; essential sensors enabled, advanced sensors disabled by default.

- [ ] **Step 1: Test stable devices, Decimal conversion, and dynamic holdings**

```python
# tests/test_sensor.py
async def test_holding_entities_use_stable_ids_and_percent_units(hass, setup_entry):
    state = hass.states.get("sensor.sanitized_corp_total_return")
    assert state.state == "25.0"
    assert state.attributes["unit_of_measurement"] == "%"
    entity = er.async_get(hass).async_get("sensor.sanitized_corp_total_return")
    assert entity.unique_id.endswith("TEST_total_return")
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_sensor.py tests/test_binary_sensor.py -q`
Expected: FAIL because platforms are missing.

- [ ] **Step 3: Implement coordinator entities and descriptions**

```python
# custom_components/toss_invest/entity.py
class TossInvestEntity(CoordinatorEntity, Entity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id: str, account_seq: str, holding: Holding | None = None) -> None:
        super().__init__(coordinator)
        self._holding = holding
        symbol = holding.symbol if holding else None
        key = symbol or "portfolio"
        self._attr_unique_id = f"{entry_id}_{key}_{self.entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}:{key}")},
            name="Toss Invest Portfolio" if symbol is None else self._holding.name,
            manufacturer="Toss Securities",
            via_device=(DOMAIN, f"{entry_id}:portfolio") if symbol else None,
        )
```

Use `SensorEntityDescription` definitions for essential and advanced metrics, `SensorDeviceClass.MONETARY` with native currency, percentage values multiplied by 100 only at the entity boundary, and `EntityCategory.DIAGNOSTIC` for freshness/API state. Use `_unrecorded_attributes` for raw candle attributes per official entity guidance. Implement one warning binary sensor with warning codes/dates as attributes. Reconcile entity additions when coordinator holdings change; sold holdings become unavailable rather than deleted.

- [ ] **Step 4: Verify entity and registry behavior**

Run: `pytest tests/test_sensor.py tests/test_binary_sensor.py -q`
Expected: all PASS, including add/sell/rebuy, disabled defaults, unknown warnings, device links, and unavailable stale dependencies.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/entity.py custom_components/toss_invest/sensor.py custom_components/toss_invest/binary_sensor.py tests/test_sensor.py tests/test_binary_sensor.py
git commit -m "feat: expose portfolio and holding entities"
```

### Task 7C: Advanced Market Context Entity Surface

**Files:**
- Create: `custom_components/toss_invest/market_entities.py`
- Modify: `custom_components/toss_invest/sensor.py`
- Create: `tests/test_market_entities.py`

**Interfaces:**
- Consumes: `runtime.market_context.data: MarketContextSnapshot | None` and `runtime.rankings.data: dict[tuple[str, str], RankingSnapshot] | None` from Task 6B.
- Produces: stable portfolio-device sensors for every official Korean indicator, latest KOSPI/KOSDAQ investor net amounts, and the six optional KR/US ranking snapshots required by the dashboard.

- [ ] **Step 1: Write failing entity-contract tests**

Test exact IDs and values for `sensor.toss_invest_portfolio_market_indicator_kospi`, `sensor.toss_invest_portfolio_market_indicator_kosdaq`, all six Korean bond symbols, and `sensor.toss_invest_portfolio_{kospi,kosdaq}_{individual,foreigner,institution,other_corporation}_net`. Assert investor net equals `buy_amount - sell_amount` as `Decimal`, uses KRW monetary metadata, and becomes unavailable only when `market_context` is stale.

Test that ranking entities are absent when `enable_rankings` is false and, when true, create exactly these six stable IDs:

```text
sensor.toss_invest_portfolio_kr_market_trading_amount
sensor.toss_invest_portfolio_kr_top_gainers
sensor.toss_invest_portfolio_kr_top_losers
sensor.toss_invest_portfolio_us_market_trading_amount
sensor.toss_invest_portfolio_us_top_gainers
sensor.toss_invest_portfolio_us_top_losers
```

Each ranking sensor state is the top symbol (or unknown for an empty ranking), attributes contain the source `ranked_at` and ordered top-10 rows, and the large `rankings` attribute is listed in `_unrecorded_attributes`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/test_market_entities.py -q -p no:cacheprovider`
Expected: FAIL because `market_entities.py` and the sensors do not exist.

- [ ] **Step 3: Implement the bounded market entity module**

Create immutable description tables in `market_entities.py`. Indicator and investor-flow entities use `dependency_groups = ("market_context",)`; ranking entities use `dependency_groups = ("rankings",)`. All entities reuse `TossInvestEntity` so IDs remain entry-ID based and account identifiers are never exposed. Preserve public market numeric data as `Decimal`. Ranking rows are converted to JSON-safe string attributes without logging or including private account data.

Expose:

```python
def build_market_entities(
    entry: ConfigEntry[TossInvestRuntimeData],
) -> list[SensorEntity]:
    """Build always-present market context and option-gated ranking entities."""
```

Call `build_market_entities(entry)` from `sensor.async_setup_entry`. Register KOSPI/KOSDAQ and investor-flow sensors enabled by default. Register bond indicators and ranking snapshots disabled by default to limit Recorder/UI growth; rankings are not registered at all unless `entry.options["enable_rankings"]` is true.

- [ ] **Step 4: Verify entity lifecycle and quality**

Run focused tests, full pytest, Ruff check/format, and mypy. Cover empty records/rankings, unknown public symbols, stale isolation, option reload compatibility, registry defaults, stable device link, and `_unrecorded_attributes`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/market_entities.py custom_components/toss_invest/sensor.py tests/test_market_entities.py
git commit -m "feat: expose advanced market context entities"
```

### Task 8: Privacy, Manual Refresh, and Alert Events

**Files:**
- Create: `custom_components/toss_invest/{alerts,button,switch,event}.py`
- Create: `tests/test_alerts.py`, `tests/test_controls.py`, `tests/test_event.py`

**Interfaces:**
- Produces: `switch.toss_invest_privacy_mode`, refresh button, `event.toss_invest_alert` with fixed event types, and `AlertEvaluator.evaluate(snapshot, now)`.

- [ ] **Step 1: Test default privacy and alert cooldown**

```python
# tests/test_alerts.py
def test_alert_evaluator_suppresses_repeat_inside_cooldown() -> None:
    evaluator = AlertEvaluator(cooldown=timedelta(hours=1), enabled={"daily_move": Decimal("0.05")})
    first = evaluator.evaluate(symbol="TEST", values={"daily_move": Decimal("0.06")}, now=NOW)
    second = evaluator.evaluate(symbol="TEST", values={"daily_move": Decimal("0.07")}, now=NOW + timedelta(minutes=30))
    assert [item.type for item in first] == ["daily_move"]
    assert second == []
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_alerts.py tests/test_controls.py tests/test_event.py -q`
Expected: FAIL importing alert and control modules.

- [ ] **Step 3: Implement persisted controls and privacy-safe events**

```python
# custom_components/toss_invest/event.py
EVENT_TYPES = ["daily_move", "total_return", "portfolio_daily", "near_high", "near_low", "drawdown", "volume_spike", "stock_warning", "stale_data", "api_failure"]

class TossInvestAlertEvent(TossInvestEntity, EventEntity):
    _attr_event_types = EVENT_TYPES

    @callback
    def async_emit(self, alert: Alert) -> None:
        payload = {"symbol": alert.symbol, "observed": str(alert.observed), "threshold": str(alert.threshold), "severity": alert.severity, "source_timestamp": alert.source_timestamp.isoformat()}
        if alert.monetary and not self._include_monetary_payloads:
            payload.pop("observed", None)
            payload.pop("threshold", None)
        self._trigger_event(alert.type, payload)
        self.async_write_ha_state()
```

Implement privacy with `RestoreEntity`, defaulting on when no prior state exists. Manual refresh calls runtime refresh-all, coalesces in-progress work, and enforces a 10-second cooldown. Enable warning and stale alerts by default; all numerical thresholds remain disabled until set.

- [ ] **Step 4: Verify controls and alerts**

Run: `pytest tests/test_alerts.py tests/test_controls.py tests/test_event.py -q`
Expected: all PASS, including restart restoration, event-type validation, threshold transition, cooldown expiry, and monetary-payload masking.

- [ ] **Step 5: Commit**

```bash
git add custom_components/toss_invest/alerts.py custom_components/toss_invest/button.py custom_components/toss_invest/switch.py custom_components/toss_invest/event.py tests/test_alerts.py tests/test_controls.py tests/test_event.py
git commit -m "feat: add privacy controls and investment alerts"
```

### Task 8B: Remove No-op Configuration Controls

**Files:**
- Modify: `custom_components/toss_invest/config_flow.py`
- Modify: `custom_components/toss_invest/{strings.json,translations/en.json,translations/ko.json}`
- Modify: `tests/test_config_flow.py`

**Interfaces:**
- Removes Options Flow controls that v0.1 cannot honor: optional KRW conversion, backend dashboard colors, and monetary alert payload opt-in.
- Keeps FX normalization always enabled for meaningful mixed-currency allocation/concentration calculations; dashboard colors remain explicit Lovelace theme variables; monetary event values remain fail-safe omitted.

- [ ] **Step 1: Write failing contract tests**

Assert the Options Flow schema and all translation catalogs exclude `enable_krw_conversion`, `gain_color`, `loss_color`, `neutral_color`, `border_color`, `glow_color`, and `include_monetary_alert_payloads`. Preserve every functional schedule, optional-data, cooldown, boolean-alert, and numerical-threshold option.

- [ ] **Step 2: Remove the no-op UI surface**

Delete the unused constants, defaults, selectors, schema rows, and translations. Do not remove the internal fail-safe event policy: absent `include_monetary_alert_payloads` must still omit monetary `observed` and `threshold` values. Do not alter allocation/concentration calculations or dashboard CSS variables.

- [ ] **Step 3: Verify and commit**

Run focused/full pytest, Ruff check/format, mypy, translation JSON checks, and an `rg` scan showing no user-facing no-op option remains. Commit `fix: remove no-op integration options`.

### Task 9: Theme-adaptive Dashboard and Notification Blueprint

**Files:**
- Create: `dashboards/{toss-invest-native,toss-invest-enhanced}.yaml`, `dashboards/README.md`
- Create: `blueprints/automation/toss_invest_alert.yaml`
- Create: `tests/test_yaml_artifacts.py`

**Interfaces:**
- Consumes: entity naming documented in Tasks 7–8.
- Produces: sixth Lovelace view `Toss 주식`, mobile/desktop layouts, blueprint accepting an event entity and notify action.

- [ ] **Step 1: Test YAML invariants and theme safety**

```python
# tests/test_yaml_artifacts.py
from pathlib import Path
import yaml

def test_dashboard_is_sixth_view_and_has_no_fixed_surface_colors() -> None:
    dashboard = yaml.safe_load(Path("dashboards/toss-invest-native.yaml").read_text())
    view = dashboard["views"][0]
    assert view["title"] == "Toss 주식"
    text = Path("dashboards/toss-invest-enhanced.yaml").read_text()
    assert "var(--primary-text-color)" in text
    assert "#000000" not in text and "#ffffff" not in text.lower()
```

- [ ] **Step 2: Verify missing-artifact failure**

Run: `pytest tests/test_yaml_artifacts.py -q`
Expected: FAIL with missing dashboard file.

- [ ] **Step 3: Add native and enhanced views plus blueprint**

```yaml
# dashboards/toss-invest-native.yaml
views:
  - title: Toss 주식
    path: toss-invest
    icon: mdi:chart-line
    type: sections
    sections:
      - type: grid
        cards:
          - type: tile
            entity: switch.toss_invest_privacy_mode
          - type: tile
            entity: button.toss_invest_refresh
          - type: entities
            title: 포트폴리오
            entities:
              - sensor.toss_invest_total_market_value
              - sensor.toss_invest_daily_return
              - sensor.toss_invest_total_return_after_cost
```

The enhanced YAML uses Button Card, Auto Entities, ApexCharts Card, and Layout Card with HA CSS variables, Korean red/blue defaults exposed as variables, two desktop columns and one mobile column. Include summary, allocation, holdings, selected detail, market context, and risk sections. The blueprint triggers on `state` changes of the selected event entity and executes a required `action` selector, preserving event type and non-sensitive payload in template variables.

- [ ] **Step 4: Validate YAML and render in the dev container**

Run: `pytest tests/test_yaml_artifacts.py -q && docker compose -f dev/compose.yaml up -d`
Expected: tests PASS and Home Assistant reports healthy; manually verify light, dark, 1440px, and 390px screenshots without changing production.

- [ ] **Step 5: Commit**

```bash
git add dashboards blueprints tests/test_yaml_artifacts.py
git commit -m "feat: add responsive Toss investment dashboard"
```

### Task 10: Documentation, CI, HACS, and Release Metadata

**Files:**
- Create: `README.md`, `LICENSE`, `docs/{configuration,privacy,recorder,troubleshooting}.md`
- Create: `.github/workflows/{test,validate,compatibility,release}.yaml`
- Create: `.github/dependabot.yml`, `.github/ISSUE_TEMPLATE/bug_report.yml`
- Modify: `hacs.json`
- Create: `tests/test_documentation.py`

**Interfaces:**
- Produces: reproducible install/configuration guide and automated validation/release gates.

- [ ] **Step 1: Require security and read-only disclosures**

```python
# tests/test_documentation.py
from pathlib import Path

def test_readme_documents_security_boundaries() -> None:
    readme = Path("README.md").read_text()
    for phrase in ["read-only", "client_secret", "Privacy mode", "not an authorization boundary", "No order"]:
        assert phrase in readme
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/test_documentation.py -q`
Expected: FAIL because README is missing.

- [ ] **Step 3: Add exact workflows and operator documentation**

```yaml
# .github/workflows/validate.yaml
name: Validate
on: [push, pull_request, workflow_dispatch]
permissions: {}
jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: hacs/action@main
        with:
          category: integration
```

Document Config Flow, every option and bound, entity defaults, alert payloads, HACS custom-repository installation, dashboard dependencies, Recorder exclusions, real-API test safety, troubleshooting by Toss request ID, and privacy limitations. Test workflow runs pytest/Ruff/mypy; compatibility workflow runs weekly against the latest stable HA image; release workflow validates a `v*` tag before creating the HACS archive.

- [ ] **Step 4: Run all local quality gates**

Run: `pytest -q && ruff check . && ruff format --check . && mypy custom_components/toss_invest && python -m json.tool hacs.json >/dev/null`
Expected: every command exits 0.

- [ ] **Step 5: Commit**

```bash
git add README.md LICENSE docs .github hacs.json tests/test_documentation.py
git commit -m "docs: prepare HACS release and CI validation"
```

### Task 10B: AGY Final-audit Hardening

**Files:**
- Modify: `custom_components/toss_invest/{sensor,button}.py`
- Modify: `tests/{test_sensor,test_controls}.py`
- Modify: `dev/{compose.yaml,README.md}` and `tests/test_dev_compose.py`
- Modify: `dashboards/toss-invest-enhanced.yaml` and `tests/test_yaml_artifacts.py`

**Interfaces:**
- Cleans optional buying-power/refresh registry entries when their options are disabled.
- Allows a git-ignored developer secrets file override while keeping the example file as a zero-setup default.
- Makes enhanced dashboard JavaScript fail safe while HA states/entities are still loading.

- [ ] **Step 1: Write failing regression tests**

Cover true-to-false option reload removal for buying-power sensors and the manual-refresh button, Compose default/override secrets source mapping without target overlap, and optional chaining/fallbacks for privacy, entity state/attributes, and candle attributes.

- [ ] **Step 2: Implement the bounded hardening**

Reuse entity-registry cleanup patterns without touching required entities. Use a Compose environment variable with a checked-in example default and document a `dev/secrets.yaml` override. Add JavaScript optional chaining and neutral fallbacks without weakening monetary masking.

- [ ] **Step 3: Verify and commit**

Run focused/full pytest, Ruff check/format, mypy, Compose config with default and override, actual HA 2026.7.2 startup, and dashboard YAML contracts. Commit the registry and UI/dev-environment fixes separately.

### Task 11: End-to-end Verification, Public Repository, and Production Gate

**Files:**
- Create: `docs/verification/2026-07-13-v0.1.0.md`
- Modify only if verification finds a defect: files owned by Tasks 1–10 plus their focused tests.

**Interfaces:**
- Produces: clean verification record, public GitHub remote, passing CI, and a release candidate ready for manual production installation.

- [ ] **Step 1: Run the complete mock-data and Docker verification**

Run: `pytest -q --cov=custom_components.toss_invest && ruff check . && ruff format --check . && mypy custom_components/toss_invest && docker compose -f dev/compose.yaml up -d && docker compose -f dev/compose.yaml ps`
Expected: tests/lint/types PASS, Home Assistant container is `healthy`, and no production URL is contacted.

- [ ] **Step 2: Perform the read-only real-API development check**

Enter credentials only in the development Home Assistant Config Flow. Confirm account selection, both market types when present, 30-second open-price scheduling, manual refresh cooldown, light/dark dashboard, privacy default-on, and sanitized logs. Never paste credentials into terminal commands, fixtures, screenshots, or the verification document.

- [ ] **Step 3: Record evidence and scan for secrets/mutations**

```markdown
# v0.1.0 Verification

- Home Assistant 2026.7.2 Docker smoke test: PASS
- Mock API suite: PASS
- Real API read-only setup: PASS
- KR/US entity checks: PASS or NOT PRESENT IN ACCOUNT
- Light/dark and desktop/mobile dashboard checks: PASS
- Rate-limit and sanitized-log checks: PASS
- Order mutation/history/conditional source scan: PASS
```

Run: `rg -n "client_secret|access_token|X-Tossinvest-Account|/orders|/conditional-orders" . --glob '!docs/superpowers/**' --glob '!tests/**'`
Expected: only intentional constant/schema references; no values, mutation paths, or log interpolation.

- [ ] **Step 4: Authenticate and create the public GitHub repository**

Run interactively: `gh auth login -h github.com`
Then run: `gh repo create inganyoyo/ha-toss-invest --public --source=. --remote=origin --description "Read-only Toss Securities portfolio integration for Home Assistant" --push`
Expected: repository URL `https://github.com/inganyoyo/ha-toss-invest` and `git remote -v` shows `origin`.

- [ ] **Step 5: Verify remote checks and commit the evidence**

Run: `gh run list --limit 10 && git status --short`
Expected: test and validation workflows pass; worktree contains only the verification note.

```bash
git add docs/verification/2026-07-13-v0.1.0.md
git commit -m "test: record v0.1.0 verification"
git push origin main
```

Do not install in production until the user reviews the verification record and explicitly approves deployment. Production deployment must use the release/HACS path, not a direct file copy.
