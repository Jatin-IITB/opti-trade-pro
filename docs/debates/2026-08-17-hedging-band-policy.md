# Debate: Delta-rebalancing policy — fixed threshold, fixed interval, or Whalley–Wilmott band?

- **Date**: 2026-08-17
- **Drivers**: Hedging engine design (ADR-007); report claims hedge P&L tracking ≈ theta.
- **Options**: A) Rebalance every N minutes. B) Fixed |delta| threshold. C) Whalley–Wilmott
  asymptotic no-transaction band, scaled by realized/implied vol ratio.
- **Outcome**: C → ADR-007

## Expert opinions

### Stochastic-Control Theorist (confidence 0.9)
**Assessment** — Under proportional costs k, the utility-maximising policy is a
no-transaction band around the BS delta; Whalley–Wilmott (1997) give the asymptotic
half-width H = (3/2·k·S·Γ²/λ)^(1/3). It is the *solution* to the cost-vs-tracking-error
trade-off, not a heuristic: options A and B are special cases someone then tunes by hand,
badly. Γ^(2/3) scaling means gamma-heavy books hedge tighter exactly when drift risk is
worst.
**Concerns** — Asymptotic in small k; λ must be chosen by the caller.
**Position** — C.

### Trading Practitioner (confidence 0.75)
**Assessment** — Time-based rebalancing (A) is what blows up in practice: it trades when
nothing happened and sleeps through gaps. A delta band is right; making its width respond
to Γ, S, and costs matches how desks actually size rehedges. The RV/IV modulation is the
gamma-scalping economics: long gamma earns ½ΓS²(σ_R²−σ_I²)dt, so hedge tighter when
realized vol runs hot, cheaper when it doesn't.
**Concerns** — EWMA RV estimation lag (~1/(1−0.94) ≈ 17 obs effective window) means regime
turns are caught late; acceptable at daily/hourly cadence.
**Position** — C.

### Simplicity Reviewer / KISS (confidence 0.6)
**Assessment** — B (fixed threshold) is one parameter and explainable in one sentence; C is
three parameters (k, λ, RV window) and a formula with a cube root. If the sim shows B within
noise of C, ship B.
**Concerns** — Parameter count creep; λ is unobservable.
**Position** — B unless the sim clearly favours C.

## Consensus
Option C. The WW band *contains* option B (its width is just principled instead of
hand-tuned), the formula is five lines, and the parameters are typed config with documented
economics. The hedging simulation reports tracking error vs theta so the policy's value is
measured, not asserted.

## Dissents
KISS reviewer holds that if future sims show the WW band within one standard error of a
fixed band on realistic cost regimes, the extra parameters should be removed.
