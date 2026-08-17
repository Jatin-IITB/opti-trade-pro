# ADR-003: Quant conventions — units, time, and signs

## Status
Accepted

## Context
The old code mixed per-1% vega with per-unit rho and per-day theta, undocumented. Unit
ambiguity is the classic source of silent quant bugs.

## Decision
Single convention set, documented in `optitrade/core/types.py` and binding everywhere:
- Time to expiry: year fraction (ACT/365), never a date, floored at 1e-12 near expiry.
- Rates and dividend yields: continuously compounded decimals.
- Volatility: annualised decimal (0.20 = 20%).
- Vega per unit vol, rho per unit rate, theta per year of calendar time. Presentation
  layers rescale (per-1%, per-day) for display only.
- Quantities signed: positive long, negative short.

## Consequences
### Positive
- Cross-validation between analytic, finite-difference, and adjoint-AD Greeks is exact
  (`tests/unit/quant/test_greeks_cross.py`) because all three speak the same units.
### Negative
- Displayed numbers differ from broker-app conventions until rescaled at the edge.
### Risks
- A contributor "helpfully" rescaling inside the core; the cross-validation tests catch it.
