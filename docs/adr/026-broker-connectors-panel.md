# ADR-026: Broker connectors panel — multi-broker OAuth management in the frontend

## Status
Accepted

## Context
The platform supports Upstox via OAuth but the frontend has no UI to manage broker
connections. Users had to know the raw `/api/v1/auth/login` URL. The product roadmap
includes Zerodha, Groww, and Mira Asset integrations. A single place to connect, monitor,
and disconnect brokers is needed — both for current use and to signal the integration
roadmap.

Prism's MCP ConnectorsPanel (~1500 lines) provides a battle-tested UX pattern:
Configured/Available sections, status badges, feature tags, action buttons, auto-refresh.

## Decision
- **ConnectorsPanel** (`frontend/src/components/ConnectorsPanel.tsx`): Prism-inspired but
  scoped to brokerage (~400 lines vs Prism's 1500). Two sections:
  - **Configured**: connected brokers showing auth details (user_id, token expiry), sync
    status (active/paused, last sync, position count, failure count), action buttons
    (Refresh Token, Disconnect).
  - **Available Brokers**: unconnected brokers with Connect button or Coming Soon badge.
- **Broker cards**: each card shows logo, name, description, feature tags
  (F&O Positions, Equity Holdings, etc.), and status badge (Connected / Not Connected /
  Coming Soon). Four brokers defined: Upstox (real OAuth), Zerodha, Groww, Mira Asset
  (Coming Soon slots).
- **OAuth flow**: "Connect with Upstox" navigates to `/api/v1/auth/login` (server-side
  redirect to Upstox OAuth). No client_id or credentials in frontend source. Disconnect
  calls `POST /api/v1/auth/logout`.
- **useAuthStatus hook** (`frontend/src/hooks/useAuthStatus.ts`): fetches
  `/api/v1/auth/status`, returns auth state for both ConnectorsPanel and PortfolioSummary.
- **Auto-refresh**: sync status polls every 10 s when connected; auth status refreshed on
  demand via Refresh button.
- **Explainer footer**: "How it works" section reinforcing OAuth, Fernet encryption,
  read-only mode, auto-sync during market hours.
- **Tab placement**: top-level "Connectors" tab with Plug icon — discoverable without
  navigation.

Enforcing tests: TypeScript strict check (`tsc --noEmit`), visual verification in browser.

## Consequences
### Positive
- Users can connect Upstox in one click from the dashboard; status is always visible.
- Coming Soon slots communicate the integration roadmap without dead features.
- Pattern is extensible — adding a new broker is one entry in the `BROKERS` array.
### Negative
- Polling sync status every 10 s is aggressive for a status endpoint; acceptable given
  the endpoint is a cache read, not an API call.
- Coming Soon brokers with no timeline could set expectations prematurely.
### Risks
- If Upstox changes their OAuth flow or token format, the Disconnect/Refresh Token
  actions may need updating — but the backend auth service abstracts this.
