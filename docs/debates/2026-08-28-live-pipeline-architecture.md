# Debate: Live pipeline — how to get Upstox data to the frontend in real time

- **Date**: 2026-08-28
- **Drivers**: Dashboard shows static demo.json; real-time analytics require live market data
- **Options**: A) REST polling B) WebSocket push from backend C) SSE (Server-Sent Events)
- **Outcome**: B (WebSocket push) → ADR-024

## Expert opinions

### Market-data architect (confidence 0.85)
**Assessment** — Upstox data changes every capture cycle (~60 s). REST polling creates
N * T requests/hour across N tabs; WebSocket holds one connection per client and pushes
only when data changes. SSE is simpler but unidirectional — the client cannot send
subscribe/unsubscribe or request snapshots.
**Concerns** — WebSocket reconnection logic adds frontend complexity; proxy configuration
(Vite, nginx) must forward `Upgrade` headers.
**Position** — B.

### Frontend engineer (confidence 0.80)
**Assessment** — React hooks compose naturally with WebSocket `onmessage`. A single
`useLiveData` hook can merge live payloads over demo data, providing graceful degradation.
REST polling requires coordinating intervals across 10+ tabs.
**Concerns** — Must handle reconnection, stale-while-revalidating, and connection status
display.
**Position** — B.

### Platform engineer (confidence 0.75)
**Assessment** — FastAPI's native WebSocket support (`@router.websocket`) eliminates the
need for a separate push service. The existing `WebSocketManager` pattern (broadcast to
connected clients) is proven in the codebase.
**Concerns** — Per-client state in WebSocketManager must be cleaned up on disconnect to
prevent memory leaks.
**Position** — B.

## Consensus
WebSocket push (B). The backend already has `WebSocketManager` with broadcast; the
capture pipeline triggers `send_dashboard_update` on each cycle. The frontend merges live
payloads over demo data so the app works without a backend. Vite proxy configured with
`ws: true`.

## Dissents
SSE would avoid the proxy `Upgrade` header issue, but the need for client-to-server
messages (subscribe, snapshot requests, ping/pong) makes it insufficient.
