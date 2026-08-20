# ADR-006: Greeks — analytic, finite-difference, and from-scratch adjoint AD, cross-validated

## Status
Accepted

## Context
One Greeks implementation cannot check itself. The report commits to finite-difference plus
adjoint AD with 500+ scenario cells revalued in <200 ms. Debate on build-vs-buy for AD:
`docs/debates/2026-08-17-adjoint-ad-vs-jax.md`.

## Decision
Three independent methods in `optitrade/greeks`:
1. Analytic Black-Scholes (`pricing.bs_greeks`) — vectorised ground truth.
2. Finite differences (`fd_greeks`) — central differences with configurable `FDBumps`;
   model-agnostic (works against any `price_fn`), the escape hatch for future non-BS models.
3. Adjoint AD (`bs_price_adjoint`) — a from-scratch tape (`Var`/`Tape`, Griewank & Walther
   2008): one forward + one backward pass yields all first-order Greeks; second order via
   central differences of AD deltas. No jax/torch dependency.

All three must agree pairwise across a moneyness × expiry × vol sweep
(`tests/unit/quant/test_greeks_cross.py`).

Scenario engine (`run_scenario_grid`): fully broadcast numpy revaluation of the whole book
across the ΔS × Δσ × Δt cube — no Python loop over scenarios. Latency target enforced by
`tests/unit/quant/test_scenario.py` (`benchmark` marker): 539 cells × 50 positions < 200 ms.

## Consequences
### Positive
- A bug in any one method is caught by the other two; the tape is ~200 lines and fully owned.
### Negative
- The AD engine covers only the ops the pricing graph needs; new models may need new ops.
### Risks
- Scalar tape is slow for large books — acceptable because the vectorised analytic path
  serves the hot loop; AD exists for exactness and model-agnostic validation.
