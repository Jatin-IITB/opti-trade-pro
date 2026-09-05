# ADR-025: Portfolio sync service — real Upstox positions through quant engines

## Status
Accepted

## Context
The dashboard analytics computed Greeks and risk on synthetic positions. Users want to see
their actual P&L, aggregate Greeks, and risk utilisation from real brokerage positions.
The OAuth 2.0 flow and Fernet-encrypted token storage are already built. The quant engines
accept `Portfolio`/`Position` types from `optitrade.core.types`. The gap was: no code
fetches portfolio data from Upstox, no mapper to core types, and `DashboardService`
returns hardcoded mock positions.

## Decision
- **UpstoxPortfolioClient** (`options_trading/services/portfolio_client.py`): typed async
  client wrapping Upstox portfolio REST APIs (positions, holdings, orders, P&L history).
  Uses `auth_service.get_valid_access_token()` on each call — no separate token handling.
- **Mapper `to_core_portfolio()`**: parses `trading_symbol` to extract strike, expiry,
  option_type. Uses ACT/365 + IST convention matching `capture_service.py` (lines 176–183).
  Maps `UpstoxPosition` → core `Position` with computed year-fraction for Greeks.
- **PortfolioSyncService** (`options_trading/services/portfolio_sync_service.py`): asyncio
  loop modeled on `CaptureScheduler` (ADR-019). 60-second cadence during market hours.
  `sync_once()`: fetch → map → journal (ADR-009) → compute aggregate Greeks → WS broadcast.
  Survive-and-count: failures increment a counter, never stop the loop. Cached portfolio
  on `app.state` for REST routes.
- **Analytics threading**: `LiveAnalytics.build_from_raw_chain()` accepts optional
  `Portfolio`. When provided, `_build_greeks_book()` uses real positions instead of
  synthetic ATM straddle; `_build_risk_dashboard()` populates actual utilisation.
- **REST API** (`/api/v1/portfolio/*`): summary, positions with per-position Greeks,
  signals (entry vs current, moneyness, DTE), sync status. All routes require auth.
- **Logging**: position data at DEBUG only; counts and summary at INFO (CLAUDE.md rule).

Enforcing tests: `tests/unit/test_portfolio_client.py`,
`tests/unit/test_portfolio_sync.py`, `tests/unit/test_portfolio_routes.py`,
`tests/unit/test_portfolio_analytics_integration.py`.

## Consequences
### Positive
- GreeksBook, RiskDashboard, and Signals tabs show real positions when Upstox is connected;
  demo fallback when not. The transition is seamless — same data shapes, different source.
- The sync service reuses proven patterns (CaptureScheduler, EventLog, WebSocketManager).
- Per-position signals (moneyness, DTE warnings, P&L %) give actionable portfolio insight.
### Negative
- Upstox API rate limits (25 req/s) constrain sync frequency; 60 s is conservative but
  means positions can be up to 60 s stale.
- The `trading_symbol` parser is brittle to Upstox format changes — covered by unit tests
  with fixture data.
### Risks
- IST year-fraction mismatch between capture and portfolio mapper would produce inconsistent
  Greeks — both use the same `_ist_now()` / ACT-365 convention, validated by cross-tests.
