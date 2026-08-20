# ADR-002: Two packages — `optitrade` quant core, `options_trading` platform

## Status
Accepted

## Context
The report specifies four engines (vol surface, Greeks, hedging, risk) that must be usable
"standalone or integrated into larger trading systems". The existing FastAPI/Upstox platform
mixes broker I/O, web handlers, and the little quant logic it has. Testing pricing math
through a web app is slow and flaky; broker SDK churn should never break pricing.

## Decision
Two packages under `src/`:
- `optitrade` — pure computation: numpy/scipy only, deterministic, strictly typed
  (mypy `disallow_untyped_defs`), no web framework, no broker, no network. The only I/O is
  the append-only event journal.
- `options_trading` — the FastAPI platform and Upstox integration; consumes `optitrade`
  through thin adapters.

Dependency direction is one-way: platform → core. Enforced by review and by the core having
no platform imports to reach for.

## Consequences
### Positive
- The quant core is unit-testable in milliseconds, publishable on its own, and holds the
  strict typing bar without dragging the legacy platform through it (ADR-004).
- SOLID at the architecture level: the platform is an adapter around a stable core interface.
### Negative
- Two packages in one wheel; contributors must learn which side a change belongs to.
### Risks
- Convenience pressure to import FastAPI types into the core. Refuse in review; CLAUDE.md
  rule 1.
