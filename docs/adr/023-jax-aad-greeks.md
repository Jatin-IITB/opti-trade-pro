# ADR-023: JAX automatic differentiation — four Greeks methods, higher-order sensitivities

## Status
Accepted

## Context
ADR-006 shipped three cross-validated Greeks methods and identified JAX AAD as a revisit
trigger for exact higher-order Greeks and book-level vectorisation. The tape-based engine
delivers exact first-order Greeks but relies on FD-of-AD for second order (gamma, vanna,
volga) and cannot compute third-order Greeks (charm, speed, color) without further nesting
that compounds truncation error. Debate: `docs/debates/2026-08-22-jax-aad-implementation.md`.

## Decision
Add `optitrade.greeks.jax_ad` as an optional module requiring `jax[cpu]>=0.4.20` (the
`[jax]` extra in pyproject.toml). The module provides:

1. **`bs_price_jax`** — BSM price and all first+second order Greeks via nested `jax.grad`,
   same signature as `bs_price_adjoint` for drop-in cross-validation.
2. **`bs_higher_order_greeks`** — six higher-order Greeks (charm, veta, speed, color,
   ultima, zomma) via third-order nesting, unavailable from the tape engine.
3. **`bs_greeks_book_jax`** — vectorised book-level pricing via `jax.vmap`, one fused XLA
   kernel call for the entire book.

Cross-validation now covers four methods pairwise (analytic, FD, adjoint tape, JAX) in
`tests/unit/quant/test_greeks_cross.py`. The tape engine (ADR-006) remains the zero-dep
default.

## Consequences
### Positive
- All Greeks through third order are exact (no FD truncation error).
- Book-level vectorisation eliminates the Python position loop.
- The fourth method adds a pairwise check against each of the other three.

### Negative
- JAX + XLA is ~60 MB; first-call compilation takes ~2 s.
- Requires `jax_enable_x64` for float64 precision (set at module import time).

### Risks
- JAX version churn may require pinning; mitigated by treating it as optional.
