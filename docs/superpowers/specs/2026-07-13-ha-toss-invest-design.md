# Home Assistant Toss Invest Integration Design

## 1. Summary

Build a read-only Home Assistant custom integration for the Toss Securities Open API. The integration supports Korean and US holdings, exposes portfolio and market data as native Home Assistant entities, calculates useful portfolio metrics, emits configurable alerts, and includes a responsive Lovelace dashboard example named `Toss 주식`.

The first release excludes order creation, modification, cancellation, order history, and every conditional-order endpoint. It may query the read-only buying-power endpoint when the user enables that data group. Trading and automation are a separate future phase with an independent design and explicit safety review.

The project will be published as the public GitHub repository `inganyoyo/ha-toss-invest`. It will support installation as a HACS custom repository from the first release. Submission to the default HACS repository will happen only after operational validation.

## 2. Goals

- Configure the integration entirely through Home Assistant Config Flow and Options Flow.
- Authenticate with Toss Securities using OAuth 2.0 Client Credentials.
- Support Korean and US holdings in their native currencies and as a combined KRW view.
- Refresh active-market prices every 30 seconds by default without approaching API limits.
- Show portfolio performance, allocation, risk signals, market context, and source-data freshness.
- Provide configurable Home Assistant alerts without issuing investment recommendations.
- Match the user's existing dashboard while adapting automatically to light and dark themes.
- Develop and verify in an isolated Docker Home Assistant environment before touching production.
- Keep credentials, tokens, account identifiers, and real portfolio data out of source control and logs.

## 3. Non-goals for Version 1

- Placing, modifying, or cancelling orders.
- Conditional orders or automated trading.
- Buy, sell, target-price, or security-selection recommendations.
- Replacing Home Assistant access control. Dashboard privacy mode is visual masking only.
- Registration in the default HACS repository before real-world validation.

## 4. Confirmed Environment

- Production Home Assistant: `2026.7.2`
- Development host: macOS with Docker 29.6.1 and Docker Compose 5.2.0
- Python runtime: 3.14 or newer, required by Home Assistant 2026.7.2
- Existing dashboard: five views (`홈`, `전기`, `시스템`, `차량`, `로또`)
- New view: sixth view named `Toss 주식`
- Existing visual language: rounded cards, restrained glow, dark and light themes
- GitHub account: `inganyoyo`; GitHub CLI must be reauthenticated before remote repository creation

## 5. Architecture

### 5.1 Repository layout

The public repository `ha-toss-invest` contains:

- `custom_components/toss_invest/`: Home Assistant integration
- `tests/`: unit and integration tests with sanitized fixtures
- `dev/`: Docker Compose Home Assistant development environment and mock API support
- `dashboards/`: theme-adaptive Lovelace example and dependency notes
- `blueprints/automation/`: alert-routing blueprint
- `.github/workflows/`: tests, linting, Hassfest, HACS validation, and release checks
- `docs/`: installation, configuration, privacy, recorder, and troubleshooting documentation

### 5.2 Internal boundaries

The integration is split into focused units:

1. **API client** — token lifecycle, request construction, response parsing, rate-limit headers, error mapping, and secret redaction.
2. **Data coordinators** — independently schedule account, asset, price, candle, market, ranking, and warning data.
3. **Calculation layer** — derive allocation, concentration, period returns, volatility, range, and drawdown using `Decimal` for monetary values.
4. **Entity layer** — map stable domain data to Home Assistant devices, sensors, binary sensors, events, buttons, and the privacy switch.
5. **Alert evaluator** — evaluate configured thresholds, suppress duplicates, and emit Home Assistant events.

No version 1 module imports, wraps, or calls order mutation, order-history, or conditional-order endpoints.

### 5.3 Configuration lifecycle

Config Flow asks for `client_id` and `client_secret`, validates them by obtaining a token, fetches available accounts, and lets the user select an account. Secrets are stored in the Home Assistant config entry and are never written to YAML or logs.

Reauthentication is triggered if the credentials are permanently rejected. Temporary token or network failures do not ask the user to re-enter credentials.

Options Flow manages display, refresh, alert, currency, color, and diagnostic settings without requiring the integration to be removed and added again.

## 6. Data Sources and Refresh Policy

### 6.1 API sources

Version 1 uses these read-only endpoint groups:

- Accounts and holdings
- Prices and daily candles
- Stock metadata and warnings
- KRW/USD exchange rate and KR/US market calendars
- Domestic market indicators and investor trading flows
- Korean and US rankings
- Buying power, when enabled

The integration treats the server-owned OpenAPI document as the source of truth and tolerates unknown enum values.

### 6.2 Default schedules

- Open-market prices: 30 seconds
- Holdings and buying power: 5 minutes
- Closed-market prices: 10 minutes
- Daily candles, indicators, exchange rate, investor flows, and rankings: 30 minutes
- Manual refresh: available through a Home Assistant button

Korean and US market calendars are evaluated independently. Only the market currently trading receives the open-market schedule.

### 6.3 User-configurable schedules

Options Flow exposes these explicit bounds:

- Open-market prices: 10 to 300 seconds
- Holdings and buying power: 30 to 3,600 seconds
- Closed-market prices: 60 to 3,600 seconds
- Candles, indicators, exchange rate, investor flows, and rankings: 300 to 21,600 seconds
- Request timeout: 5 to 60 seconds
- Maximum retry count: 0 to 5
- Daily-candle lookback: 20 to 500 trading days, default 252

It also exposes manual-refresh availability, KRW conversion, and optional data groups. Manual refresh is coalesced with an in-progress refresh and has a 10-second local cooldown.

Safety behavior is not disableable: the integration always honors `Retry-After`, applies exponential backoff with jitter, prevents concurrent duplicate refreshes, and throttles requests when `X-RateLimit-Remaining` is low. Published limits are treated as defaults only; runtime response headers control actual throttling.

## 7. Home Assistant Device and Entity Model

### 7.1 Account device

The account device provides essential enabled-by-default entities for:

- Total purchase amount
- Total market value
- Total profit/loss and return, before and after cost
- Daily profit/loss and return
- KR/US and KRW/USD allocation
- Top-one and top-three concentration
- KRW and USD buying power, if enabled
- KRW/USD exchange rate
- Korean and US market status
- Data freshness and API health

### 7.2 Holding devices

Each active holding has a device identified by account and stock symbol. Essential entities expose:

- Current and average purchase price
- Quantity and market value
- Total profit/loss and return, before and after cost
- Daily profit/loss and return
- Warning state

Advanced entities are disabled by default to limit entity and Recorder growth:

- 1-week, 1-month, 3-month, 6-month, and 1-year returns
- Period high and low
- Drawdown from the recent high
- Historical volatility
- Volume change
- Raw or reduced daily-candle diagnostic data

When a holding is sold, its entities become unavailable and remain in the registry so Home Assistant history is preserved. They return to service if the holding is purchased again. Newly purchased holdings are discovered automatically.

### 7.3 Precision and source precedence

- Monetary and percentage calculations use `Decimal`, never binary floating point.
- Native-currency values are preserved.
- KRW-converted values are separate derived fields.
- Toss-provided after-cost values take precedence over local estimates.
- Derived metrics include their calculation window and last source timestamp.
- A missing or invalid field affects only dependent entities.

### 7.4 Recorder policy

Large candle attributes can increase the Home Assistant database quickly. They are disabled by default. Documentation provides a Recorder exclusion example for users who enable them. Essential numeric sensors remain suitable for Home Assistant history and ApexCharts.

## 8. Derived Investment Information

The integration derives objective descriptive metrics, not advice:

- Holding weights and country/currency exposure
- Top-one and top-three concentration
- Period returns from adjusted daily candles
- Period high/low and distance to each
- Drawdown from recent high
- Historical volatility based on daily returns
- Volume change relative to a configurable trailing average
- Portfolio-level daily and total performance

Foreign-exchange exposure is shown, but FX contribution to historical profit is not claimed because the holdings response does not provide historical purchase exchange rates.

## 9. Dashboard Design

The example adds a sixth Lovelace view named `Toss 주식`.

### 9.1 Layout

1. **Header and summary** — portfolio value, daily profit/loss, total return, market states, freshness, privacy switch, and refresh button.
2. **Allocation** — holding, country, and currency allocation plus concentration warnings.
3. **Holdings** — automatically generated holding cards with price, daily change, return versus average purchase price, and market value.
4. **Selected holding detail** — period chart, performance windows, high/low, drawdown, volatility, and warning state.
5. **Market context** — KOSPI, KOSDAQ, KRW/USD, investor flows, and optional rankings.
6. **Risk and alerts** — active warning, threshold, stale-data, and API-health signals.

Desktop uses a primarily two-column layout. Mobile collapses to a single column. The dashboard uses Home Assistant theme variables instead of hard-coded surface and text colors.

### 9.2 Colors and themes

- Light and dark themes update without reconfiguration.
- Korean-market defaults use red for gains, blue for losses, and neutral theme colors for no change.
- Gain, loss, neutral, border, and glow choices are configurable.
- Glow is restrained in light mode and more visible in dark mode.

The recommended version 1 approach is a native integration plus a dashboard YAML example. A dedicated Lovelace card is deferred until operational use identifies concrete limitations.

### 9.3 Dashboard dependencies

The enhanced example uses Button Card, Auto Entities, ApexCharts Card, and Layout Card. The dashboard documentation records tested versions and installation links. Missing custom cards must not prevent the integration and its native entities from working. A second minimal example uses only native Home Assistant cards.

## 10. Privacy Mode

`switch.toss_invest_privacy_mode` defaults to on and persists its state across Home Assistant restarts. Dashboard templates use it to mask monetary amounts while retaining percentages and allocation.

Privacy mode does not alter source sensor states and is not an authorization boundary. Any Home Assistant user who can access developer tools or entity history may still see monetary values. Documentation states this limitation prominently.

## 11. Alerts

Options Flow configures portfolio-wide defaults and optional per-holding overrides for:

- Daily percentage move
- Total return or loss threshold
- Portfolio daily profit/loss threshold
- Near-period-high or near-period-low threshold
- Drawdown threshold
- Volume spike
- Stock warning activation
- Data staleness and repeated API failure

Price, return, profit/loss, high/low, drawdown, and volume thresholds are disabled until the user enters a value. Stock-warning and stale-data alerts are enabled by default. The default repeat cooldown is one hour and is configurable.

The evaluator emits `event.toss_invest_alert`. Events include the alert type, symbol when applicable, observed value, threshold, source timestamp, and severity. Credentials, account identifiers, and private monetary values are omitted unless the user explicitly enables monetary alert payloads.

A bundled automation blueprint routes events to mobile-app notifications, persistent notifications, or another user-selected notify action. State transitions and a configurable cooldown prevent repeated notifications while a condition remains active.

## 12. Failure Handling

- Refresh groups fail independently; successful data remains available.
- Temporary failures retain the last successful value and mark data stale.
- Permanent credential rejection starts reauthentication.
- HTTP 429 honors `Retry-After` and server rate-limit headers.
- Network and 5xx failures use capped exponential backoff with jitter.
- Unknown warning and enum values are preserved as diagnostics rather than crashing parsing.
- Invalid numeric fields are isolated from unrelated calculations.
- API request IDs are logged for support, while secrets and account identifiers are masked.
- Coordinator overlap and manual-refresh storms are coalesced.

## 13. Development Environment

Docker Compose runs Home Assistant `2026.7.2` with the integration source bind-mounted into `custom_components`. Development configuration and runtime data are excluded from Git.

The environment provides:

- A mock Toss API or HTTP fixtures for development without a real account
- A disposable Home Assistant configuration volume
- Commands for starting, stopping, validating, and viewing logs
- A safe path for manually entering real credentials only in the local Config Flow

Most Python edits are tested through pytest and focused integration reloads. Container restarts are reserved for manifest, dependency, or platform-registration changes that Home Assistant cannot reload safely.

## 14. Testing and Validation

Automated coverage includes:

- OAuth issuance, expiry, refresh, and rejection
- Endpoint parsing, unknown fields, malformed numbers, and partial responses
- Rate-limit headers, 429 handling, timeout, retry, jitter, and request coalescing
- KR and US holdings with KRW and USD precision
- Allocation, concentration, period return, volatility, volume, and drawdown calculations
- Config Flow, Options Flow, and reauthentication
- Dynamic holding addition, removal, and restoration
- Entity availability, stable unique IDs, and device-registry behavior
- Alert thresholds, deduplication, cooldown, and payload privacy
- Privacy-switch restoration
- Dashboard YAML syntax and documented dependencies

CI runs pytest, Ruff, mypy, Hassfest, and HACS validation. Docker smoke tests verify installation, setup, reload, and dashboard loading against Home Assistant `2026.7.2`. A scheduled compatibility job runs the same smoke test against the latest stable Home Assistant release available at build time.

Before production installation:

1. Pass unit, integration, validation, and Docker smoke tests using mock data.
2. Run read-only real-API tests in the development container.
3. Confirm no credentials or real portfolio data appear in the repository, fixtures, logs, or CI artifacts.
4. Install the release artifact in production and validate entities before adding the dashboard view.

## 15. Release and GitHub Workflow

The GitHub CLI is currently installed but its `inganyoyo` authentication is expired. Before creating the remote public repository, the user must complete `gh auth login -h github.com` interactively.

Releases use semantic versioning and attach a HACS-compatible archive. The README documents installation, supported data, API limits, privacy behavior, dashboard dependencies, troubleshooting, and the explicit absence of trading functionality.

## 16. Success Criteria

Version 1 is complete when:

- A user can install the repository through HACS custom repositories.
- Config Flow validates credentials and allows account selection without YAML secrets.
- Korean and US holdings appear with correct native-currency and KRW summary values.
- Open-market price updates run at the configured 30-second default without rate-limit errors.
- The sixth `Toss 주식` view works in both light and dark themes and on desktop and mobile.
- Privacy masking, manual refresh, alerts, and failure recovery behave as documented.
- Mock-data tests and real read-only development tests pass.
- No order mutation, order-history, or conditional-order endpoint and no sensitive account data is present in version 1.

## 17. Future Phase: Trading

Trading is intentionally deferred. Any future order or automated-trading work requires a new design covering separate permissions, explicit enablement, confirmation rules, idempotency, order limits, market-session constraints, kill switches, audit history, notification requirements, and failure recovery. It must not be enabled by upgrading this read-only integration without deliberate user action.
