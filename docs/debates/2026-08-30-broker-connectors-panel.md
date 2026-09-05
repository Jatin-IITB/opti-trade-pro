# Debate: Broker connectors — how to present multi-broker integration in the frontend

- **Date**: 2026-08-30
- **Drivers**: Users need a single place to link/manage brokerage accounts; Prism's MCP
  connector pattern is a proven reference
- **Options**: A) Settings page with inline form B) Dedicated connectors panel (Prism-style) C) Modal-based onboarding wizard
- **Outcome**: B (dedicated connectors panel) → ADR-026

## Expert opinions

### Product designer (confidence 0.85)
**Assessment** — Prism's ConnectorsPanel pattern (Configured/Available sections, status
badges, feature tags, action buttons) is battle-tested across 1500+ lines. A simplified
version (~400 lines) preserves the UX patterns without the complexity of Prism's
multi-transport onboarding wizard. Each broker is a card with clear status — Connected,
Not Connected, Coming Soon — making the platform's integration roadmap visible.
**Concerns** — Coming Soon slots must not look like broken features; the badge + disabled
button pattern handles this.
**Position** — B.

### Security engineer (confidence 0.90)
**Assessment** — OAuth redirect flow (user leaves the app → authenticates on broker's
page → callback) is the only acceptable pattern for brokerage auth. No credentials touch
our frontend or backend in plaintext. The explainer footer reinforces this: read-only,
Fernet-encrypted, auto-refresh.
**Concerns** — The "Connect" button must navigate to the backend's `/api/v1/auth/login`
which handles the OAuth redirect, not directly to the broker's OAuth URL (which would
leak our client_id in frontend source).
**Position** — B.

### Frontend engineer (confidence 0.80)
**Assessment** — A dedicated tab is discoverable; a settings sub-page or modal requires
navigation users may miss. Auto-refresh polling (10 s) for sync status keeps the card
live without manual refresh. The `useAuthStatus` hook centralizes auth state for reuse
by Portfolio and Connectors tabs.
**Concerns** — Polling interval should be configurable; 10 s is aggressive for a status
check.
**Position** — B.

## Consensus
Dedicated Connectors panel (B) as a top-level dashboard tab. Upstox has real OAuth flow;
Zerodha, Groww, Mira Asset are Coming Soon slots showing the integration roadmap. Card
layout with status badges, feature tags, and action buttons. Modeled after Prism's
ConnectorsPanel but scoped to brokerage use case.

## Dissents
None significant — the modal wizard (C) was briefly considered but adds complexity without
benefit when there's only one active integration.
