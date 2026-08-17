# ADR-007: Hedging — Whalley–Wilmott no-transaction band, RV/IV-modulated gamma scalping

## Status
Accepted

## Context
Fixed-interval delta rebalancing either bleeds transaction costs (too frequent) or leaks
gamma P&L through delta drift (too infrequent). The optimal policy under proportional costs
is a stochastic-control result, not a tuning knob. Debate:
`docs/debates/2026-08-17-hedging-band-policy.md`.

## Decision
- Rebalance only when |portfolio delta| exits a no-transaction band with the Whalley–Wilmott
  (1997) asymptotically optimal half-width H = (3/2 · k·S·Γ² / λ)^(1/3) (k proportional
  cost, λ risk aversion) — `optitrade/hedging/band.py`.
- Gamma scalping modulates the band by the realized/implied vol ratio (RiskMetrics EWMA for
  RV): RV ≫ IV ⇒ tighten (harvest ½ΓS²(σ_R²−σ_I²)dt), RV ≪ IV ⇒ widen (save costs), linear
  in between — `optitrade/hedging/gamma_scalper.py`.
- `DeltaHedger.decide` is a pure function returning a `HedgeDecision` with a plain-English
  rationale and confidence; callers journal it. State lives with the caller.
- Tracking quality is measured, not asserted by hand: the GBM hedging simulation
  (`optitrade/backtest/hedging_sim.py`) reports hedged P&L vs theoretical theta
  (`tests/unit/quant/test_hedging_sim.py`).

## Consequences
### Positive
- The rebalancing trade-off is solved by the model that owns it; every hedge decision is
  auditable.
### Negative
- λ (risk aversion) is a genuine free parameter the caller must own.
### Risks
- WW is asymptotic (small-cost); for very large costs the band is heuristic — bounded by
  `max_half_width`.
