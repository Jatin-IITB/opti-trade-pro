# ADR-005: Vol surface — cubic spline in log-moneyness + per-expiry SABR, time in total variance

## Status
Accepted

## Context
The engine needs both a non-parametric surface that reprices the market exactly and a
parametric model with sensible extrapolation and stable Greeks. Naive strike-space
interpolation with time interpolation in vol (not variance) produces calendar arbitrage.
Full debate: `docs/debates/2026-08-17-sabr-calibration-strategy.md`.

## Decision
- Non-parametric: natural cubic spline of IV vs **log-moneyness** per expiry (C²-continuous
  smiles ⇒ smooth vega/vanna), flat extrapolation beyond quoted strikes.
- Parametric: SABR (Hagan et al. 2002 lognormal formula) calibrated per expiry with **beta
  fixed** (1.0 equities, 0.5 rates) — beta and rho are not jointly identifiable from one
  smile; fixing beta removes the degeneracy.
- Calibration: multi-start (seeded stratified draws over α, ρ, ν) refined by bounded
  least-squares; keep the min-RMSE solution. Multi-start defends against the known local
  minima in the ρ–ν plane.
- Time dimension: interpolate **linearly in total variance** w = σ²T at fixed log-moneyness;
  monotone slices ⇒ calendar-arbitrage-free interpolation.
- Static no-arbitrage validated post-fit (Breeden–Litzenberger butterfly convexity, calendar
  monotonicity) in `optitrade/vol/arbitrage.py` — checked, not imposed as fit constraints
  (KISS: constrained SQP was judged not worth the complexity while validation catches the
  same defects; revisit if violations occur on real data).

## Consequences
### Positive
- Accuracy target is test-enforced: SABR round-trip RMSE < 0.3 vol-pt
  (`tests/unit/quant/test_sabr.py`); sub-microsecond vol lookups after calibration.
### Negative
- Two surface representations to maintain (spline for repricing, SABR for extrapolation).
### Risks
- Sparse wings can still fit poorly at fixed beta; RMSE is surfaced on the fit object so
  callers can fall back to the spline.
