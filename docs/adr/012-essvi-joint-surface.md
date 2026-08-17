# ADR-012: Surface engine v2 — SSVI joint calibration with in-fit no-arb; SABR demoted to benchmark

## Status
Accepted

## Context
ADR-005's per-expiry SABR + spline validates no-arbitrage after the fact but cannot impose
it, and the time dimension is interpolation, not model. The flagship direction requires a
whole-surface, arbitrage-free calibration with a model-free self-check. Debate:
`docs/debates/2026-08-17-surface-v2-essvi.md`.

## Decision
- `optitrade/vol/essvi.py`: SSVI total variance w(k,θ_t) with power-law φ(θ)=ηθ^(−γ),
  calibrated **jointly across all expiries** (ρ, η, γ, θ_1..θ_n); θ monotonicity is
  structural (optimised as positive increments); the Gatheral–Jacquier butterfly
  sufficient conditions enter the objective as penalty residuals. Per-expiry ρ (full
  eSSVI, Hendriks–Martini 2019) is the documented extension.
- Post-fit validation adds the Durrleman condition g(k) ≥ 0 (`check_durrleman`) to the
  existing butterfly/calendar checks.
- `optitrade/vol/density.py`: Breeden–Litzenberger risk-neutral density extraction as a
  **validation gate** — pdf non-negative, integrates to ~1, implied mean ≈ forward.
- SABR (ADR-005) is retained and reported as the per-expiry benchmark on every
  calibration; both RMSEs are surfaced together.

Enforcing tests: `tests/unit/quant/test_essvi.py` (round-trip RMSE < 0.3 vol-pt, zero
Durrleman violations), `tests/unit/quant/test_density.py`.

## Consequences
### Positive
- Calendar consistency and butterfly conditions are properties of the parameterisation,
  not hopes about the data; the RND gate catches what parametrics miss.
### Negative
- Joint optimisation is a harder problem than per-slice fits; more parameters, more care.
### Risks
- Global ρ misfit on strongly skewed term structures — revisit trigger recorded in the
  debate: >0.5 vol-pt on real NSE data ⇒ implement per-expiry ρ.
