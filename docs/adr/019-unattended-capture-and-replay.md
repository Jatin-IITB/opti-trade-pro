# ADR-019: Unattended capture scheduler; real-history replay; backtest-vs-desk drift metric

## Status
Accepted

## Context
Phase 0's exit criterion is history accumulating without a human; phase 3's harness must
run unchanged on that history; phase 4's credibility metric is how far live (paper)
behaviour drifts from the backtest on identical days.

## Decision
- **Scheduler** (`options_trading/services/capture_scheduler.py`): an asyncio loop calling
  the capture pipeline every configured interval inside the IST market window
  (Mon–Fri, 09:15–15:30, holiday list as deployment config). Injected clock and sleeper —
  the schedule logic is unit-tested without wall time. One failed capture never kills the
  loop; failures are counted and surfaced on a status route. Capture is operator-started
  (`POST /capture/schedule/start`), never auto-started at app boot — a trading service
  that silently starts collecting on deploy is a surprise, and surprises are bugs.
- **StoreReplay** (`optitrade/backtest/market_replay.py`): stored Parquet chains →
  `MarketDay`s (end-of-day snapshot per date, filter → surface fit → trailing
  close-to-close RV → the same feature keys the synthetic replay emits). Unfittable days
  are skipped with collected warnings, not fatal. The walk-forward harness runs on
  `StoreReplay` and `SyntheticVRPMarket` interchangeably — same protocol, same code path.
- **Drift** (`optitrade/desk/reconcile.py`): run the backtester and the desk cycle over
  the *same* replay with the *same* strategy (the shared `Strategy` protocol makes this a
  like-for-like comparison) and report per-day P&L drift in bps of initial equity.
  Because decision code is shared, residual drift isolates execution-model differences —
  fills, margin proxy, hedge booking — which is exactly the gap live trading must close.

Enforcing tests: `tests/unit/test_capture_scheduler.py`,
`tests/unit/quant/test_store_replay.py`, `test_reconcile.py`.

## Consequences
### Positive
- The resume metric ("N days accumulating unattended") is a deployment fact away; the
  synthetic→real transition needs zero harness changes; drift is a number, not a vibe.
### Negative
- Holiday calendars are config the operator must maintain; EOD-only replay ignores
  intraday captures until an intraday replay is justified.
### Risks
- Scheduler and desk clocks disagreeing on "market open" — both use the same
  zoneinfo("Asia/Kolkata") pure function.
