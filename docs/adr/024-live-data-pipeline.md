# ADR-024: Live data pipeline — Upstox capture → quant analytics → WebSocket broadcast

## Status
Accepted

## Context
The dashboard displayed static demo data from `demo.json`. For the product to be useful,
users need real-time volatility surfaces, Greeks, option chains, and risk metrics computed
from live Upstox market data — streamed to the frontend without manual refresh.

The capture service (ADR-019) already fetches option chains from Upstox. The quant engines
(eSSVI calibration, Greeks computation, risk engine) are fully built. The gap was: no
pipeline connecting capture → analytics → frontend, and no real-time transport.

## Decision
- **LiveAnalytics** (`options_trading/services/live_analytics.py`): stateless service that
  takes a `RawChain` capture and produces a complete `DashboardSnapshot` — vol surface,
  eSSVI calibration, Greeks comparison (4 methods), option chain with Greeks, and risk
  dashboard. Each computation is isolated; one failure doesn't block others.
- **LivePipelineService** (`options_trading/services/live_pipeline.py`): asyncio orchestrator
  that runs capture → analytics → WebSocket broadcast on a configurable interval during
  market hours. Modeled on `CaptureScheduler` (ADR-019): survive-and-count failures,
  operator-started, never auto-started at boot.
- **WebSocket transport** (`/api/v1/dashboard/ws/{client_id}`): bidirectional channel.
  Server pushes `dashboard_update` messages; client sends `request_snapshot`, `subscribe`,
  `unsubscribe`, `ping`. `WebSocketManager` tracks connected clients and broadcasts to all.
- **Frontend `useLiveData` hook**: connects to WebSocket, merges live payloads over demo
  data baseline. Graceful degradation — app works fully on demo data when backend is down.
  Auto-reconnects every 5 s. Header shows connection status (Live/Demo Mode) and spot price.
- **Vite proxy**: `/api` proxy configured with `ws: true` for WebSocket passthrough.

Enforcing tests: `tests/unit/test_live_analytics.py`, `tests/unit/test_live_pipeline.py`,
`tests/unit/test_websocket_manager.py`.

## Consequences
### Positive
- Five dashboard tabs (Vol Surface, eSSVI Fit, Greeks Book, Option Chain, Risk) show live
  data when the backend is running; five (Scenarios, P&L, Higher-Order, Backtest, VRP)
  remain demo until their backend computations are built.
- Single WebSocket connection per client replaces what would be 10+ REST polling intervals.
- Demo fallback means the frontend is always demonstrable without backend setup.
### Negative
- WebSocket reconnection adds frontend complexity (~130 lines in `useLiveData`).
- Vite and production reverse proxies must forward `Upgrade` headers.
### Risks
- `WebSocketManager` per-client state must be cleaned on disconnect to avoid memory leaks —
  addressed by `finally` block in the WebSocket route handler.

## Amendment (2026-09-03)
The demo-data baseline described above is withdrawn; `frontend/src/data/demo.json` is
deleted. Three claims in the original text no longer hold:

- *"Graceful degradation — app works fully on demo data when backend is down."* The demo
  chain was priced off a 20,000 NIFTY. Because the backend broadcasts nothing until the
  first capture completes, this was not a brief flash before real data arrived — it was
  the steady state whenever the market was shut, which is most of the day. It was observed
  in production showing a 20,000 spot against a real level of 23,873.
- *"Header shows connection status (Live/Demo Mode)."* The `DataSource` union driving that
  badge was later removed as unused, so the disclosure went with it and the sample chain
  rendered indistinguishably from live data. That is the failure mode phases 1-3 were spent
  eliminating elsewhere, and the reason their fabricated series were deleted rather than
  left as fallbacks.
- *"Five tabs remain demo until their backend computations are built."* All ten now have
  real data paths.

Replacement: every field of `DashboardData` starts `null`, and market panels render
through `LiveGate`, which reports that no chain has been captured yet and why (not
connected / connecting / outside market hours). This mirrors `HistoryGate` for the
replay-backed panels; the two differ only in what is missing. The header spot shows an em
dash rather than a number before the first capture. Deleting the file rather than leaving
it unreferenced is deliberate: a fallback that exists will eventually be shown.

The pipeline decision itself — capture callback → analytics → WebSocket broadcast — is
unchanged.
