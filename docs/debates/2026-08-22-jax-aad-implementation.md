# Debate: JAX AAD — when to bring in the framework

**Date:** 2026-08-22
**Participants:** Claude (engineering AI), Jatin Gupta
**Outcome:** Proceed with JAX as an optional dependency for exact higher-order Greeks

## Question

ADR-006 shipped three independent Greeks methods (analytic, FD, tape-based adjoint) and
flagged JAX AAD as a revisit trigger. The tape engine delivers exact first-order Greeks
and uses FD-of-AD for second order. When — if ever — should we bring in JAX?

## Position A: stay with the tape

**For:** Zero external AD dependency; ~300 lines we fully own; second-order via FD-of-AD is
accurate enough (cross-validated to 1e-5). Adding JAX pulls in XLA, LLVM, and a large
transitive dependency tree.

**Against:** Third-order Greeks (charm, speed, color, ultima) are unavailable without
further FD nesting, which compounds truncation error. Book-level vectorisation requires
a Python loop over positions — no XLA fusion.

## Position B: add JAX as an optional extra

**For:** `jax.grad(jax.grad(f))` yields exact second- and third-order Greeks with zero
truncation error. `jax.vmap` eliminates the position loop — one fused XLA kernel prices
the whole book. Higher-order Greeks (charm, veta, speed, color, ultima, zomma) fall out
of nesting at no implementation cost. The tape engine stays as is; JAX is additive.

**Against:** XLA compilation latency on first call (~2 s). JAX requires `jax_enable_x64`
for quant-grade precision. Dependency size is non-trivial (~60 MB).

## Decision

Position B. JAX is an optional extra (`pip install optitrade-pro[jax]`), exactly like
the `[mcp]` and `[agentic]` extras. The tape engine remains the default and is unaffected.
Cross-validation now covers four methods pairwise.

## Evidence

- Cross-validation passes with analytic-vs-JAX tolerances of 1e-8 (first order) and 1e-6
  (second order) across the full moneyness × expiry × vol sweep.
- Six higher-order Greeks (charm, veta, speed, color, ultima, zomma) pass finiteness and
  sign checks.
- `jax.vmap` book pricing matches the scalar loop to machine precision.
