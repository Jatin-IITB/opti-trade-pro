# Debate: Adjoint AD for Greeks — build a tape from scratch, or adopt jax/autograd?

- **Date**: 2026-08-17
- **Drivers**: Report commits to "adjoint AD, all first-order Greeks in one backward pass";
  Greeks engine design (task: ADR-006).
- **Options**: A) From-scratch tape-based reverse-mode engine. B) jax dependency.
  C) Skip AD, ship analytic + finite differences only.
- **Outcome**: A → ADR-006

## Expert opinions

### Numerical-Methods Specialist (confidence 0.85)
**Assessment** — The BS pricing graph uses ~10 elementary ops (arith, log, exp, sqrt,
Φ, φ). A tape covering exactly these is ~200 lines with hand-checkable derivative rules
(Φ' = φ, φ'(x) = −xφ(x)). Reverse-mode correctness is trivially verifiable against the
closed-form Greeks we already have — a rare case where the ground truth is exact.
**Concerns** — Second-order (gamma/vanna/volga) needs forward-over-reverse or FD-of-AD;
pure reverse gives first order only.
**Position** — A, with second order via central differences of AD deltas.

### Infrastructure/Dependency Steward (confidence 0.9)
**Assessment** — jax is a ~300 MB dependency chain with platform-specific wheels (Apple
Silicon vs CI linux), XLA warm-up latency, and its own float semantics. For a library whose
core promise is "numpy/scipy only, deterministic", that is a disproportionate cost for
differentiating one closed-form model.
**Concerns** — If exotic models (Monte Carlo pricers) land later, a hand tape won't scale;
we would revisit.
**Position** — A now; explicit revisit trigger: first non-closed-form pricer.

### Performance Engineer (confidence 0.6)
**Assessment** — The scalar tape is 100–1000× slower per evaluation than the vectorised
analytic path; jax would vmap it. But the hot loop (scenario grids) uses the broadcast
analytic pricer anyway — AD's job here is exactness and model-agnostic validation, not
throughput.
**Concerns** — Someone may later put the tape in a loop over a large book; docstrings must
steer them to the vectorised path.
**Position** — A, reluctantly; B if AD ever enters the hot path.

## Consensus
Build the tape (Option A). It is small, fully owned, exactly verifiable against analytic
Greeks, and keeps the core dependency-light. Second-order Greeks via FD-of-AD. Revisit
trigger recorded: adopting any pricer without closed form.

## Dissents
Performance Engineer maintains that if AD is ever used for portfolio-scale revaluation the
decision must flip to jax rather than optimising the tape.
