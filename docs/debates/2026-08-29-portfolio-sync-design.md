# Debate: Portfolio sync — how to integrate real Upstox positions into the quant stack

- **Date**: 2026-08-29
- **Drivers**: Dashboard analytics are synthetic; users want their actual P&L, Greeks, risk
- **Options**: A) On-demand fetch per API call B) Background sync service with cache C) Client-side fetch + backend compute
- **Outcome**: B (background sync service) → ADR-025

## Expert opinions

### Trading-systems architect (confidence 0.90)
**Assessment** — Upstox API rate limits are 25 req/s burst, but real-time position updates
require consistent cadence. An on-demand fetch (A) risks latency spikes at page load and
redundant API calls from multiple dashboard tabs. A background sync service polling every
60 s during market hours keeps a fresh cache that any route or WebSocket broadcast can
read without blocking.
**Concerns** — Sync service must survive API failures without stopping the loop; stale
positions must be clearly timestamped so the frontend can warn.
**Position** — B.

### Quant engineer (confidence 0.85)
**Assessment** — Portfolio analytics (aggregate Greeks, risk utilization, P&L explain)
require the full position set plus current market data in one snapshot. Threading a cached
`Portfolio` into `LiveAnalytics.build_from_raw_chain()` is a clean extension — the
analytics code already accepts optional position overrides.
**Concerns** — The mapper from Upstox `trading_symbol` to core `Position` fields (strike,
expiry, option_type) must use the same ACT/365 + IST convention as capture. Inconsistent
year-fractions would miscompute Greeks.
**Position** — B.

### Security reviewer (confidence 0.80)
**Assessment** — Position data is PII-adjacent (reveals trading strategy). Logging must be
DEBUG-only for actual positions, INFO for counts/summary only. The sync service must use
the existing encrypted token store, never handle raw credentials.
**Concerns** — Sync service should not auto-start on deploy (same principle as ADR-019:
capture scheduler).
**Position** — B, with logging constraints.

## Consensus
Background sync service (B), modeled on `LivePipelineService` + `CaptureScheduler`.
60-second cadence during market hours, survive-and-count failures, portfolio cached on
`app.state`. REST routes and WebSocket broadcast read from the cache. Position data logged
at DEBUG only. Mapper reuses IST/ACT-365 conventions from capture.

## Dissents
On-demand fetch (A) would be simpler but creates latency on every page load and hammers
the Upstox API when multiple clients are connected.
