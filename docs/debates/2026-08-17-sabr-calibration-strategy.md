# Debate: SABR calibration — free beta vs fixed beta; global vs multi-start local optimisation

- **Date**: 2026-08-17
- **Drivers**: Vol engine design (ADR-005); report target RMSE < 0.3 vol-pt.
- **Options**: A) Calibrate all four params (α, β, ρ, ν) with a global optimiser
  (differential evolution). B) Fix β, single-start Levenberg–Marquardt. C) Fix β,
  multi-start bounded least-squares with seeded stratified starts.
- **Outcome**: C → ADR-005

## Expert opinions

### Volatility Quant (confidence 0.9)
**Assessment** — β and ρ are not jointly identifiable from a single smile: both control
skew, and the data cannot separate backbone from correlation. Hagan's own practice and the
literature fix β by asset class (1.0 equities, 0.5 rates). With β free, day-over-day
recalibration produces parameter whiplash with identical fits — poison for hedging
stability.
**Concerns** — Fixed β=1.0 can strain fits on extreme equity skews; report RMSE on the fit
object so callers see it.
**Position** — C (β fixed).

### Optimisation Specialist (confidence 0.8)
**Assessment** — The (ρ, ν) surface has documented local minima. Single-start LM (B) lands
in them depending on the initial guess — irreproducible quality. Global optimisers (A) cost
100–1000× the evaluations for a 3-parameter problem where 8 stratified starts refined by
bounded trust-region least-squares reliably find the basin. Deterministic seeding keeps
calibration reproducible run-to-run, which a global stochastic search does not guarantee.
**Concerns** — Multi-start count is a knob; 8 is empirical, not proven.
**Position** — C.

### Latency Owner (confidence 0.7)
**Assessment** — Calibration runs per expiry per snapshot. 8 starts × ~30 LM iterations ×
9 strikes is microseconds-to-milliseconds with vectorised Hagan; differential evolution
would push whole-surface calibration toward seconds and out of the interactive budget the
report promises.
**Concerns** — None beyond keeping Hagan vectorised.
**Position** — C.

## Consensus
Option C: β fixed by asset class, seeded stratified multi-start over (α, ρ, ν), each start
refined with bounded least-squares, minimum-RMSE winner kept, RMSE surfaced on the result.
Test-enforced round-trip: noisy synthetic smiles recover < 0.3 vol-pt RMSE.

## Dissents
None — all three experts converged on C independently.
