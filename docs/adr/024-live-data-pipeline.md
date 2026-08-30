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
