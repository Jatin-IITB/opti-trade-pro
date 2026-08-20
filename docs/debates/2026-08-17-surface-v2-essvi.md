# Debate: Surface engine v2 — stay per-expiry SABR, go SSVI/eSSVI joint, or ML surface?

- **Date**: 2026-08-17
- **Drivers**: Flagship direction ("math with teeth"): whole-surface arbitrage-free
  calibration with a density self-check, upgrading ADR-005's per-expiry approach.
- **Options**: A) Keep per-expiry SABR + post-fit validation. B) SSVI with power-law phi
  calibrated jointly across expiries with butterfly/calendar conditions in the fit
  (eSSVI per-expiry-rho as documented extension). C) Nonparametric/ML surface
  (GP or NN) fitted to history.
- **Outcome**: B, with A retained as the benchmark → ADR-012

## Expert opinions

### Volatility Quant (confidence 0.85)
**Assessment** — Per-expiry SABR fits each smile but says nothing between expiries; the
calendar dimension is patched by interpolation, and no-arb is only *checked*, not
*imposed*. SSVI (Gatheral–Jacquier 2014) gives closed-form sufficient conditions —
θφ(θ)(1+|ρ|) ≤ 4 and θφ(θ)²(1+|ρ|) ≤ 4 — that can sit directly in the objective as
penalties, and monotone θ_t makes calendar consistency structural. That is the difference
between "we validate" and "the surface cannot be arbitrageable by construction".
**Concerns** — Global ρ is restrictive on strongly skew-term markets; full eSSVI
(per-expiry ρ, Hendriks–Martini 2019) is the escape hatch.
**Position** — B.

### Research-Risk Officer (confidence 0.8)
**Assessment** — Option C is a research project wearing an engineering hat: an ML surface
needs history we haven't accumulated (the data spine lands in the same wave), has no
no-arb guarantees without constrained architectures, and its "so what" is weak if it can't
beat carry. B is a bounded, literature-backed step with a testable exit criterion
(RMSE < 0.3 vol-pt, zero Durrleman violations).
**Concerns** — None beyond keeping C on the roadmap as phase-6 research, gated by the
backtest harness.
**Position** — B.

### Benchmark Steward (confidence 0.9)
**Assessment** — Never delete the baseline. SABR stays as the per-expiry benchmark the
joint fit must be compared against on every calibration (report both RMSEs); the RND
extraction (Breeden–Litzenberger) is the model-free self-check both must pass. A number
without a benchmark is marketing.
**Concerns** — Benchmark drift if SABR paths stop being exercised; solved by keeping its
tests and wiring both into the surface endpoint.
**Position** — B with A retained.

## Consensus
SSVI joint calibration with no-arb penalties in-fit and Durrleman + RND validation
post-fit; SABR kept as the reported benchmark; full eSSVI and ML surfaces recorded as
extensions, the latter gated behind the phase-6 research loop.

## Dissents
None material; the Volatility Quant flags that if global-ρ misfit exceeds ~0.5 vol-pt on
real NSE data, per-expiry ρ must be implemented rather than tolerated.
